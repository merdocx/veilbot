# VeilBot - Telegram VPN Bot

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111.0+-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Telegram-бот для автоматической продажи доступа к Outline VPN серверам с интеграцией платежной системы YooKassa и веб-админкой.

## 🚀 Возможности

- **Автоматическая продажа VPN** через Telegram бота
- **Интеграция с YooKassa** для приема платежей
- **Веб-админка** для управления серверами, тарифами и пользователями
- **Outline VPN** интеграция для создания и управления ключами
- **Система тарифов** с гибкими настройками
- **Автоматическое продление** SSL сертификатов
- **Безопасность** с rate limiting, шифрованием БД и security headers

## 📋 Требования

- Python 3.8+
- SQLite3
- Nginx
- Outline VPN серверы
- YooKassa аккаунт
- Telegram Bot Token

## 🛠 Установка

### 1. Клонирование репозитория

```bash
git clone https://github.com/merdocx/veilbot.git
cd veilbot
```

### 2. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 3. Настройка переменных окружения

Создайте файл `.env` на основе `.env.example`:

```bash
cp .env.example .env
```

Заполните необходимые переменные:

```env
# Bot Configuration
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
YOOKASSA_SHOP_ID=your_shop_id
YOOKASSA_API_KEY=your_api_key
YOOKASSA_RETURN_URL=https://t.me/your_bot_username

# Admin Panel Security
SECRET_KEY=your_secret_key
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=bcrypt_hash_of_password

# Database
DATABASE_PATH=/path/to/vpn.db
DB_ENCRYPTION_KEY=your_encryption_key

# Session Configuration
SESSION_MAX_AGE=3600
SESSION_SECURE=True

# Rate Limiting
RATE_LIMIT_LOGIN=5/minute
RATE_LIMIT_API=100/minute
```

### 4. Инициализация базы данных

```bash
python3 setup_security.py
```

### 5. Настройка сервисов

```bash
# Установка systemd сервисов
sudo cp veilbot.service /etc/systemd/system/
sudo cp veilbot-admin.service /etc/systemd/system/

# Перезагрузка systemd
sudo systemctl daemon-reload

# Включение автозапуска
sudo systemctl enable veilbot.service
sudo systemctl enable veilbot-admin.service
```

### 6. Настройка Nginx (для HTTPS)

```bash
# Установка Nginx и Certbot
sudo apt install nginx certbot python3-certbot-nginx

# Копирование конфигурации
sudo cp nginx/veil-bot.ru /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/veil-bot.ru /etc/nginx/sites-enabled/

# Получение SSL сертификата
sudo certbot --nginx -d your-domain.com --non-interactive --agree-tos
```

## 🚀 Запуск

### Запуск всех сервисов

```bash
./manage_services.sh start
```

### Проверка статуса

```bash
./manage_services.sh status
```

### Просмотр логов

```bash
./manage_services.sh logs        # Логи бота
./manage_services.sh admin-logs  # Логи админки
```

## 📊 Структура проекта

```
veilbot/
├── bot.py                 # Основной Telegram бот
├── admin/                 # Веб-админка
│   ├── main.py           # FastAPI приложение
│   ├── admin_routes.py   # API маршруты
│   ├── templates/        # HTML шаблоны
│   └── static/           # Статические файлы
├── db.py                 # Работа с базой данных
├── payment.py            # Интеграция с YooKassa
├── outline.py            # Интеграция с Outline VPN
├── config.py             # Конфигурация
├── requirements.txt      # Python зависимости
├── manage_services.sh    # Управление сервисами
├── backup_db.sh          # Скрипт бэкапа
└── docs/                 # Документация
```

## 🔧 Конфигурация

### Тарифы

Тарифы настраиваются в базе данных:

```sql
INSERT INTO tariffs (name, duration_sec, traffic_limit_mb, price_rub) 
VALUES ('1 месяц', 2592000, 1000000, 200);
```

### Серверы Outline

Добавьте Outline серверы в базу:

```sql
INSERT INTO servers (name, api_url, cert_sha256, country, active) 
VALUES ('Server RU', 'https://server.com:port/api', 'cert_hash', 'RU', 1);
```

## 🔒 Безопасность

- Все пароли хешируются с помощью bcrypt
- База данных шифруется
- Rate limiting для защиты от брутфорса
- Security headers в админке
- HTTPS обязателен для админки
- Автоматическое обновление SSL сертификатов

## 📈 Мониторинг

### Логи

- Логи бота: `journalctl -u veilbot.service`
- Логи админки: `journalctl -u veilbot-admin.service`
- Логи Nginx: `/var/log/nginx/`

### Бэкапы

Автоматические бэкапы базы данных:

```bash
./backup_db.sh
```

## 🤝 Вклад в проект

1. Fork репозитория
2. Создайте ветку для новой функции (`git checkout -b feature/amazing-feature`)
3. Commit изменения (`git commit -m 'Add amazing feature'`)
4. Push в ветку (`git push origin feature/amazing-feature`)
5. Откройте Pull Request

## 📄 Лицензия

Этот проект лицензирован под MIT License - см. файл [LICENSE](LICENSE) для деталей.

## 🆘 Поддержка

Если у вас есть вопросы или проблемы:

1. Проверьте [Issues](https://github.com/merdocx/veilbot/issues)
2. Создайте новый Issue с подробным описанием проблемы
3. Приложите логи и конфигурацию (без секретных данных)

## 🔄 Обновления

Для обновления проекта:

```bash
git pull origin main
pip install -r requirements.txt
./manage_services.sh restart
```

---

**Внимание:** Этот проект предназначен для легального использования VPN сервисов. Убедитесь, что вы соблюдаете местные законы и правила.