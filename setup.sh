#!/bin/bash
set -e

echo "[1/4] Установка зависимостей..."
sudo apt update
# Пытаемся установить Python 3.11 и pip для него
sudo apt install -y python3.11 python3.11-venv python3.11-distutils python3.11-dev sqlite3 || true
# Фолбэк: если python3.11 недоступен в репозитории, сообщаем пользователю
if ! command -v python3.11 >/dev/null 2>&1; then
  echo "❌ python3.11 не найден в системе. Добавьте репозиторий с Python 3.11 или установите его вручную."
  echo "   Пример (Ubuntu 22.04+): sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install -y python3.11 python3.11-venv"
  exit 1
fi

echo "[2/4] Установка Python-библиотек..."
python3.11 -m pip install --upgrade pip
python3.11 -m pip install -r requirements.txt

echo "[3/4] Настройка systemd службы..."
# Обновляем ExecStart под python3.11
sudo sed -i 's|^ExecStart=/usr/bin/python3 |ExecStart=/usr/bin/python3.11 |' veilbot.service
sudo sed -i 's|^ExecStart=/usr/bin/python3 -m uvicorn|ExecStart=/usr/bin/python3.11 -m uvicorn|' veilbot-admin.service || true
sudo cp veilbot.service /etc/systemd/system/veilbot.service
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable veilbot
sudo systemctl restart veilbot

echo "[4/4] Готово. Бот VeilBot запущен!"
