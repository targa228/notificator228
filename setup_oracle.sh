#!/usr/bin/env bash
# Instalare automata a botului OLX -> Telegram pe un server Linux
# (ex. Oracle Cloud Free Tier, Ubuntu). Verificare EXACTA la 5 minute,
# prin systemd timer (aliniat la ceas: :00, :05, :10 ...).
#
# Folosire (dupa ce ai dat git clone si esti in folderul proiectului):
#   bash setup_oracle.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$APP_DIR/.venv"
PYBIN="$VENV/bin/python"
RUN_USER="$(whoami)"

echo "==> 1/5 Instalez pachetele de sistem (python, pip, venv)..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip

echo "==> 2/5 Creez mediul Python si instalez dependentele..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# Seeding curat pe acest server (ignora starea mostenita din repo).
rm -f "$APP_DIR/state.json"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "==> 3/5 Configurare secrete (raman DOAR pe acest server, in .env):"
  read -rp "    Lipeste TELEGRAM_BOT_TOKEN: " TK
  read -rp "    Lipeste TELEGRAM_CHAT_ID:  " CID
  printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n' "$TK" "$CID" > "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
else
  echo "==> 3/5 .env exista deja, il pastrez."
fi

echo "==> 4/5 Instalez serviciul + timer-ul systemd..."
sudo tee /etc/systemd/system/olx-bot.service >/dev/null <<EOF
[Unit]
Description=OLX -> Telegram notifier (single run)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$PYBIN $APP_DIR/olx_bot.py
EOF

sudo tee /etc/systemd/system/olx-bot.timer >/dev/null <<EOF
[Unit]
Description=Ruleaza notificatorul OLX la fiecare 5 minute

[Timer]
OnBootSec=1min
OnCalendar=*:0/5
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now olx-bot.timer

echo "==> 5/5 Rulez o data acum (prima rulare = seeding silentios)..."
sudo systemctl start olx-bot.service || true

echo ""
echo "=================================================="
echo " GATA! Botul ruleaza singur, exact la 5 minute."
echo "=================================================="
systemctl list-timers olx-bot.timer --no-pager || true
echo ""
echo "Comenzi utile:"
echo "  Vezi logurile:    journalctl -u olx-bot.service -n 50 --no-pager"
echo "  Test pe loc:      $PYBIN $APP_DIR/olx_bot.py --test"
echo "  Status timer:     systemctl list-timers olx-bot.timer"
echo "  Oprire de tot:    sudo systemctl disable --now olx-bot.timer"
