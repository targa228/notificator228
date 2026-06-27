#!/usr/bin/env python3
"""OLX.ro -> Telegram new-listing notifier.

Polls a set of OLX search queries, detects *genuinely new* listings and pushes a
compact Telegram message (title + price + link, with Telegram's own link-preview
card) for each one.

Secrets (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) are read from the environment
(.env locally, GitHub Actions Secrets in the cloud). They are NEVER written to
disk, logged, or committed.
"""
import argparse
import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Make stdout UTF-8 so emoji/diacritics print on Windows consoles (cp1250 etc.).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
FILTERS_FILE = BASE_DIR / "filters.json"
STATE_FILE = BASE_DIR / "state.json"

OLX_API = "https://www.olx.ro/api/v1/offers/"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# How many results to pull per query each poll. Newest listings appear near the
# top, so 50 comfortably covers a 5-15 min polling interval.
PAGE_LIMIT = int(os.getenv("OLX_PAGE_LIMIT", "50"))
# Safety cap: never fire more than this many notifications per query per run.
MAX_NOTIFY_PER_FILTER = int(os.getenv("MAX_NOTIFY_PER_FILTER", "15"))
# Skip paid/promoted ("promovat") listings. Set to "false" to include them.
IGNORE_PROMOTED = os.getenv("IGNORE_PROMOTED", "true").lower() not in ("0", "false", "no")
# Keep at most this many seen IDs per filter (bounds state.json size).
SEEN_CAP = 800

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
    "Referer": "https://www.olx.ro/",
}


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}Z] {msg}", flush=True)


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"WARN: could not parse {path.name}: {e}")
    return default


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fetch_offers(query, limit=PAGE_LIMIT, attempts=3):
    params = {
        "offset": 0,
        "limit": limit,
        "query": query,
        "sort_by": "created_at:desc",
    }
    last_err = None
    for i in range(attempts):
        try:
            r = requests.get(OLX_API, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json().get("data", [])
        except Exception as e:
            last_err = e
            time.sleep(2 * (i + 1))
    raise last_err


def is_promoted(offer):
    """True for paid/promoted listings (OLX 'promovat': top, highlighted, bundled)."""
    p = offer.get("promotion") or {}
    return bool(p.get("top_ad") or p.get("highlighted") or p.get("options"))


def price_label(offer):
    for p in offer.get("params", []):
        if p.get("key") == "price" or p.get("type") == "price":
            val = p.get("value")
            if isinstance(val, dict):
                return val.get("label") or "fără preț"
            if val:
                return str(val)
    return "fără preț"


def parse_time(raw):
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def format_message(filter_name, offer):
    tag = filter_name.upper().replace(" ", "_")
    title = html.escape(offer.get("title", "(fără titlu)"))
    price = html.escape(price_label(offer))
    link = offer.get("url", "")
    return f"\U0001F195 <b>[{tag}]</b> {title} — {price}\n{link}"


def send_telegram(token, chat_id, text):
    url = TELEGRAM_API.format(token=token, method="sendMessage")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = requests.post(url, data=payload, timeout=30)
    if r.status_code == 429:
        retry = 5
        try:
            retry = r.json().get("parameters", {}).get("retry_after", 5)
        except Exception:
            pass
        log(f"Telegram rate limit, sleeping {retry}s")
        time.sleep(retry + 1)
        r = requests.post(url, data=payload, timeout=30)
    return r


def run_once():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in environment.")
        sys.exit(1)

    filters = load_json(FILTERS_FILE, [])
    if not filters:
        log("ERROR: filters.json is empty or missing.")
        sys.exit(1)

    state = load_json(STATE_FILE, {})
    state.setdefault("seen", {})
    first_ever = "monitor_start" not in state
    if first_ever:
        state["monitor_start"] = datetime.now(timezone.utc).isoformat()
        log("First run: seeding seen-IDs only, no notifications will be sent.")
    cutoff = parse_time(state["monitor_start"]) or datetime.now(timezone.utc)

    total_new = 0
    changed = False

    for f in filters:
        name = f["name"]
        query = f["query"]
        tag = name.upper().replace(" ", "_")
        seen_list = state["seen"].get(name, [])
        seen = set(seen_list)

        try:
            offers = fetch_offers(query)
        except Exception as e:
            log(f"[{tag}] fetch failed: {e}")
            continue

        fresh = []
        skipped_promoted = 0
        for o in offers:
            oid = o.get("id")
            if oid is None or oid in seen:
                continue
            seen.add(oid)
            seen_list.append(oid)
            changed = True
            # Skip paid/promoted ("promovat") listings entirely.
            if IGNORE_PROMOTED and is_promoted(o):
                skipped_promoted += 1
                continue
            ct = parse_time(o.get("created_time", ""))
            # Notify only listings created AFTER monitoring started. This skips
            # old listings that merely got bumped back to the top.
            if not first_ever and ct is not None and ct >= cutoff:
                fresh.append((ct, o))

        fresh.sort(key=lambda x: x[0])  # chronological order
        fresh = fresh[-MAX_NOTIFY_PER_FILTER:]

        for ct, o in fresh:
            text = format_message(name, o)
            try:
                resp = send_telegram(token, chat_id, text)
            except Exception as e:
                log(f"[{tag}] Telegram send exception: {type(e).__name__}")
                continue
            if resp.ok:
                log(f"[{tag}] sent #{o.get('id')}: {o.get('title','')[:55]}")
                total_new += 1
            else:
                log(f"[{tag}] Telegram error {resp.status_code}: {resp.text[:200]}")
            time.sleep(1.2)

        state["seen"][name] = seen_list[-SEEN_CAP:]
        log(f"[{tag}] checked {len(offers)} offers, {len(fresh)} new, "
            f"{skipped_promoted} promoted skipped.")

    if changed:
        save_state(state)
    log(f"Done. Notifications sent this run: {total_new}")
    return total_new


def run_test():
    """Validate both pipelines without touching state: print formatted messages
    for the top results of the first filter, and send ONE real test message."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")
        sys.exit(1)

    filters = load_json(FILTERS_FILE, [])
    f = filters[0]
    log(f"TEST: fetching OLX query '{f['query']}' ...")
    offers = fetch_offers(f["query"], limit=5)
    log(f"TEST: OLX returned {len(offers)} offers. Preview of formatted messages:")
    for o in offers[:3]:
        print("-" * 60)
        print(format_message(f["name"], o))
    print("-" * 60)

    sample = offers[0] if offers else None
    if sample:
        text = "✅ Test OLX-bot OK\n" + format_message(f["name"], sample)
    else:
        text = "✅ Test OLX-bot OK (no offers fetched)"
    log("TEST: sending one message to Telegram ...")
    try:
        resp = send_telegram(token, chat_id, text)
        if resp.ok:
            log("TEST: Telegram OK — message delivered. Token & chat_id work.")
        else:
            log(f"TEST: Telegram returned {resp.status_code}: {resp.text[:200]}")
            log("      (401/404 = token invalid/expired; replace it and re-run.)")
    except Exception as e:
        log(f"TEST: Telegram send exception: {type(e).__name__}")


def main():
    ap = argparse.ArgumentParser(description="OLX.ro -> Telegram new-listing notifier")
    ap.add_argument("--test", action="store_true",
                    help="Fetch a sample, print formatted messages, send one test message.")
    ap.add_argument("--loop", type=int, metavar="SECONDS", default=0,
                    help="Run forever, polling every N seconds (for a VPS/always-on host).")
    args = ap.parse_args()

    if args.test:
        run_test()
        return
    if args.loop > 0:
        log(f"Loop mode: polling every {args.loop}s (precise). Ctrl+C to stop.")
        while True:
            start = time.monotonic()
            try:
                run_once()
            except Exception as e:
                log(f"Run error: {type(e).__name__}: {e}")
            # Sleep so each cycle starts ~every args.loop seconds, regardless of
            # how long the poll itself took (keeps the interval precise).
            elapsed = time.monotonic() - start
            time.sleep(max(5, args.loop - elapsed))
    else:
        run_once()


if __name__ == "__main__":
    main()
