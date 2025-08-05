# 🚀 Обновления V2Ray API - Исторические данные трафика

## 📅 Дата обновления: 5 августа 2025

### 🔄 Что изменилось

#### **Новые эндпоинты для исторических данных:**

1. **`GET /api/traffic/history`** - Общий объем трафика всех ключей с момента создания
2. **`GET /api/keys/{key_id}/traffic/history`** - История трафика конкретного ключа
3. **`GET /api/traffic/daily/{date}`** - Ежедневная статистика трафика
4. **`POST /api/keys/{key_id}/traffic/history/reset`** - Сброс истории ключа
5. **`POST /api/traffic/history/cleanup`** - Очистка старых данных

### 📊 Новые возможности

#### **1. Исторические данные трафика**
- Отслеживание общего объема трафика с момента создания ключей
- Накопление данных по дням
- Анализ активности пользователей
- Детальная статистика сессий

#### **2. Ежедневная статистика**
- Статистика по конкретным датам
- Количество активных ключей
- Общее количество сессий
- Форматированный объем трафика

#### **3. Управление данными**
- Сброс истории для отдельных ключей
- Автоматическая очистка старых данных
- Настраиваемый период хранения

### 🔧 Обновления в коде

#### **Новые методы в `V2RayProtocol`:**

```python
# Получение исторических данных
async def get_traffic_history(self) -> Dict:
    """Получить общий объем трафика для всех ключей с момента создания"""

async def get_key_traffic_history(self, key_id: str) -> Dict:
    """Получить общий объем трафика для конкретного ключа с момента создания"""

async def get_daily_traffic_stats(self, date: str) -> Dict:
    """Получить ежедневную статистику трафика (формат даты: YYYY-MM-DD)"""

async def reset_key_traffic_history(self, key_id: str) -> bool:
    """Сбросить историю трафика для конкретного ключа"""

async def cleanup_traffic_history(self, days_to_keep: int = 30) -> bool:
    """Очистить старые данные истории трафика"""
```

### 📈 Примеры использования

#### **Получение общей истории трафика:**
```python
v2ray = ProtocolFactory.create_protocol('v2ray', server_config)
history = await v2ray.get_traffic_history()

data = history.get('data', {})
total_keys = data.get('total_keys', 0)
active_keys = data.get('active_keys', 0)
total_traffic = data.get('total_traffic_formatted', '0 B')

print(f"Total keys: {total_keys}")
print(f"Active keys: {active_keys}")
print(f"Total traffic: {total_traffic}")
```

#### **Получение истории конкретного ключа:**
```python
key_history = await v2ray.get_key_traffic_history(key_id)
data = key_history.get('data', {})
key_uuid = data.get('key_uuid', 'Unknown')
total_traffic = data.get('total_traffic', {})
total_formatted = total_traffic.get('total_formatted', '0 B')

print(f"Key UUID: {key_uuid}")
print(f"Total traffic: {total_formatted}")
```

#### **Получение ежедневной статистики:**
```python
today = datetime.now().strftime('%Y-%m-%d')
daily_stats = await v2ray.get_daily_traffic_stats(today)

data = daily_stats.get('data', {})
date = data.get('date', 'Unknown')
total_formatted = data.get('total_formatted', '0 B')
active_keys = data.get('active_keys', 0)

print(f"Date: {date}")
print(f"Total traffic: {total_formatted}")
print(f"Active keys: {active_keys}")
```

### 🧪 Тестирование

#### **Создан тестовый скрипт:**
- `test_v2ray_history.py` - Комплексное тестирование новых эндпоинтов
- Проверка всех новых методов
- Валидация структуры ответов
- Тестирование обработки ошибок

#### **Обновлены unit-тесты:**
- Добавлены тесты для новых методов
- Проверка существования всех методов
- Валидация импортов

### 📊 Структура данных

#### **Ответ `/api/traffic/history`:**
```json
{
    "status": "success",
    "data": {
        "total_keys": 2,
        "active_keys": 1,
        "total_traffic_bytes": 14548992,
        "total_rx_bytes": 7274496,
        "total_tx_bytes": 7274496,
        "keys": [
            {
                "key_uuid": "0e5ff24b-c47b-4193-ae76-3ba8233a1930",
                "key_name": "nvipetrenko@gmail.com",
                "port": 10001,
                "created_at": "2025-08-05T16:06:36.865147",
                "last_activity": "2025-08-05T16:06:47.590883",
                "is_active": true,
                "total_traffic": {
                    "total_bytes": 14548992,
                    "rx_bytes": 7274496,
                    "tx_bytes": 7274496,
                    "total_connections": 43,
                    "total_formatted": "13.88 MB",
                    "rx_formatted": "6.94 MB",
                    "tx_formatted": "6.94 MB"
                }
            }
        ],
        "total_traffic_formatted": "13.88 MB",
        "total_rx_formatted": "6.94 MB",
        "total_tx_formatted": "6.94 MB"
    },
    "timestamp": "2025-08-05T16:06:36.865907"
}
```

#### **Ответ `/api/traffic/daily/{date}`:**
```json
{
    "status": "success",
    "data": {
        "date": "2025-08-05",
        "total_bytes": 14548992,
        "total_connections": 43,
        "active_keys": 0,
        "total_sessions": 5,
        "total_formatted": "13.88 MB"
    },
    "timestamp": "2025-08-05T16:06:51.354243"
}
```

### 🔄 Обратная совместимость

- ✅ Все существующие методы продолжают работать
- ✅ Fallback к устаревшим эндпоинтам при ошибках
- ✅ Совместимость с существующим кодом
- ✅ Постепенная миграция на новые эндпоинты

### 🚀 Преимущества обновления

1. **📊 Детальная аналитика** - Полная история трафика с момента создания
2. **📈 Ежедневная статистика** - Анализ активности по дням
3. **🔧 Управление данными** - Сброс и очистка истории
4. **📱 Улучшенный мониторинг** - Более детальная информация о пользователях
5. **🛡️ Надежность** - Fallback механизмы для совместимости

### 📝 Миграция

#### **Для существующих интеграций:**
1. Новые методы доступны сразу
2. Старые методы продолжают работать
3. Постепенная миграция на новые эндпоинты
4. Обратная совместимость гарантирована

#### **Рекомендации:**
1. Начните использовать новые методы для новых функций
2. Постепенно мигрируйте существующий код
3. Используйте fallback механизмы для надежности
4. Обновите документацию и тесты

---

**Статус:** ✅ Актуально  
**Версия API:** 2.1.0  
**Дата:** 5 августа 2025 