# OLX → Telegram notifier

Monitorizează niște căutări de pe **OLX.ro** și trimite o notificare compactă pe
**Telegram** pentru fiecare anunț **nou** (titlu + preț + link cu preview), exact
ca în exemplul cerut: `🆕 [CAMERA_FOTO] obiectiv Sony E 18-55mm — fără preț`.

Rulează **în cloud, gratuit, fără să-ți ții calculatorul pornit**, prin GitHub
Actions (un „robot" programat care pornește singur la fiecare ~10 minute).

---

## Cum funcționează (pe scurt)

1. La fiecare rulare, scriptul interoghează API-ul public OLX
   (`https://www.olx.ro/api/v1/offers/`) pentru fiecare căutare din
   [`filters.json`](filters.json), sortat după dată.
2. Compară anunțurile cu ce a „văzut" deja (`state.json`) și trimite pe Telegram
   doar pe cele **create după** momentul în care ai pornit monitorizarea.
   → Astfel **nu primești spam** cu anunțuri vechi care urcă în listă doar pentru
   că au fost „bump-uite"/promovate.
3. Starea (`state.json`) e salvată înapoi în repo, ca să-și amintească între rulări.

Secretele (token + chat id) stau în **GitHub Secrets** (criptate), niciodată în cod.

---

## Configurare în cloud (GitHub Actions) — recomandat

1. **Cont GitHub** (gratuit) → creează un repo nou, de ex. `olx-notifier`
   (privat sau public; vezi nota despre minute mai jos).
2. Urcă **toate fișierele din acest folder** în repo (fără `.env` — e exclus
   automat prin `.gitignore`).
3. În repo: **Settings → Secrets and variables → Actions → New repository secret**
   și adaugă două secrete:
   - `TELEGRAM_BOT_TOKEN` = tokenul de la @BotFather
   - `TELEGRAM_CHAT_ID`   = id-ul tău numeric (ex. `679733568`)
4. Mergi în tab-ul **Actions**, activează workflow-urile, și apasă
   **Run workflow** o dată (prima rulare doar „învață" anunțurile existente).
5. Gata — de acum pornește singur la ~10 minute.

### Repo privat vs public (minute gratuite)
- **Public**: minute de Actions **nelimitate** și gratuite → poți polui mai des
  (ex. la 5 min). Codul e vizibil, dar **nu conține niciun secret**.
- **Privat**: 2000 minute/lună gratuite. La 10 minute interval te încadrezi lejer.

GitHub poate întârzia rulările programate când are trafic mare, deci tratează
intervalul ca „aproximativ la 10–15 minute".

---

## Test / rulare locală (opțional)

```bash
pip install -r requirements.txt
cp .env.example .env      # apoi pune token + chat_id în .env
python olx_bot.py --test  # trimite un mesaj de test + arată cum arată notificările
python olx_bot.py         # o rulare normală (prima = doar seeding)
python olx_bot.py --loop 600   # rulează la nesfârșit, la 600s (pt. un VPS pornit non-stop)
```

`.env` este în `.gitignore` și **nu trebuie urcat niciodată** în GitHub.

---

## Filtrele tale

Le editezi în [`filters.json`](filters.json). Fiecare are:
- `name` — eticheta din mesaj (`camera foto` → `[CAMERA_FOTO]`)
- `query` — termenul căutat pe OLX
- `source` — link-ul original (doar pentru referință)

Ca să adaugi/modifici o căutare, schimbi `query`. Pentru a reseta complet
memoria (să „uite" tot și să o ia de la zero), șterge `state.json`.

---

## Probleme posibile și soluții

| Problemă | Cauză | Soluție |
|---|---|---|
| Nu vin notificări | Prima rulare e doar seeding | Normal — vin de la al doilea ciclu, când apar anunțuri noi |
| `Telegram error 401/404` | Token greșit/expirat | Generează altul la @BotFather și actualizează secretul |
| Anunțuri vechi „noi" | Anunț bump-uit/promovat | Deja filtrat după data creării; nu ar trebui să apară |
| Prea multe/puține rezultate | Termen prea larg/îngust | Ajustează `query` în `filters.json` |
| OLX blochează (403) din cloud | Rar, IP de datacenter | Scriptul reîncearcă; dacă persistă, rulează pe un VPS mic |

---

## Alternative de hosting
- **GitHub Actions** (folosit aici) — gratuit, zero întreținere. ✅
- **VPS mic / Oracle Cloud Free Tier / Fly.io** — pentru interval exact și fără
  întârzieri; rulezi `python olx_bot.py --loop 600`.

> Securitate: tokenul și chat id-ul stau doar în `.env` local (exclus din git) și
> în GitHub Secrets (criptate). Nu sunt scrise în cod, în loguri sau în `state.json`.
