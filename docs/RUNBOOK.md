# Руководство по эксплуатации VeilBot

Это краткий регламент для дежурного инженера/администратора.

## 1. Службы

| Компонент | systemd unit |
|-----------|--------------|
| Telegram-бот | `veilbot.service` |
| Админка (FastAPI) | `veilbot-admin.service` |

```bash
# запуск/остановка
sudo systemctl restart veilbot.service veilbot-admin.service

# tail журналов (перенаправляется в journald)
sudo journalctl -u veilbot.service -f
sudo journalctl -u veilbot-admin.service -f
```

## 2. Логи

Все рабочие логи находятся в `/var/log/veilbot/`:

- `bot.log` – основные события бота
- `admin_audit.log` – действия в админке
- `veilbot_security.log` – события безопасности

Ротация выполняется скриптом `scripts/rotate_logs.sh` и сохраняет архивы в `/var/log/veilbot/archive/`.

## 3. Резервное копирование БД

Скрипт `backup_db.sh` создаёт резервную копию SQLite через `sqlite3 .backup` и выполняет `VACUUM`.

Стандартные директория и расписание:

- каталоги бэкапов: `/var/backups/veilbot`
- systemd unit и таймер: `setup/veilbot-db-backup.service`, `setup/veilbot-db-backup.timer`

Установка таймера:

```bash
sudo cp setup/veilbot-db-backup.service /etc/systemd/system/
sudo cp setup/veilbot-db-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now veilbot-db-backup.timer
```

Проверка последнего бэкапа:

```bash
ls -lh /var/backups/veilbot/
```

## 4. Health-check

- HTTP: `GET http://127.0.0.1:8001/healthz`
- Скрипт проверки: `scripts/health_check.sh`

Пример использования:

```bash
/root/veilbot/scripts/health_check.sh
```

## 5. CI / Автотесты

GitHub Actions workflow `.github/workflows/ci.yml` запускает:

- `npm run lint` — линтеры фронтенда
- `pytest` — Python-тесты

Пуш в `main` или PR автоматически запускают проверки.

## 6. Экстренные действия

1. **Проблемы с БД** — остановить сервисы, восстановить последнюю копию из `/var/backups/veilbot`, затем `sqlite3 vpn.db "PRAGMA integrity_check;"`.
2. **Критичный отказ бота** — `sudo systemctl restart veilbot.service`; если не помогает, проверить `bot.log`.
3. **Админка недоступна** — `sudo systemctl restart veilbot-admin.service` и проверить `/var/log/veilbot/admin_audit.log`.

## 7. Полезные команды

```bash
# Сбор статистики ключей
sqlite3 /root/veilbot/vpn.db "SELECT COUNT(*) FROM keys WHERE expiry_at > strftime('%s','now');"

# Очистка архивов логов и старых бэкапов
/root/veilbot/scripts/cleanup.sh

# Ручная ротация логов
/root/veilbot/scripts/rotate_logs.sh
```

---

За детальными инструкциями см. `docs/README.md` и материалы в `docs/archive/` (история проекта).

