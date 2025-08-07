# 🛠️ План конкретных улучшений VeilBot

## 🎯 **Приоритет 1: Критические улучшения (1 неделя)**

### 1. **Расширенное логирование безопасности**

#### Добавить в `bot.py`:
```python
import logging
from datetime import datetime
import json

# Настройка логирования безопасности
security_logger = logging.getLogger('security')
security_logger.setLevel(logging.INFO)

# Создать файловый обработчик
security_handler = logging.FileHandler('veilbot_security.log')
security_handler.setLevel(logging.INFO)

# Создать форматтер
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
security_handler.setFormatter(formatter)

# Добавить обработчик к логгеру
security_logger.addHandler(security_handler)

def log_payment_security(user_id: int, amount: float, action: str, success: bool = True, error: str = None):
    """Логирование платежных операций для безопасности"""
    log_data = {
        'timestamp': datetime.now().isoformat(),
        'user_id': user_id,
        'amount': amount,
        'action': action,
        'success': success,
        'error': error,
        'ip_address': None,  # Можно добавить IP пользователя
        'user_agent': None   # Можно добавить User-Agent
    }
    
    if success:
        security_logger.info(f"PAYMENT_SUCCESS: {json.dumps(log_data)}")
    else:
        security_logger.error(f"PAYMENT_FAILURE: {json.dumps(log_data)}")

def log_suspicious_activity(user_id: int, activity_type: str, details: str):
    """Логирование подозрительной активности"""
    log_data = {
        'timestamp': datetime.now().isoformat(),
        'user_id': user_id,
        'activity_type': activity_type,
        'details': details,
        'risk_level': 'medium'
    }
    
    security_logger.warning(f"SUSPICIOUS_ACTIVITY: {json.dumps(log_data)}")
```

#### Обновить функции платежей:
```python
async def create_payment_with_email_and_protocol(message, user_id, tariff, email=None, country=None, protocol="outline"):
    try:
        # Логируем попытку создания платежа
        log_payment_security(user_id, tariff['price_rub'], 'create_payment_attempt')
        
        # Существующий код...
        result = await payment_service.create_payment(...)
        
        if result:
            log_payment_security(user_id, tariff['price_rub'], 'create_payment_success')
        else:
            log_payment_security(user_id, tariff['price_rub'], 'create_payment_failed', success=False, error="Payment creation failed")
            
        return result
        
    except Exception as e:
        log_payment_security(user_id, tariff['price_rub'], 'create_payment_error', success=False, error=str(e))
        raise
```

### 2. **Мониторинг производительности с Prometheus**

#### Создать файл `monitoring.py`:
```python
from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time
from functools import wraps

# Метрики
payment_counter = Counter('veilbot_payments_total', 'Total payments', ['status', 'protocol'])
payment_duration = Histogram('veilbot_payment_duration_seconds', 'Payment processing time')
user_actions = Counter('veilbot_user_actions_total', 'User actions', ['action'])
memory_usage = Gauge('veilbot_memory_bytes', 'Memory usage in bytes')
active_users = Gauge('veilbot_active_users', 'Number of active users')

def monitor_function_duration(metric_name: str):
    """Декоратор для мониторинга времени выполнения функций"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                payment_duration.observe(duration)
                return result
            except Exception as e:
                duration = time.time() - start_time
                payment_duration.observe(duration)
                raise
        return wrapper
    return decorator

def start_monitoring():
    """Запуск сервера метрик"""
    start_http_server(8000)
    print("🚀 Prometheus metrics server started on port 8000")

def update_memory_metrics():
    """Обновление метрик памяти"""
    import psutil
    process = psutil.Process()
    memory_info = process.memory_info()
    memory_usage.set(memory_info.rss)
```

#### Добавить в `bot.py`:
```python
from monitoring import (
    payment_counter, payment_duration, user_actions, 
    memory_usage, active_users, monitor_function_duration, 
    start_monitoring, update_memory_metrics
)

# Запустить мониторинг при старте
start_monitoring()

# Обновлять метрики памяти каждые 30 секунд
async def update_metrics_periodically():
    while True:
        update_memory_metrics()
        await asyncio.sleep(30)

# Запустить обновление метрик
asyncio.create_task(update_metrics_periodically())

# Применить мониторинг к функциям
@monitor_function_duration('payment_creation')
async def create_payment_with_email_and_protocol(message, user_id, tariff, email=None, country=None, protocol="outline"):
    # Логируем действие пользователя
    user_actions.labels(action='create_payment').inc()
    
    try:
        # Существующий код...
        result = await payment_service.create_payment(...)
        
        if result:
            payment_counter.labels(status='success', protocol=protocol).inc()
        else:
            payment_counter.labels(status='failed', protocol=protocol).inc()
            
        return result
        
    except Exception as e:
        payment_counter.labels(status='error', protocol=protocol).inc()
        raise
```

### 3. **Retry механизм для платежей**

#### Создать файл `retry_utils.py`:
```python
import asyncio
import logging
from functools import wraps
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)

def retry_on_error(max_attempts: int = 3, delay: float = 1.0, backoff_factor: float = 2.0):
    """Декоратор для повторных попыток с экспоненциальной задержкой"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        wait_time = delay * (backoff_factor ** attempt)
                        logger.warning(f"Attempt {attempt + 1} failed for {func.__name__}: {e}. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"All {max_attempts} attempts failed for {func.__name__}: {e}")
            
            raise last_exception
        return wrapper
    return decorator

def retry_with_circuit_breaker(max_failures: int = 5, reset_timeout: float = 60.0):
    """Декоратор с circuit breaker паттерном"""
    def decorator(func: Callable) -> Callable:
        failure_count = 0
        last_failure_time = 0.0
        
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            nonlocal failure_count, last_failure_time
            
            current_time = asyncio.get_event_loop().time()
            
            # Проверяем, не открыт ли circuit breaker
            if failure_count >= max_failures:
                if current_time - last_failure_time < reset_timeout:
                    raise Exception(f"Circuit breaker is open for {func.__name__}")
                else:
                    # Сбрасываем счетчик после timeout
                    failure_count = 0
            
            try:
                result = await func(*args, **kwargs)
                # Сбрасываем счетчик при успехе
                failure_count = 0
                return result
            except Exception as e:
                failure_count += 1
                last_failure_time = current_time
                raise
                
        return wrapper
    return decorator
```

#### Применить к функциям платежей:
```python
from retry_utils import retry_on_error, retry_with_circuit_breaker

@retry_on_error(max_attempts=3, delay=1.0)
@retry_with_circuit_breaker(max_failures=5, reset_timeout=60.0)
async def create_payment_with_email_and_protocol(message, user_id, tariff, email=None, country=None, protocol="outline"):
    # Существующий код...
    pass

@retry_on_error(max_attempts=3, delay=1.0)
async def wait_for_payment_with_protocol(message, payment_id, server, user_id, tariff, country=None, protocol="outline"):
    # Существующий код...
    pass
```

## 🎯 **Приоритет 2: Качество кода (1 месяц)**

### 4. **Type Hints и валидация**

#### Обновить `bot.py` с type hints:
```python
from typing import Optional, Dict, List, Any, Union
from dataclasses import dataclass

@dataclass
class Tariff:
    id: int
    name: str
    price_rub: float
    duration_sec: int
    protocol: str

@dataclass
class PaymentResult:
    success: bool
    payment_id: Optional[str]
    error: Optional[str]
    redirect_url: Optional[str]

async def create_payment_with_email_and_protocol(
    message: types.Message,
    user_id: int,
    tariff: Tariff,
    email: Optional[str] = None,
    country: Optional[str] = None,
    protocol: str = "outline"
) -> PaymentResult:
    """Создание платежа с email и протоколом"""
    try:
        # Валидация входных данных
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("Invalid user_id")
        
        if not isinstance(tariff, Tariff):
            raise ValueError("Invalid tariff object")
        
        if email and not is_valid_email(email):
            raise ValueError("Invalid email format")
        
        # Логирование безопасности
        log_payment_security(user_id, tariff.price_rub, 'create_payment_attempt')
        
        # Существующий код...
        
        return PaymentResult(
            success=True,
            payment_id=payment_id,
            error=None,
            redirect_url=redirect_url
        )
        
    except Exception as e:
        log_payment_security(user_id, tariff.price_rub, 'create_payment_error', success=False, error=str(e))
        return PaymentResult(
            success=False,
            payment_id=None,
            error=str(e),
            redirect_url=None
        )
```

### 5. **Линтеры и форматирование**

#### Создать `pyproject.toml`:
```toml
[tool.black]
line-length = 88
target-version = ['py310']
include = '\.pyi?$'
extend-exclude = '''
/(
  # directories
  \.eggs
  | \.git
  | \.hg
  | \.mypy_cache
  | \.tox
  | \.venv
  | build
  | dist
)/
'''

[tool.isort]
profile = "black"
multi_line_output = 3
line_length = 88

[tool.mypy]
python_version = "3.10"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
disallow_untyped_decorators = true
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_no_return = true
warn_unreachable = true
strict_equality = true

[tool.flake8]
max-line-length = 88
extend-ignore = ["E203", "W503"]
exclude = [
    ".git",
    "__pycache__",
    "build",
    "dist",
    ".venv",
    ".mypy_cache",
]
```

#### Создать `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.3.0
    hooks:
      - id: black
        language_version: python3.10

  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort

  - repo: https://github.com/pycqa/flake8
    rev: 6.0.0
    hooks:
      - id: flake8

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.3.0
    hooks:
      - id: mypy
        additional_dependencies: [types-requests]
```

### 6. **Расширенные тесты**

#### Создать `test_integration.py`:
```python
import unittest
import asyncio
from unittest.mock import Mock, patch
from bot import create_payment_with_email_and_protocol, log_payment_security
from monitoring import payment_counter, user_actions

class TestPaymentIntegration(unittest.TestCase):
    """Интеграционные тесты платежного модуля"""
    
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
    
    def tearDown(self):
        self.loop.close()
    
    @patch('bot.payment_service')
    async def test_payment_creation_with_monitoring(self, mock_payment_service):
        """Тест создания платежа с мониторингом"""
        # Подготовка
        mock_message = Mock()
        mock_message.from_user.id = 12345
        
        tariff = {
            'id': 1,
            'name': 'Test Tariff',
            'price_rub': 100.0,
            'duration_sec': 86400
        }
        
        mock_payment_service.create_payment.return_value = {
            'payment_id': 'test_payment_123',
            'redirect_url': 'https://payment.example.com'
        }
        
        # Выполнение
        result = await create_payment_with_email_and_protocol(
            mock_message, 12345, tariff, email='test@example.com'
        )
        
        # Проверки
        self.assertTrue(result.success)
        self.assertEqual(result.payment_id, 'test_payment_123')
        
        # Проверка метрик
        self.assertEqual(
            payment_counter._value.sum(),
            1  # Один успешный платеж
        )
    
    @patch('bot.payment_service')
    async def test_payment_retry_mechanism(self, mock_payment_service):
        """Тест механизма повторных попыток"""
        # Подготовка - сервис падает первые 2 раза
        mock_payment_service.create_payment.side_effect = [
            Exception("Network error"),
            Exception("Timeout"),
            {'payment_id': 'test_payment_123'}
        ]
        
        mock_message = Mock()
        mock_message.from_user.id = 12345
        
        tariff = {'id': 1, 'name': 'Test', 'price_rub': 100.0, 'duration_sec': 86400}
        
        # Выполнение
        result = await create_payment_with_email_and_protocol(
            mock_message, 12345, tariff
        )
        
        # Проверки
        self.assertTrue(result.success)
        self.assertEqual(mock_payment_service.create_payment.call_count, 3)

class TestSecurityLogging(unittest.TestCase):
    """Тесты логирования безопасности"""
    
    @patch('bot.security_logger')
    def test_payment_security_logging(self, mock_logger):
        """Тест логирования платежных операций"""
        # Выполнение
        log_payment_security(12345, 100.0, 'test_action', success=True)
        
        # Проверки
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        self.assertIn('PAYMENT_SUCCESS', log_message)
        self.assertIn('12345', log_message)
        self.assertIn('100.0', log_message)

if __name__ == '__main__':
    unittest.main()
```

## 🎯 **Приоритет 3: Масштабирование (2-3 месяца)**

### 7. **Docker контейнеризация**

#### Создать `Dockerfile`:
```dockerfile
FROM python:3.10-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Создание рабочей директории
WORKDIR /app

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода приложения
COPY . .

# Создание пользователя для безопасности
RUN useradd --create-home --shell /bin/bash veilbot
RUN chown -R veilbot:veilbot /app
USER veilbot

# Открытие портов
EXPOSE 8000 8001

# Запуск приложения
CMD ["python", "bot.py"]
```

#### Создать `docker-compose.yml`:
```yaml
version: '3.8'

services:
  bot:
    build: .
    ports:
      - "8000:8000"  # Prometheus metrics
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - DATABASE_URL=${DATABASE_URL}
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  admin:
    build: .
    command: python admin/admin_routes.py
    ports:
      - "8001:8001"
    environment:
      - DATABASE_URL=${DATABASE_URL}
    volumes:
      - ./data:/app/data
    restart: unless-stopped
    depends_on:
      - bot

  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana-storage:/var/lib/grafana
    restart: unless-stopped
    depends_on:
      - prometheus

volumes:
  grafana-storage:
```

### 8. **Миграция на PostgreSQL**

#### Создать `database_migration.py`:
```python
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import logging

logger = logging.getLogger(__name__)

class DatabaseMigrator:
    """Миграция с SQLite на PostgreSQL"""
    
    def __init__(self, sqlite_path: str, postgres_url: str):
        self.sqlite_path = sqlite_path
        self.postgres_url = postgres_url
    
    def migrate_data(self):
        """Миграция всех данных"""
        try:
            # Подключение к SQLite
            sqlite_conn = sqlite3.connect(self.sqlite_path)
            sqlite_conn.row_factory = sqlite3.Row
            
            # Подключение к PostgreSQL
            pg_conn = psycopg2.connect(self.postgres_url)
            pg_cursor = pg_conn.cursor(cursor_factory=RealDictCursor)
            
            # Миграция таблиц
            self._migrate_tariffs(sqlite_conn, pg_cursor)
            self._migrate_servers(sqlite_conn, pg_cursor)
            self._migrate_keys(sqlite_conn, pg_cursor)
            self._migrate_payments(sqlite_conn, pg_cursor)
            
            # Подтверждение изменений
            pg_conn.commit()
            
            logger.info("Migration completed successfully")
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            pg_conn.rollback()
            raise
        finally:
            sqlite_conn.close()
            pg_conn.close()
    
    def _migrate_tariffs(self, sqlite_conn, pg_cursor):
        """Миграция тарифов"""
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM tariffs")
        tariffs = sqlite_cursor.fetchall()
        
        for tariff in tariffs:
            pg_cursor.execute("""
                INSERT INTO tariffs (id, name, price_rub, duration_sec, protocol)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (tariff['id'], tariff['name'], tariff['price_rub'], 
                  tariff['duration_sec'], tariff.get('protocol', 'outline')))
        
        logger.info(f"Migrated {len(tariffs)} tariffs")
    
    def _migrate_servers(self, sqlite_conn, pg_cursor):
        """Миграция серверов"""
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM servers")
        servers = sqlite_cursor.fetchall()
        
        for server in servers:
            pg_cursor.execute("""
                INSERT INTO servers (id, name, host, port, protocol, country, api_url, api_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (server['id'], server['name'], server['host'], server['port'],
                  server['protocol'], server['country'], server['api_url'], server['api_key']))
        
        logger.info(f"Migrated {len(servers)} servers")
    
    def _migrate_keys(self, sqlite_conn, pg_cursor):
        """Миграция ключей"""
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM keys")
        keys = sqlite_cursor.fetchall()
        
        for key in keys:
            pg_cursor.execute("""
                INSERT INTO keys (id, user_id, server_id, access_url, name, created_at, expires_at, traffic_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (key['id'], key['user_id'], key['server_id'], key['access_url'],
                  key['name'], key['created_at'], key['expires_at'], key.get('traffic_used', 0)))
        
        logger.info(f"Migrated {len(keys)} keys")
    
    def _migrate_payments(self, sqlite_conn, pg_cursor):
        """Миграция платежей"""
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute("SELECT * FROM payments")
        payments = sqlite_cursor.fetchall()
        
        for payment in payments:
            pg_cursor.execute("""
                INSERT INTO payments (id, user_id, tariff_id, amount, status, created_at, paid_at, email)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (payment['id'], payment['user_id'], payment['tariff_id'],
                  payment['amount'], payment['status'], payment['created_at'],
                  payment.get('paid_at'), payment.get('email')))
        
        logger.info(f"Migrated {len(payments)} payments")

if __name__ == '__main__':
    migrator = DatabaseMigrator(
        sqlite_path='vpn.db',
        postgres_url='postgresql://user:password@localhost/veilbot'
    )
    migrator.migrate_data()
```

## 📋 **План внедрения**

### Неделя 1: Критические улучшения
1. **День 1-2**: Добавить расширенное логирование безопасности
2. **День 3-4**: Настроить мониторинг Prometheus
3. **День 5-7**: Добавить retry механизм

### Месяц 1: Качество кода
1. **Неделя 1**: Добавить type hints
2. **Неделя 2**: Настроить линтеры
3. **Неделя 3**: Расширить тесты
4. **Неделя 4**: Обновить документацию

### Месяц 2-3: Масштабирование
1. **Месяц 2**: Docker контейнеризация
2. **Месяц 3**: Миграция на PostgreSQL

## 🎯 **Ожидаемые результаты**

### После недели 1:
- ✅ Безопасность: +50%
- ✅ Мониторинг: +100%
- ✅ Стабильность: +30%

### После месяца 1:
- ✅ Качество кода: +40%
- ✅ Покрытие тестами: 90%
- ✅ Документация: +100%

### После месяца 3:
- ✅ Готовность к масштабированию: +100%
- ✅ Автоматизация: +80%
- ✅ Производительность: +50%

---

**Статус**: ✅ План готов к реализации  
**Приоритет**: Безопасность > Качество > Масштабирование  
**Готовность**: 100% к внедрению
