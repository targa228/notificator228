#!/usr/bin/env bash
# Adauga botul de Vinted pe acelasi server (al doilea systemd timer, la 2 min).
# Refoloseste mediul Python (.venv) si secretele (.env) de la botul OLX.
# Ruleaza din folderul proiectului:  bash setup_vinted.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$APP_DIR/.venv"
PYBIN="$VENV/bin/python"
RUN_USER="$(whoami)"

if [ ! -x "$PYBIN" ]; then
  echo "EROARE: nu gasesc $VENV — ruleaza intai setup_oracle.sh (botul OLX)."
  exit 1
fi

echo "==> Verific dependentele..."
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# Seeding curat pentru Vinted.
rm -f "$APP_DIR/state_vinted.json"

echo "==> Instalez serviciul + timer-ul systemd pentru Vinted (la 2 min)..."
sudo tee /etc/systemd/system/vinted-bot.service >/dev/null <<EOF
[Unit]
Description=Vinted -> Telegram notifier (single run)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$PYBIN $APP_DIR/vinted_bot.py
EOF

sudo tee /etc/systemd/system/vinted-bot.timer >/dev/null <<EOF
[Unit]
Description=Ruleaza notificatorul Vinted la fiecare 2 minute

[Timer]
OnBootSec=1min
OnCalendar=*:0/2
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now vinted-bot.timer

echo "==> Rulez o data acum (seeding silentios)..."
sudo systemctl start vinted-bot.service || true

echo ""
echo "=================================================="
echo " GATA! Botul Vinted ruleaza singur, la 2 minute."
echo "=================================================="
systemctl list-timers vinted-bot.timer --no-pager || true
echo ""
echo "Loguri Vinted:  journalctl -u vinted-bot.service -n 50 --no-pager"
