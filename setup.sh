#!/bin/bash
set -e

echo "[1/4] Установка зависимостей..."
sudo apt update
sudo apt install -y python3 python3-pip sqlite3

echo "[2/4] Установка Python-библиотек..."
pip3 install aiogram yookassa requests

echo "[3/4] Настройка systemd службы..."
sudo cp veilbot.service /etc/systemd/system/veilbot.service
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable veilbot
sudo systemctl restart veilbot

echo "[4/4] Готово. Бот VeilBot запущен!"
