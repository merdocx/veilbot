# VeilBot - Telegram VPN Bot

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111.0+-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

**VeilBot** - это Telegram-бот для управления VPN сервисами на базе **V2Ray VLESS**, с системой оплаты через YooKassa и оптимизацией памяти.

## 🚀 Возможности

### **Основной функционал**
- **Протокол VPN**: V2Ray VLESS
- **Система оплаты**: Интеграция с YooKassa
- **Управление ключами**: Автоматическое создание, удаление и мониторинг
- **Реферальная система**: Бонусы за приглашения
- **Оптимизация памяти**: Автоматическая очистка и мониторинг

### **Администрирование**
- **Веб-админка**: Полнофункциональный веб-интерфейс для управления
- **Логирование**: Подробные логи всех операций
- **Мониторинг**: Отслеживание состояния серверов и ключей
- **Безопасность**: Rate limiting, шифрование БД, security headers

### **VPN протокол**
- **V2Ray VLESS**: Продвинутый протокол с обфускацией трафика и Reality

## 📋 Требования

- **Python**: 3.11+
- **База данных**: SQLite3
- **Операционная система**: Linux/Unix
- **Веб-сервер**: Nginx (опционально)
- **Платежная система**: YooKassa аккаунт
- **Telegram**: Bot Token от @BotFather

## 🛠️ Установка

### 1. **Клонирование репозитория**
```bash
git clone <repository-url>
cd veilbot
```

### 2. **Установка зависимостей**
```bash
python3.11 -m pip install -r requirements.txt
```

### 3. **Установка инструментов UI-кита**
```bash
npm install
```

> Установка Node-зависимостей необходима для запуска линтеров/форматтеров CSS и JS админки.

### 4. **Настройка конфигурации**
```bash
cp .env.example .env
# Отредактируйте .env файл с вашими настройками
```

### 5. **Настройка переменных окружения**
```bash
# Обязательные параметры
TELEGRAM_BOT_TOKEN=your_bot_token_here
ADMIN_ID=your_telegram_id_here

# Опциональные параметры для YooKassa
YOOKASSA_SHOP_ID=your_shop_id
YOOKASSA_API_KEY=your_api_key
YOOKASSA_RETURN_URL=your_return_url
```

## 🚀 Запуск

### **Автоматический запуск через systemd (рекомендуется)**
```bash
# Управление сервисами
./manage_services.sh start      # Запустить оба сервиса (бот и админка)
./manage_services.sh stop       # Остановить оба сервиса
./manage_services.sh restart    # Перезапустить оба сервиса
./manage_services.sh status     # Показать статус обоих сервисов
./manage_services.sh logs       # Показать логи бота
./manage_services.sh admin-logs # Показать логи админки
```

### **Или через systemctl напрямую**
```bash
# Управление ботом
sudo systemctl start veilbot.service
sudo systemctl stop veilbot.service
sudo systemctl restart veilbot.service
sudo systemctl status veilbot.service

# Управление админкой
sudo systemctl start veilbot-admin.service
sudo systemctl stop veilbot-admin.service
sudo systemctl restart veilbot-admin.service
sudo systemctl status veilbot-admin.service
```

## 📁 Структура проекта

```
veilbot/
├── bot.py                 # Основной файл бота
├── config.py              # Конфигурация
├── db.py                  # База данных
├── vpn_protocols.py       # VPN протоколы
├── outline.py             # Outline API
├── utils.py               # Утилиты
├── memory_optimizer.py    # Оптимизация памяти
├── validators.py          # Валидаторы
├── security_logger.py     # Логирование безопасности
├── requirements.txt       # Зависимости Python
├── .env                   # Переменные окружения
├── manage_services.sh      # Управление сервисами (systemd)
├── backup_db.sh            # Скрипт резервного копирования БД
├── docs/                  # Документация (см. RUNBOOK.md)
│   └── archive/           # Исторические материалы
├── admin/                 # Веб-админка
├── payments/              # Платежные модули
└── scripts/               # Служебные скрипты эксплуатации
```

## ⚙️ Конфигурация

### **Основные настройки**
- `TELEGRAM_BOT_TOKEN` - токен вашего Telegram бота
- `ADMIN_ID` - ID администратора в Telegram
- `DATABASE_PATH` - путь к файлу базы данных (по умолчанию: vpn.db)

### **VPN протоколы**
- **Outline**: Простой и быстрый протокол
- **V2Ray VLESS**: Продвинутый протокол с обфускацией

### **Система оплаты**
- Интеграция с YooKassa
- Поддержка различных тарифов
- Автоматическая обработка платежей

## 🔧 Устранение неполадок

### **Бот не запускается**
1. Проверьте логи: `./manage_services.sh logs` или `sudo journalctl -u veilbot.service -f`
2. Убедитесь, что все зависимости установлены
3. Проверьте конфигурацию в `.env`
4. Проверьте статус сервиса: `sudo systemctl status veilbot.service`

### **Ошибка "TerminatedByOtherGetUpdates"**
1. Остановите сервис: `sudo systemctl stop veilbot.service`
2. Запустите заново: `sudo systemctl start veilbot.service`

### **Проблемы с базой данных**
1. Проверьте права доступа к файлу БД
2. Убедитесь, что SQLite3 установлен
3. Проверьте логи на ошибки БД

### **Проблемы с памятью**
1. Бот автоматически оптимизирует память
2. Проверьте логи оптимизации
3. При необходимости перезапустите бота

## 📊 Мониторинг

### **Логи**
- Рабочие логи (`bot.log`, `admin_audit.log`, `veilbot_security.log`) находятся в `/var/log/veilbot/`
- Архивы ротации создаются в `/var/log/veilbot/archive/`
- Скрипты управления логами: `scripts/rotate_logs.sh`, `scripts/cleanup.sh`

### **Статистика**
- Использование памяти
- Количество активных ключей
- Статистика платежей
- Мониторинг серверов

### **Автоматизация**
- GitHub Actions workflow `.github/workflows/ci.yml` выполняет `npm run lint` и `pytest`
- Регулярный бэкап SQLite оформляется как systemd timer (`setup/veilbot-db-backup.timer`)
- Ротация логов подключается отдельным таймером (`setup/veilbot-logrotate.timer`)
- Health-check эндпоинт админки: `GET /healthz` (см. `docs/RUNBOOK.md`)

## 🔒 Безопасность

- Шифрование базы данных
- Логирование всех действий
- Валидация входных данных
- Защита от SQL-инъекций
- Rate limiting для API
- Security headers
- CSRF защита в админ-панели

## 🤝 Поддержка

При возникновении проблем:
1. Проверьте логи: `./manage_services.sh logs` или `sudo journalctl -u veilbot.service -f`
2. Проверьте статус: `./manage_services.sh status` или `sudo systemctl status veilbot.service`
3. Перезапустите сервисы: `./manage_services.sh restart`
4. Обратитесь к документации или создайте issue

## 📝 Лицензия

Этот проект распространяется под лицензией MIT. См. файл LICENSE для подробностей.

## 🆘 Экстренная остановка

Для экстренной остановки всех процессов бота:
```bash
pkill -f "python3 bot.py"
```

---

**VeilBot** - надежное решение для управления VPN сервисами через Telegram!

## 📚 Дополнительная документация

- [API Documentation](API_DOCUMENTATION.md) - Подробная документация API
- [Admin UI Guide](ADMIN_UI_GUIDE.md) - UI-кит, компоненты и пайплайн проверок
- [Deployment Guide](DEPLOYMENT.md) - Руководство по развертыванию
- [Security Policy](SECURITY.md) - Политика безопасности
- [Changelog](CHANGELOG.md) - История изменений