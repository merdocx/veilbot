# 🔒 Документация системы безопасности VeilBot

## 📋 Обзор

Система безопасности VeilBot обеспечивает комплексный мониторинг и защиту от подозрительной активности в платежной системе и VPN-сервисе.

## 🏗️ Архитектура

### Основные компоненты

1. **SecurityLogger** - основной класс для логирования событий безопасности
2. **SecurityEvent** - модель события безопасности
3. **Rate Limiting** - система ограничения частоты запросов
4. **Risk Scoring** - система оценки рисков
5. **Alert System** - система оповещений

### Структура файлов

```
veilbot/
├── security_logger.py          # Основной модуль безопасности
├── test_security_logger.py     # Тесты системы безопасности
├── veilbot_security.log        # Файл логов безопасности
└── bot.py                      # Интеграция с основным ботом
```

## 🔧 Установка и настройка

### Автоматическая установка

Система безопасности автоматически интегрирована в основной бот. Никаких дополнительных настроек не требуется.

### Ручная настройка

```python
from security_logger import SecurityLogger

# Создание логгера с кастомными настройками
security_logger = SecurityLogger(
    log_file="custom_security.log",
    max_file_size=20 * 1024 * 1024  # 20MB
)
```

## 📊 Типы событий

### 1. Попытки платежей (`payment_attempt`)
- **Описание**: Логирование всех попыток создания платежей
- **Данные**: user_id, amount, protocol, country, email
- **Риск**: 10-25 баллов

### 2. Успешные платежи (`payment_success`)
- **Описание**: Логирование успешно завершенных платежей
- **Данные**: user_id, payment_id, amount, protocol, country
- **Риск**: 5-20 баллов

### 3. Неудачные платежи (`payment_failure`)
- **Описание**: Логирование неудачных попыток платежей
- **Данные**: user_id, amount, protocol, error, country
- **Риск**: 20-35 баллов

### 4. Создание VPN ключей (`key_creation`)
- **Описание**: Логирование создания VPN ключей
- **Данные**: user_id, key_id, protocol, server_id, tariff_id
- **Риск**: 15-30 баллов

### 5. Подозрительная активность (`suspicious_activity`)
- **Описание**: Логирование подозрительных действий
- **Данные**: user_id, activity_type, details
- **Риск**: 80 баллов (высокий)

## 🚨 Система ограничений (Rate Limiting)

### Лимиты по умолчанию

```python
rate_limits = {
    'payment_attempts': {'limit': 5, 'window': 300},   # 5 попыток за 5 минут
    'failed_payments': {'limit': 3, 'window': 600},    # 3 неудачных за 10 минут
    'key_requests': {'limit': 10, 'window': 300},      # 10 запросов ключей за 5 минут
}
```

### Настройка лимитов

```python
# Изменение лимитов
security_logger.rate_limits['payment_attempts'] = {'limit': 10, 'window': 300}
```

## 📈 Система оценки рисков

### Факторы риска

1. **Тип события** (базовый риск)
   - payment_attempt: 10 баллов
   - payment_failure: 20 баллов
   - payment_success: 5 баллов
   - key_creation: 15 баллов
   - suspicious_activity: 30 баллов

2. **Сумма платежа**
   - > 1000 рублей: +10 баллов

3. **Протокол VPN**
   - V2Ray: +5 баллов

4. **Страна**
   - Необычная страна: +15 баллов

5. **История пользователя**
   - > 2 неудачных попыток: +10 баллов за каждую

### Уровни риска

- **0-30**: Низкий риск
- **31-50**: Средний риск
- **51-79**: Высокий риск
- **80-100**: Критический риск

## 🔍 Мониторинг и алерты

### Автоматические алерты

Система автоматически отправляет алерты при:
- Превышении лимитов запросов
- Высоком уровне риска (>50 баллов)
- Подозрительной активности

### Формат алерта

```
🚨 СИГНАЛИЗАЦИЯ БЕЗОПАСНОСТИ
Пользователь: 123456789
Действие: rate_limit_exceeded
Риск: 80/100
Детали: {'description': 'Too many payment attempts'}
IP: 192.168.1.1
Время: 2025-08-07T21:24:26.509565
```

## 📝 Использование API

### Базовые функции

```python
from security_logger import (
    log_payment_attempt,
    log_payment_success,
    log_payment_failure,
    log_key_creation,
    log_suspicious_activity
)

# Логирование попытки платежа
log_payment_attempt(
    user_id=123456789,
    amount=1000,  # в копейках
    protocol='outline',
    country='RU',
    email='user@example.com'
)

# Логирование успешного платежа
log_payment_success(
    user_id=123456789,
    payment_id='pay_123456',
    amount=1000,
    protocol='outline',
    country='RU'
)

# Логирование неудачного платежа
log_payment_failure(
    user_id=123456789,
    amount=1000,
    protocol='outline',
    error='Payment failed',
    country='RU'
)

# Логирование создания ключа
log_key_creation(
    user_id=123456789,
    key_id='key_123',
    protocol='outline',
    server_id=1,
    tariff_id=1
)

# Логирование подозрительной активности
log_suspicious_activity(
    user_id=123456789,
    activity_type='rate_limit_exceeded',
    details='Too many payment attempts'
)
```

### Продвинутые функции

```python
from security_logger import security_logger

# Получение профиля риска пользователя
profile = security_logger.get_user_risk_profile(123456789)
print(profile)
# {
#     'user_id': 123456789,
#     'recent_failures': 2,
#     'rate_limits': {...},
#     'risk_level': 'normal'
# }

# Очистка старых данных
security_logger.cleanup_old_data()
```

## 📊 Анализ логов

### Структура лога

```json
{
    "timestamp": "2025-08-07T21:24:26.509565",
    "event_type": "payment_attempt",
    "user_id": 123456789,
    "action": "payment_creation",
    "success": true,
    "details": {
        "amount": 1000,
        "protocol": "outline",
        "country": "RU",
        "email": "user@example.com",
        "error": null,
        "rate_limit_ok": true
    },
    "ip_address": "192.168.1.1",
    "user_agent": "Telegram Bot",
    "session_id": null,
    "risk_score": 25
}
```

### Анализ логов

```bash
# Поиск подозрительной активности
grep "suspicious_activity" veilbot_security.log

# Поиск высокорисковых событий
grep "risk_score.*[8-9][0-9]" veilbot_security.log

# Поиск событий конкретного пользователя
grep "user_id.*123456789" veilbot_security.log

# Подсчет событий по типам
grep -o "event_type.*[^,]*" veilbot_security.log | sort | uniq -c
```

## 🧪 Тестирование

### Запуск тестов

```bash
# Запуск всех тестов безопасности
python3 test_security_logger.py

# Запуск с подробным выводом
python3 test_security_logger.py -v

# Запуск конкретного теста
python3 test_security_logger.py TestSecurityLogger.test_payment_attempt_logging
```

### Тестовые сценарии

1. **Нормальная активность** - тестирование обычных операций
2. **Превышение лимитов** - тестирование rate limiting
3. **Высокорисковые операции** - тестирование системы оценки рисков
4. **Подозрительная активность** - тестирование алертов

## 🔧 Настройка и кастомизация

### Кастомизация лимитов

```python
# Создание кастомного логгера
custom_logger = SecurityLogger()

# Изменение лимитов
custom_logger.rate_limits.update({
    'custom_action': {'limit': 20, 'window': 600}
})
```

### Кастомизация оценки рисков

```python
# Переопределение метода оценки рисков
def custom_risk_score(self, event_type, user_id, action, details):
    # Ваша логика оценки рисков
    base_score = super()._get_risk_score(event_type, user_id, action, details)
    # Дополнительная логика
    return base_score

# Применение кастомной функции
security_logger._get_risk_score = custom_risk_score
```

### Интеграция с внешними системами

```python
# Отправка алертов в Telegram
def send_telegram_alert(event):
    bot_token = "YOUR_BOT_TOKEN"
    chat_id = "YOUR_CHAT_ID"
    
    message = f"🚨 Security Alert: {event.action}"
    # Отправка через Telegram API
    
# Переопределение метода отправки алертов
security_logger._send_security_alert = send_telegram_alert
```

## 📈 Мониторинг производительности

### Метрики

- **Количество событий в секунду**
- **Средний риск по событиям**
- **Количество алертов**
- **Время обработки событий**

### Мониторинг файлов логов

```bash
# Размер файла логов
ls -lh veilbot_security.log

# Количество строк
wc -l veilbot_security.log

# Ротация логов (если нужно)
logrotate /etc/logrotate.d/veilbot_security
```

## 🚨 Реагирование на инциденты

### Процедура реагирования

1. **Обнаружение** - система автоматически обнаруживает подозрительную активность
2. **Алерт** - отправка уведомления администратору
3. **Анализ** - анализ логов и профиля риска пользователя
4. **Действие** - принятие мер (блокировка, ограничение, мониторинг)

### Типичные сценарии

1. **Превышение лимитов платежей**
   - Временная блокировка пользователя
   - Увеличенный мониторинг

2. **Высокорисковые платежи**
   - Дополнительная верификация
   - Запрос подтверждения

3. **Подозрительная активность**
   - Немедленная блокировка
   - Расследование инцидента

## 🔐 Безопасность системы

### Защита логов

- Файлы логов защищены правами доступа
- Ротация логов для предотвращения переполнения
- Шифрование чувствительных данных

### Аудит

- Все действия с системой безопасности логируются
- Регулярные проверки целостности логов
- Мониторинг доступа к файлам логов

## 📞 Поддержка

### Логирование проблем

При возникновении проблем с системой безопасности:

1. Проверьте файл `veilbot_security.log`
2. Запустите тесты: `python3 test_security_logger.py`
3. Проверьте права доступа к файлам логов
4. Убедитесь в корректности импортов

### Отладка

```python
# Включение отладочного режима
import logging
logging.getLogger('security').setLevel(logging.DEBUG)

# Проверка состояния логгера
print(security_logger.suspicious_activity_cache)
print(security_logger.rate_limits)
```

---

## 📋 Чек-лист внедрения

- [ ] Система безопасности интегрирована в бота
- [ ] Тесты проходят успешно
- [ ] Логи создаются корректно
- [ ] Rate limiting работает
- [ ] Система оценки рисков функционирует
- [ ] Алерты отправляются
- [ ] Документация обновлена
- [ ] Мониторинг настроен

**Статус**: ✅ Реализовано и протестировано
