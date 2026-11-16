#!/bin/bash
# Установка бота «Списки»: bash /opt/spiski/setup.sh
set -e
APP=/opt/spiski
DATA=/var/lib/spiski

echo "== Ставлю зависимости Python =="
export DEBIAN_FRONTEND=noninteractive
command -v python3 >/dev/null && python3 -m venv --help >/dev/null 2>&1 || { apt-get update -q; apt-get install -y -q python3-venv; }
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"
mkdir -p "$DATA"

if [ ! -f "$APP/.env" ]; then
  echo
  read -rsp "Вставь токен бота от BotFather (символы не видны) и нажми Enter: " TOKEN; echo
  # Ваши с женой ID берём у бюджетного бота, чтобы не вводить заново
  IDS=$(grep -h '^ALLOWED_IDS=' /opt/budget-bot/.env 2>/dev/null | head -1 | cut -d= -f2)
  printf 'BOT_TOKEN=%s\nALLOWED_IDS=%s\nDB_PATH=%s/spiski.db\n' "$TOKEN" "$IDS" "$DATA" > "$APP/.env"
  chmod 600 "$APP/.env"
  [ -n "$IDS" ] && echo "Доступ взял у бюджетного бота: $IDS"
fi

cat > /etc/systemd/system/spiski.service <<EOF
[Unit]
Description=Telegram bot: shopping and to-do lists
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=$APP/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now spiski
systemctl restart spiski

# Пусть сторож следит и за этим ботом
WD=/opt/watchdog/.env
if [ -f "$WD" ] && ! grep -q '^SERVICES=.*\bspiski\b' "$WD"; then
  sed -i 's/^SERVICES=\(.*\)$/SERVICES=\1 spiski/' "$WD"
  git -C /opt/watchdog pull -q 2>/dev/null || true
  systemctl restart watchdog-bot 2>/dev/null || true
  echo "Сторож теперь следит и за «Списками»"
fi

sleep 3
systemctl is-active spiski
echo
echo "Готово. Вы с женой открываете бота и нажимаете Start."
echo "Если бот ответит «Это частный бот», выполни: bash $APP/allow.sh ID_из_ответа"
