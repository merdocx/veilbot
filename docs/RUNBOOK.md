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

## 8. Утилитарные скрипты для администратора

### 8.1 Сравнение ключей (`scripts/compare_keys.py`)

**Назначение**: Диагностический инструмент для сравнения ключей в базе данных с ключами на VPN серверах.

**Использование**:
```bash
cd /root/veilbot
python3 scripts/compare_keys.py
```

**Что делает**:
- Загружает список всех активных серверов из БД
- Для каждого сервера:
  - Загружает ключи из БД
  - Загружает ключи с сервера через API (Outline/V2Ray)
  - Сравнивает и находит расхождения
- Выводит детальный отчет о расхождениях

**Типы расхождений**:
- `missing_on_server` - ключи есть в БД, но отсутствуют на сервере
- `missing_in_db` - ключи есть на сервере, но отсутствуют в БД
- `db_without_remote_id` - ключи в БД без привязки к ID на сервере

**Пример вывода**:
```
Server NL-01 (ID 1, protocol v2ray, country NL)
  DB keys: 150
  Server keys: 148
  Missing on server (2):
    - DB entry: {"id": 123, "v2ray_uuid": "...", ...}
  Missing in DB (1):
    - Remote key: {"uuid": "...", "name": "...", ...}
```

**Когда использовать**:
- После сбоев серверов для проверки целостности данных
- Перед миграцией данных
- При подозрении на расхождения между БД и серверами
- Для поиска "осиротевших" ключей

**Примечания**:
- Скрипт работает только с активными серверами (`active = 1`)
- Поддерживает оба протокола: Outline и V2Ray
- Вывод в формате JSON для удобства обработки

### 8.2 Массовая рассылка (`scripts/broadcast.py`)

**Назначение**: Инструмент для массовой рассылки сообщений всем пользователям бота.

**Использование**:
```bash
cd /root/veilbot
# Отредактировать текст сообщения в файле scripts/broadcast.py (строка 30)
python3 scripts/broadcast.py
```

**Что делает**:
- Инициализирует базу данных
- Создает экземпляр бота
- Отправляет сообщение всем пользователям через `broadcast_message()`
- Логирует процесс рассылки

**Как изменить текст сообщения**:
1. Откройте `scripts/broadcast.py`
2. Найдите переменную `message_text` (строка ~30)
3. Измените текст сообщения
4. Сохраните и запустите скрипт

**Пример использования**:
```python
# В scripts/broadcast.py
message_text = """Важное уведомление для всех пользователей.

Информация о технических работах..."""
```

**Когда использовать**:
- Технические работы и уведомления
- Важные обновления сервиса
- Информация о сбоях и восстановлении
- Промо-акции и новости

**Примечания**:
- ⚠️ **Внимание**: Сообщение отправляется ВСЕМ пользователям бота
- Скрипт использует функцию `broadcast_message()` из `bot/handlers/common.py`
- Логирование происходит в `bot.log`
- Рекомендуется тестировать на небольшой группе пользователей перед массовой рассылкой

**Безопасность**:
- Требует наличия `TELEGRAM_BOT_TOKEN` в конфигурации
- Проверяет `ADMIN_ID` перед отправкой
- Логирует все действия для аудита

---

За детальными инструкциями см. `docs/README.md` и материалы в `docs/archive/` (история проекта).

