#!/usr/bin/env python3
"""Vinted.ro -> Telegram new-listing notifier.

Same idea as olx_bot.py but for Vinted. Vinted's catalog API needs a session:
we GET the homepage to obtain an anonymous `access_token_web` cookie, then call
the API with it as a Bearer token. A fresh session is created each run, so token
expiry is never an issue.

New-listing detection uses a per-filter high-water-mark on the (incrementing)
item id: we only notify items with id greater than the newest one seen when we
started — this skips old items that merely got bumped/promoted to the top.

Secrets (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) come from the environment / .env,
shared with the OLX bot. Photos are sent with sendPhoto so the image always shows.
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
FILTERS_FILE = BASE_DIR / "filters_vinted.json"
STATE_FILE = BASE_DIR / "state_vinted.json"

VINTED_BASE = "https://www.vinted.ro"
CATALOG_API = VINTED_BASE + "/api/v2/catalog/items"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

PAGE_LIMIT = int(os.getenv("VINTED_PAGE_LIMIT", "20"))
MAX_NOTIFY_PER_FILTER = int(os.getenv("MAX_NOTIFY_PER_FILTER", "15"))
IGNORE_PROMOTED = os.getenv("IGNORE_PROMOTED", "true").lower() not in ("0", "false", "no")
SEEN_CAP = 800

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


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
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def make_session():
    """Fresh Vinted session: homepage GET -> anonymous token -> Bearer header."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
    })
    s.get(VINTED_BASE + "/", timeout=30)
    tok = s.cookies.get("access_token_web")
    if not tok:
        raise RuntimeError("nu am putut obtine tokenul de sesiune Vinted")
    s.headers["Authorization"] = f"Bearer {tok}"
    return s


def fetch_items(session, params):
    p = dict(params)
    p.setdefault("order", "newest_first")
    p["per_page"] = PAGE_LIMIT
    r = session.get(CATALOG_API, params=p, timeout=30)
    if r.status_code == 401:
        raise PermissionError("token expirat")
    r.raise_for_status()
    return r.json().get("items", [])


def fmt_price(item):
    price = item.get("price") or {}
    amount = price.get("amount")
    cur = price.get("currency_code", "")
    if amount is None:
        return "fără preț"
    try:
        f = float(amount)
        amount = str(int(f)) if f.is_integer() else f"{f:.2f}"
    except Exception:
        pass
    return f"{amount} {cur}".strip()


def item_url(item):
    return item.get("url") or (VINTED_BASE + item.get("path", ""))


def item_photo(item):
    ph = item.get("photo") or {}
    if ph.get("url"):
        return ph["url"]
    photos = item.get("photos") or []
    if photos and photos[0].get("url"):
        return photos[0]["url"]
    return None


def format_caption(filter_name, item):
    tag = filter_name.upper().replace(" ", "_")
    title = html.escape(item.get("title", "(fără titlu)"))
    brand = item.get("brand_title")
    price = html.escape(fmt_price(item))
    extra = f" · {html.escape(brand)}" if brand else ""
    return f"\U0001F195 <b>[VINTED · {tag}]</b> {title}{extra} — {price}\n{item_url(item)}"


def send_photo(token, chat_id, photo, caption):
    url = TELEGRAM_API.format(token=token, method="sendPhoto")
    payload = {"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML"}
    r = requests.post(url, data=payload, timeout=30)
    if r.status_code == 429:
        retry = 5
        try:
            retry = r.json().get("parameters", {}).get("retry_after", 5)
        except Exception:
            pass
        time.sleep(retry + 1)
        r = requests.post(url, data=payload, timeout=30)
    if not r.ok and photo:
        # Fallback to a plain text message if the photo could not be sent.
        msg_url = TELEGRAM_API.format(token=token, method="sendMessage")
        r = requests.post(msg_url, data={
            "chat_id": chat_id, "text": caption, "parse_mode": "HTML",
            "disable_web_page_preview": False}, timeout=30)
    return r


def run_once():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")
        sys.exit(1)

    filters = load_json(FILTERS_FILE, [])
    if not filters:
        log("ERROR: filters_vinted.json is empty or missing.")
        sys.exit(1)

    state = load_json(STATE_FILE, {})
    state.setdefault("filters", {})

    try:
        session = make_session()
    except Exception as e:
        log(f"Vinted session failed: {e}")
        sys.exit(1)

    total_new = 0
    changed = False

    for f in filters:
        name = f["name"]
        tag = name.upper().replace(" ", "_")
        fstate = state["filters"].setdefault(name, {})
        seen = set(fstate.get("seen", []))
        high_water = fstate.get("high_water")

        try:
            items = fetch_items(session, f["params"])
        except PermissionError:
            session = make_session()  # refresh once and retry
            items = fetch_items(session, f["params"])
        except Exception as e:
            log(f"[{tag}] fetch failed: {e}")
            continue

        ids = [int(it["id"]) for it in items if it.get("id") is not None]
        first_ever = high_water is None

        fresh = []
        if not first_ever:
            for it in items:
                iid = it.get("id")
                if iid is None:
                    continue
                iid = int(iid)
                if iid <= high_water or iid in seen:
                    continue
                if IGNORE_PROMOTED and it.get("promoted"):
                    continue
                fresh.append(it)
            fresh.sort(key=lambda x: int(x["id"]))  # oldest-new first
            fresh = fresh[-MAX_NOTIFY_PER_FILTER:]

        for it in fresh:
            cap = format_caption(name, it)
            try:
                resp = send_photo(token, chat_id, item_photo(it), cap)
            except Exception as e:
                log(f"[{tag}] Telegram exception: {type(e).__name__}")
                continue
            if resp.ok:
                log(f"[{tag}] sent #{it.get('id')}: {it.get('title','')[:50]}")
                total_new += 1
            else:
                log(f"[{tag}] Telegram error {resp.status_code}: {resp.text[:150]}")
            time.sleep(1.2)

        # Update state.
        seen.update(ids)
        fstate["seen"] = list(seen)[-SEEN_CAP:]
        if ids:
            fstate["high_water"] = max([high_water or 0] + ids)
        changed = True
        log(f"[{tag}] {len(items)} items, {len(fresh)} new"
            + (" (seeding)" if first_ever else "") + ".")

    if changed:
        save_state(state)
    log(f"Done. Notifications sent this run: {total_new}")
    return total_new


def run_test():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    filters = load_json(FILTERS_FILE, [])
    f = filters[0]
    log(f"TEST: Vinted filter '{f['name']}' ...")
    session = make_session()
    items = fetch_items(session, f["params"])
    log(f"TEST: got {len(items)} items. Preview:")
    for it in items[:3]:
        print("-" * 60)
        print(format_caption(f["name"], it))
    print("-" * 60)
    if items:
        it = items[0]
        cap = "✅ Test Vinted-bot OK\n" + format_caption(f["name"], it)
        resp = send_photo(token, chat_id, item_photo(it), cap)
        if resp.ok:
            log("TEST: Telegram OK — message delivered.")
        else:
            log(f"TEST: Telegram {resp.status_code}: {resp.text[:150]}")


def main():
    ap = argparse.ArgumentParser(description="Vinted.ro -> Telegram notifier")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SECONDS", default=0)
    args = ap.parse_args()
    if args.test:
        run_test()
    elif args.loop > 0:
        log(f"Loop mode: every {args.loop}s.")
        while True:
            start = time.monotonic()
            try:
                run_once()
            except Exception as e:
                log(f"Run error: {type(e).__name__}: {e}")
            time.sleep(max(5, args.loop - (time.monotonic() - start)))
    else:
        run_once()


if __name__ == "__main__":
    main()
