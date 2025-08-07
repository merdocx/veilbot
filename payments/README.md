# 💳 VeilBot Payment Module

Модуль для обработки платежей в проекте VeilBot с поддержкой YooKassa и других платежных систем.

## 📋 Содержание

- [Особенности](#особенности)
- [Установка](#установка)
- [Быстрый старт](#быстрый-старт)
- [Архитектура](#архитектура)
- [API документация](#api-документация)
- [Примеры использования](#примеры-использования)
- [Миграция](#миграция)
- [Тестирование](#тестирование)

## ✨ Особенности

- **Асинхронная архитектура** - полная поддержка async/await
- **Типизация** - полная типизация с type hints
- **Модульность** - четкое разделение ответственности
- **Расширяемость** - легко добавить новые платежные системы
- **Валидация** - встроенная валидация всех данных
- **Логирование** - структурированное логирование
- **Тестируемость** - изолированные компоненты для тестирования

## 🚀 Установка

### Зависимости

Добавьте в `requirements.txt`:

```txt
# Платежный модуль
aiohttp==3.8.6
pydantic==2.7.3
aiosqlite==0.19.0

# Для тестирования
pytest==7.4.3
pytest-asyncio==0.21.1
```

### Установка

```bash
pip install -r requirements.txt
```

## 🏃‍♂️ Быстрый старт

### Базовая настройка

```python
from payments import PaymentService, YooKassaService, PaymentRepository

# Инициализация сервисов
yookassa_service = YooKassaService(
    shop_id="your_shop_id",
    api_key="your_api_key",
    return_url="https://t.me/your_bot"
)

payment_repo = PaymentRepository(db_path="vpn.db")
payment_service = PaymentService(
    payment_repo=payment_repo,
    yookassa_service=yookassa_service
)
```

### Создание платежа

```python
# Создание платежа
payment_id, confirmation_url = await payment_service.create_payment(
    user_id=123456789,
    tariff_id=1,
    amount=10000,  # 100 рублей в копейках
    email="user@example.com",
    country="RU",
    protocol="outline"
)

if payment_id and confirmation_url:
    print(f"Payment created: {payment_id}")
    print(f"Confirmation URL: {confirmation_url}")
```

### Ожидание платежа

```python
# Ожидание платежа с таймаутом
success = await payment_service.wait_for_payment(
    payment_id=payment_id,
    timeout_minutes=5
)

if success:
    print("Payment completed successfully!")
else:
    print("Payment failed or timeout")
```

## 🏗️ Архитектура

### Структура модуля

```
payments/
├── __init__.py              # Основной экспорт
├── models/                  # Модели данных
│   ├── __init__.py
│   ├── payment.py          # Модель платежа
│   └── enums.py            # Перечисления
├── services/               # Бизнес-логика
│   ├── __init__.py
│   ├── payment_service.py  # Основной сервис
│   ├── yookassa_service.py # YooKassa интеграция
│   └── webhook_service.py  # Webhook обработчики
├── repositories/           # Работа с данными
│   ├── __init__.py
│   └── payment_repository.py
├── keyboards/              # Telegram клавиатуры
│   ├── __init__.py
│   └── payment_keyboards.py
├── utils/                  # Утилиты
│   ├── __init__.py
│   ├── validators.py       # Валидация
│   └── formatters.py       # Форматирование
└── README.md              # Документация
```

### Принципы архитектуры

1. **Clean Architecture** - разделение на слои
2. **Dependency Injection** - инверсия зависимостей
3. **Repository Pattern** - абстракция доступа к данным
4. **Service Layer** - бизнес-логика в сервисах
5. **Single Responsibility** - единственная ответственность

## 📚 API документация

### PaymentService

Основной сервис для работы с платежами.

#### Методы

##### `create_payment()`

Создание нового платежа.

```python
async def create_payment(
    self,
    user_id: int,
    tariff_id: int,
    amount: int,
    email: str,
    country: Optional[str] = None,
    protocol: str = "outline",
    description: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]
```

**Параметры:**
- `user_id` - ID пользователя
- `tariff_id` - ID тарифа
- `amount` - Сумма в копейках
- `email` - Email для чека
- `country` - Код страны (опционально)
- `protocol` - VPN протокол
- `description` - Описание платежа (опционально)

**Возвращает:**
- `Tuple[payment_id, confirmation_url]` или `(None, None)` при ошибке

##### `wait_for_payment()`

Ожидание платежа с таймаутом.

```python
async def wait_for_payment(
    self,
    payment_id: str,
    timeout_minutes: int = 5,
    check_interval_seconds: int = 5
) -> bool
```

**Параметры:**
- `payment_id` - ID платежа
- `timeout_minutes` - Таймаут в минутах
- `check_interval_seconds` - Интервал проверки

**Возвращает:**
- `True` если платеж оплачен, `False` при таймауте

##### `process_payment_success()`

Обработка успешного платежа.

```python
async def process_payment_success(self, payment_id: str) -> bool
```

**Параметры:**
- `payment_id` - ID платежа

**Возвращает:**
- `True` если обработка успешна

### YooKassaService

Сервис для работы с YooKassa API.

#### Методы

##### `create_payment()`

Создание платежа в YooKassa.

```python
async def create_payment(
    self,
    amount: int,
    description: str,
    email: str,
    payment_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], Optional[str]]
```

##### `check_payment()`

Проверка статуса платежа.

```python
async def check_payment(self, payment_id: str) -> bool
```

##### `refund_payment()`

Возврат платежа.

```python
async def refund_payment(
    self,
    payment_id: str,
    amount: int,
    reason: str = "Возврат"
) -> bool
```

### PaymentRepository

Репозиторий для работы с платежами в БД.

#### Методы

##### `create()`

Создание платежа в БД.

```python
async def create(self, payment: Payment) -> Payment
```

##### `get_by_payment_id()`

Получение платежа по ID.

```python
async def get_by_payment_id(self, payment_id: str) -> Optional[Payment]
```

##### `update_status()`

Обновление статуса платежа.

```python
async def update_status(self, payment_id: str, status: PaymentStatus) -> bool
```

## 💡 Примеры использования

### Интеграция в Telegram бот

```python
from aiogram import Bot, Dispatcher, types
from payments import PaymentService, YooKassaService, PaymentRepository
from payments.keyboards.payment_keyboards import PaymentKeyboards

class PaymentBot:
    def __init__(self, bot: Bot, config: dict):
        self.bot = bot
        
        # Инициализация платежного модуля
        self.yookassa_service = YooKassaService(
            shop_id=config['YOOKASSA_SHOP_ID'],
            api_key=config['YOOKASSA_API_KEY'],
            return_url=config['YOOKASSA_RETURN_URL']
        )
        
        self.payment_repo = PaymentRepository()
        self.payment_service = PaymentService(
            payment_repo=self.payment_repo,
            yookassa_service=self.yookassa_service
        )
        
        self.keyboards = PaymentKeyboards()
    
    async def handle_payment_request(self, message: types.Message):
        """Обработка запроса на оплату"""
        user_id = message.from_user.id
        
        # Создание платежа
        payment_id, confirmation_url = await self.payment_service.create_payment(
            user_id=user_id,
            tariff_id=1,
            amount=10000,
            email="user@example.com",
            protocol="outline"
        )
        
        if payment_id and confirmation_url:
            # Отправка сообщения с кнопкой оплаты
            keyboard = self.keyboards.get_payment_keyboard(
                payment_url=confirmation_url,
                payment_id=payment_id
            )
            
            await message.answer(
                "💳 Оплата готова! Нажмите кнопку ниже для оплаты.",
                reply_markup=keyboard
            )
            
            # Запуск ожидания платежа
            asyncio.create_task(
                self.payment_service.wait_for_payment(payment_id)
            )
        else:
            await message.answer("❌ Ошибка создания платежа")
```

### Webhook обработчик

```python
from fastapi import FastAPI, Request
from payments import WebhookService, PaymentRepository, PaymentService

app = FastAPI()

# Инициализация сервисов
payment_repo = PaymentRepository()
payment_service = PaymentService(payment_repo, yookassa_service)
webhook_service = WebhookService(payment_repo, payment_service)

@app.post("/webhook/yookassa")
async def yookassa_webhook(request: Request):
    """Обработка webhook от YooKassa"""
    result = await webhook_service.process_webhook_request(request, "yookassa")
    return result
```

## 🔄 Миграция

### Поэтапная миграция

1. **Этап 1**: Создание нового модуля (параллельно со старым)
2. **Этап 2**: Замена функций создания платежей
3. **Этап 3**: Замена функций ожидания платежей
4. **Этап 4**: Замена webhook обработчиков
5. **Этап 5**: Удаление старого кода

### Пример замены функции

**Старый код:**
```python
async def create_payment_with_email_and_protocol(message, user_id, tariff, email, country, protocol):
    payment_id, payment_url = await asyncio.get_event_loop().run_in_executor(
        None, create_payment, tariff['price_rub'], f"Покупка тарифа '{tariff['name']}'", email
    )
    # ... остальной код
```

**Новый код:**
```python
async def create_payment_new(message, user_id, tariff, email, country, protocol):
    payment_id, confirmation_url = await payment_service.create_payment(
        user_id=user_id,
        tariff_id=tariff['id'],
        amount=tariff['price_rub'] * 100,  # Конвертируем в копейки
        email=email,
        country=country,
        protocol=protocol
    )
    # ... остальной код
```

## 🧪 Тестирование

### Unit тесты

```python
import pytest
from payments import PaymentService, YooKassaService, PaymentRepository

@pytest.mark.asyncio
async def test_create_payment():
    """Тест создания платежа"""
    # Моки
    mock_repo = MockPaymentRepository()
    mock_yookassa = MockYooKassaService()
    
    service = PaymentService(mock_repo, mock_yookassa)
    
    # Тест
    payment_id, url = await service.create_payment(
        user_id=123,
        tariff_id=1,
        amount=10000,
        email="test@example.com"
    )
    
    assert payment_id is not None
    assert url is not None
```

### Integration тесты

```python
@pytest.mark.asyncio
async def test_payment_flow():
    """Тест полного цикла платежа"""
    # Создание платежа
    payment_id, url = await service.create_payment(...)
    
    # Симуляция оплаты
    await mock_yookassa.simulate_payment(payment_id)
    
    # Ожидание платежа
    success = await service.wait_for_payment(payment_id)
    
    assert success is True
```

## 📊 Мониторинг

### Логирование

Модуль использует структурированное логирование:

```python
import logging

logger = logging.getLogger(__name__)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

### Метрики

```python
# Получение статистики платежей
stats = await payment_service.get_payment_statistics(days=30)
print(f"Успешность: {stats['success_rate']:.1f}%")
print(f"Общая сумма: {stats['total_amount'] / 100:.2f}₽")
```

## 🔧 Конфигурация

### Переменные окружения

```bash
# YooKassa
YOOKASSA_SHOP_ID=your_shop_id
YOOKASSA_API_KEY=your_api_key
YOOKASSA_RETURN_URL=https://t.me/your_bot

# База данных
DATABASE_PATH=vpn.db

# Режим работы
TEST_MODE=true
```

### Конфигурационный файл

```python
# config.py
PAYMENT_CONFIG = {
    'YOOKASSA_SHOP_ID': os.getenv('YOOKASSA_SHOP_ID'),
    'YOOKASSA_API_KEY': os.getenv('YOOKASSA_API_KEY'),
    'YOOKASSA_RETURN_URL': os.getenv('YOOKASSA_RETURN_URL'),
    'DATABASE_PATH': os.getenv('DATABASE_PATH', 'vpn.db'),
    'TEST_MODE': os.getenv('TEST_MODE', 'false').lower() == 'true'
}
```

## 🤝 Поддержка

### Обработка ошибок

```python
try:
    payment_id, url = await payment_service.create_payment(...)
except PaymentError as e:
    logger.error(f"Payment error: {e}")
    # Обработка ошибки
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    # Общая обработка ошибок
```

### Валидация данных

```python
from payments.utils.validators import PaymentValidators

# Валидация email
if not PaymentValidators.validate_email(email):
    raise ValueError("Invalid email")

# Валидация суммы
is_valid, error = PaymentValidators.validate_amount(amount)
if not is_valid:
    raise ValueError(error)
```

## 📈 Производительность

### Асинхронная работа

Все операции с внешними API выполняются асинхронно:

```python
# Параллельная обработка платежей
tasks = []
for payment in pending_payments:
    task = asyncio.create_task(
        payment_service.process_payment_success(payment.payment_id)
    )
    tasks.append(task)

results = await asyncio.gather(*tasks)
```

### Кэширование

Для улучшения производительности можно добавить кэширование:

```python
import redis

# Кэширование статуса платежа
async def get_payment_status_cached(payment_id: str) -> str:
    cache_key = f"payment_status:{payment_id}"
    
    # Проверяем кэш
    cached_status = await redis.get(cache_key)
    if cached_status:
        return cached_status
    
    # Получаем из API
    status = await yookassa_service.check_payment(payment_id)
    
    # Сохраняем в кэш
    await redis.setex(cache_key, 300, status)  # TTL 5 минут
    
    return status
```

## 🔮 Планы развития

### Будущие возможности

1. **Поддержка других платежных систем**
   - Stripe
   - PayPal
   - СБП

2. **Расширенная аналитика**
   - Детальная статистика
   - Прогнозирование
   - A/B тестирование

3. **Автоматизация**
   - Автоматические возвраты
   - Уведомления
   - Обработка споров

4. **Безопасность**
   - Проверка подписи webhook'ов
   - Защита от мошенничества
   - Аудит операций

---

**Автор:** VeilBot Team  
**Версия:** 1.0.0  
**Лицензия:** MIT
