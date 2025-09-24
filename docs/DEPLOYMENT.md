# Руководство по развертыванию VeilBot

## Подготовка сервера

### Системные требования
- Ubuntu 20.04+ или Debian 11+
- Python 3.8+
- 2GB RAM минимум
- 10GB свободного места
- Статический IP адрес

### Обновление системы
```bash
sudo apt update && sudo apt upgrade -y
```

## Установка зависимостей

### Python и pip
```bash
sudo apt install python3 python3-pip python3-venv -y
```

### Nginx и Certbot
```bash
sudo apt install nginx certbot python3-certbot-nginx -y
```

### Дополнительные пакеты
```bash
sudo apt install sqlite3 git curl -y
```

## Настройка проекта

### 1. Клонирование репозитория
```bash
git clone https://github.com/merdocx/veilbot.git
cd veilbot
```

### 2. Создание виртуального окружения
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Настройка переменных окружения
```bash
cp .env.example .env
nano .env
```

### 4. Генерация секретных ключей
```bash
# Генерация SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Генерация DB_ENCRYPTION_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Генерация хеша пароля
python3 -c "from passlib.hash import bcrypt; print(bcrypt.hash('your_password'))"
```

## Настройка базы данных

### Инициализация
```bash
python3 setup_security.py
```

### Проверка структуры
```bash
sqlite3 vpn.db ".schema"
```

## Настройка сервисов

### 1. Копирование сервисных файлов
```bash
sudo cp veilbot.service /etc/systemd/system/
sudo cp veilbot-admin.service /etc/systemd/system/
```

### 2. Активация сервисов
```bash
sudo systemctl daemon-reload
sudo systemctl enable veilbot.service
sudo systemctl enable veilbot-admin.service
```

### 3. Запуск сервисов
```bash
sudo systemctl start veilbot.service
sudo systemctl start veilbot-admin.service
```

## Настройка Nginx

### 1. Копирование конфигурации
```bash
sudo cp nginx/veil-bot.ru /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/veil-bot.ru /etc/nginx/sites-enabled/
```

### 2. Проверка конфигурации
```bash
sudo nginx -t
```

### 3. Перезапуск Nginx
```bash
sudo systemctl restart nginx
```

## Получение SSL сертификата

### 1. Настройка DNS
Убедитесь, что домен указывает на ваш сервер:
```bash
nslookup veil-bot.ru
```

### 2. Получение сертификата
```bash
sudo certbot --nginx -d veil-bot.ru -d www.veil-bot.ru --non-interactive --agree-tos -m your-email@domain.com --redirect
```

### 3. Проверка автопродления
```bash
sudo certbot renew --dry-run
```

## Настройка бэкапов

### 1. Создание скрипта бэкапа
```bash
chmod +x backup_db.sh
```

### 2. Добавление в cron
```bash
crontab -e
# Добавить строку:
0 2 * * * /path/to/veilbot/backup_db.sh
```

## Проверка работоспособности

### 1. Статус сервисов
```bash
./manage_services.sh status
```

### 2. Проверка логов
```bash
./manage_services.sh logs
./manage_services.sh admin-logs
```

### 3. Тест админки
```bash
curl -I https://veil-bot.ru
```

Примечание: скрипт `setup_security.py`, упомянутый выше, отсутствует в репозитории. Инициализацию безопасности выполняйте вручную согласно `admin/config.py` (генерация `SECRET_KEY`, `ADMIN_PASSWORD_HASH`) и `docs/SECURITY.md`.

### 4. Тест бота
Отправьте команду `/start` вашему боту в Telegram.

## Мониторинг

### Логи
- Бот: `journalctl -u veilbot.service -f`
- Админка: `journalctl -u veilbot-admin.service -f`
- Nginx: `tail -f /var/log/nginx/veil-bot.ru.access.log`

### Метрики
- Использование CPU: `htop`
- Использование памяти: `free -h`
- Использование диска: `df -h`

## Устранение неполадок

### Проблемы с сервисами
```bash
# Перезапуск всех сервисов
./manage_services.sh restart

# Проверка статуса
systemctl status veilbot.service
systemctl status veilbot-admin.service
```

### Проблемы с SSL
```bash
# Проверка сертификата
sudo certbot certificates

# Обновление сертификата
sudo certbot renew
```

### Проблемы с базой данных
```bash
# Проверка целостности
sqlite3 vpn.db "PRAGMA integrity_check;"

# Восстановление из бэкапа
cp vpn.db.backup vpn.db
```

## Обновление

### 1. Остановка сервисов
```bash
./manage_services.sh stop
```

### 2. Обновление кода
```bash
git pull origin main
```

### 3. Обновление зависимостей
```bash
pip install -r requirements.txt
```

### 4. Запуск сервисов
```bash
./manage_services.sh start
```

## Безопасность

### Файрвол
```bash
# Установка UFW
sudo apt install ufw

# Настройка правил
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### Регулярные обновления
```bash
# Автоматические обновления безопасности
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

### Мониторинг безопасности
```bash
# Проверка открытых портов
sudo netstat -tlnp

# Проверка процессов
ps aux | grep python
``` 