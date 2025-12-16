# Архитектура системы лимитов трафика для подписок V2Ray

## 1. Обзор

Подписка V2Ray содержит несколько ключей на разных серверах. Необходимо реализовать систему контроля общего трафика подписки с агрегацией данных со всех ключей.

**Ключевые принципы (актуальная модель):**
- Логика аналогична системе лимитов для отдельных ключей (`monitor_v2ray_traffic_limits`)
- Базовый лимит задаётся в тарифах (`tariffs.traffic_limit_mb`)
- Подписка может иметь собственный override‑лимит (`subscriptions.traffic_limit_mb`):
  - если `subscriptions.traffic_limit_mb` **не NULL**, используется он (0 = безлимит)
  - если `subscriptions.traffic_limit_mb` **NULL**, используется лимит из тарифа
- Если у пользователя есть активная подписка, на него распространяется только общий лимит подписки
- Уведомления о превышении лимита приходят только по подписке, не по отдельным ключам
- При наличии активной подписки отдельные ключи не проверяются на превышение лимита

## 2. Архитектура

### 2.1 Принцип работы

1. **Лимит берется из подписки или тарифа:**  
   - если в `subscriptions.traffic_limit_mb` задано значение (включая 0), оно используется как лимит подписки  
   - иначе лимит берётся из `tariffs.traffic_limit_mb`
2. **Агрегация трафика:** Суммируется `traffic_usage_bytes` всех ключей подписки
3. **Проверка лимита:** Сравнение агрегированного трафика с лимитом из тарифа
4. **Уведомления:** Только о превышении лимита подписки (не отдельных ключей)
5. **Отключение:** После grace period отключаются все ключи подписки

### 2.2 Отличия от системы ключей

- **Для ключей:** Каждый ключ имеет свой лимит и проверяется отдельно
- **Для подписок:** Один общий лимит на всю подписку, агрегация трафика всех ключей
- **Приоритет:** Если есть активная подписка, отдельные ключи не проверяются на лимит

### 3.1 Изменения в схеме БД

#### 3.1.1 Таблица `subscriptions`

Добавить поля для отслеживания превышения лимита (сам лимит берется из тарифа):

```sql
ALTER TABLE subscriptions ADD COLUMN traffic_usage_bytes INTEGER DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN traffic_over_limit_at INTEGER;
ALTER TABLE subscriptions ADD COLUMN traffic_over_limit_notified INTEGER DEFAULT 0;
```

**Поля:**
- `traffic_usage_bytes` - суммарное использование трафика всеми ключами подписки (агрегируется)
- `traffic_over_limit_at` - timestamp превышения лимита (аналогично `v2ray_keys.traffic_over_limit_at`)
- `traffic_over_limit_notified` - битовые флаги уведомлений (аналогично `v2ray_keys.traffic_over_limit_notified`)

**Важно:** Лимит НЕ хранится в `subscriptions`, берется из `tariffs.traffic_limit_mb` через `subscriptions.tariff_id`

#### 3.1.2 Таблица `subscription_traffic_snapshots`

Для отслеживания дельт трафика (аналогично `v2ray_usage_snapshots`):

```sql
CREATE TABLE IF NOT EXISTS subscription_traffic_snapshots (
    subscription_id INTEGER PRIMARY KEY,
    total_bytes INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT 0,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);
```

**Назначение:** Хранит последний известный суммарный трафик всех ключей подписки для расчета дельты (аналогично логике для ключей).

### 3.2 Логика подсчета трафика

#### 3.2.1 Агрегация трафика ключей

```python
def calculate_subscription_traffic(subscription_id: int) -> int:
    """
    Вычислить суммарный трафик всех ключей подписки
    
    Returns:
        Суммарный трафик в байтах
    """
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT COALESCE(SUM(traffic_usage_bytes), 0)
            FROM v2ray_keys
            WHERE subscription_id = ?
        """, (subscription_id,))
        return cursor.fetchone()[0] or 0
```

#### 3.2.2 Обновление трафика подписки

```python
def update_subscription_traffic(subscription_id: int, usage_bytes: int) -> None:
    """
    Обновить суммарный трафик подписки на основе всех ключей
    
    Args:
        subscription_id: ID подписки
        usage_bytes: Суммарный трафик в байтах
    """
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE subscriptions
            SET traffic_usage_bytes = ?,
                last_updated_at = ?
            WHERE id = ?
        """, (usage_bytes, int(time.time()), subscription_id))
```

#### 3.2.3 Получение лимита из тарифа

```python
def get_subscription_traffic_limit(subscription_id: int) -> int:
    """
    Получить лимит трафика подписки из тарифа
    
    Returns:
        Лимит в байтах (0 = безлимит)
    """
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT COALESCE(t.traffic_limit_mb, 0)
            FROM subscriptions s
            LEFT JOIN tariffs t ON s.tariff_id = t.id
            WHERE s.id = ?
        """, (subscription_id,))
        result = cursor.fetchone()
        if result and result[0]:
            return int(result[0]) * 1024 * 1024  # Конвертация МБ в байты
        return 0
```

### 3.3 Мониторинг и контроль

#### 3.3.1 Фоновая задача `monitor_subscription_traffic_limits()`

Аналогично `monitor_v2ray_traffic_limits()`, но для подписок. Использует ту же логику:
- Получение данных с серверов через V2Ray API
- Расчет дельт через snapshots
- Уведомления с теми же флагами
- Grace period 24 часа
- Отключение после grace period

**Ключевые отличия:**
- Агрегирует трафик всех ключей подписки
- Сравнивает с лимитом из тарифа (не из ключа)
- Отключает все ключи подписки при превышении

```python
async def monitor_subscription_traffic_limits() -> None:
    """
    Контроль превышения трафиковых лимитов для подписок V2Ray.
    
    Логика полностью аналогична monitor_v2ray_traffic_limits():
    1. Получить все активные подписки с лимитами трафика (из тарифов)
    2. Для каждой подписки:
       a. Получить все ключи подписки
       b. Агрегировать трафик всех ключей (сумма traffic_usage_bytes)
       c. Получить лимит из тарифа (tariffs.traffic_limit_mb)
       d. Обновить traffic_usage_bytes в subscriptions
       e. Проверить превышение лимита
       f. Отправить уведомления при необходимости (аналогично ключам)
       g. Отключить все ключи подписки после grace period
    """
    
    TRAFFIC_NOTIFY_WARNING = 1
    TRAFFIC_NOTIFY_DISABLED = 2
    TRAFFIC_DISABLE_GRACE = 86400  # 24 часа
    
    async def job() -> None:
        now = int(time.time())
        
        with get_db_cursor() as cursor:
            # Получить активные подписки с лимитами из тарифов
            cursor.execute("""
                SELECT 
                    s.id,
                    s.user_id,
                    s.traffic_usage_bytes,
                    s.traffic_over_limit_at,
                    s.traffic_over_limit_notified,
                    s.expires_at,
                    s.tariff_id,
                    COALESCE(t.traffic_limit_mb, 0) AS traffic_limit_mb,
                    t.name AS tariff_name
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                WHERE s.is_active = 1
                  AND s.expires_at > ?
                  AND COALESCE(t.traffic_limit_mb, 0) > 0
            """, (now,))
            
            subscriptions = cursor.fetchall()
            
            # Для каждой подписки агрегировать трафик ключей
            for sub in subscriptions:
                subscription_id, user_id, stored_usage, over_limit_at, notified_flags, expires_at, tariff_id, limit_mb, tariff_name = sub
                
                # Агрегировать трафик всех ключей подписки
                cursor.execute("""
                    SELECT COALESCE(SUM(traffic_usage_bytes), 0)
                    FROM v2ray_keys
                    WHERE subscription_id = ?
                """, (subscription_id,))
                
                total_usage = cursor.fetchone()[0] or 0
                
                # Обновить traffic_usage_bytes в подписке
                cursor.execute("""
                    UPDATE subscriptions
                    SET traffic_usage_bytes = ?,
                        last_updated_at = ?
                    WHERE id = ?
                """, (total_usage, now, subscription_id))
                
                # Проверить лимит (логика аналогична monitor_v2ray_traffic_limits)
                limit_bytes = int(limit_mb) * 1024 * 1024
                over_limit = total_usage > limit_bytes
                
                # ... остальная логика как в monitor_v2ray_traffic_limits()
    
    await _run_periodic(
        "monitor_subscription_traffic_limits",
        interval_seconds=600,  # 10 минут
        job=job,
        max_backoff=3600,
    )
```

#### 3.3.2 Отключение подписки при превышении

При превышении лимита (логика аналогична ключам):
1. Установить `traffic_over_limit_at = now` при первом превышении
2. Отправить предупреждение пользователю (флаг `TRAFFIC_NOTIFY_WARNING`)
3. После grace period (24 часа) отключить все ключи подписки через V2Ray API
4. Деактивировать подписку (`is_active = 0`)
5. Отправить уведомление об отключении (флаг `TRAFFIC_NOTIFY_DISABLED`)

**Важно:** Отключаются ВСЕ ключи подписки, а не отдельные ключи.

### 3.4 Интеграция с существующей системой

#### 3.4.1 Исключение ключей подписки из проверки

**КРИТИЧЕСКИЙ МОМЕНТ:** В `monitor_v2ray_traffic_limits()` нужно исключить ключи, которые принадлежат активным подпискам:

```python
# В monitor_v2ray_traffic_limits() изменить запрос:
cursor.execute("""
    SELECT 
        k.id,
        k.user_id,
        k.v2ray_uuid,
        k.server_id,
        COALESCE(k.traffic_limit_mb, 0) AS traffic_limit_mb,
        COALESCE(k.traffic_usage_bytes, 0) AS traffic_usage_bytes,
        k.traffic_over_limit_at,
        COALESCE(k.traffic_over_limit_notified, 0) AS traffic_over_limit_notified,
        k.expiry_at,
        k.subscription_id,
        IFNULL(s.api_url, '') AS api_url,
        IFNULL(s.api_key, '') AS api_key,
        IFNULL(t.name, '') AS tariff_name,
        IFNULL(k.email, '') AS email
    FROM v2ray_keys k
    JOIN servers s ON k.server_id = s.id
    LEFT JOIN tariffs t ON k.tariff_id = t.id
    WHERE k.expiry_at > ?
      AND COALESCE(k.traffic_limit_mb, 0) > 0
      -- ИСКЛЮЧИТЬ ключи, которые принадлежат активным подпискам
      AND (k.subscription_id IS NULL OR k.subscription_id NOT IN (
          SELECT id FROM subscriptions 
          WHERE is_active = 1 AND expires_at > ?
      ))
""", (now, now))
```

**Причина:** Если у пользователя есть активная подписка, лимиты отдельных ключей не проверяются - только общий лимит подписки.

#### 3.4.2 Обновление трафика подписки при изменении ключей

Трафик подписки обновляется автоматически при каждом запуске `monitor_subscription_traffic_limits()` через агрегацию. Дополнительная синхронизация не требуется, так как используется та же таблица `v2ray_keys.traffic_usage_bytes`.

#### 3.4.2 Отображение в интерфейсе

В команде `/my_keys` показывать:
- Общий трафик подписки
- Лимит подписки
- Процент использования
- Статус (активна/превышен лимит)

### 3.5 Периодические лимиты (опционально)

Если нужны периодические лимиты (например, месячные), можно добавить поле `traffic_reset_at`:

```python
# При создании/продлении подписки
traffic_reset_at = expires_at  # Сброс при истечении
# Или
traffic_reset_at = created_at + 30 * 24 * 3600  # Сброс каждые 30 дней
```

При проверке лимита:
```python
if traffic_reset_at and now >= traffic_reset_at:
    # Сбросить счетчик
    reset_subscription_traffic(subscription_id)
```

**Примечание:** Это опциональная функция, не обязательна для базовой реализации.

## 4. Важные моменты реализации

### 4.1 Приоритет подписки над ключами

**Правило:** Если у пользователя есть активная подписка, то:
- Отдельные ключи НЕ проверяются на превышение лимита
- Уведомления приходят только о превышении лимита подписки
- Лимит подписки применяется ко всем ключам подписки вместе

### 4.2 Обновление трафика

Трафик подписки обновляется:
- При каждом запуске `monitor_subscription_traffic_limits()` (каждые 10 минут)
- Путем агрегации `traffic_usage_bytes` всех ключей подписки
- Используются те же данные, что и для ключей (не дублирование)

### 4.3 Уведомления

Уведомления о превышении лимита:
- Отправляются только по подписке (не по отдельным ключам)
- Используют те же флаги, что и для ключей (`TRAFFIC_NOTIFY_WARNING`, `TRAFFIC_NOTIFY_DISABLED`)
- Текст уведомления указывает на подписку, а не на отдельный ключ

## 5. План реализации

### Этап 1: Миграция БД
1. Добавить поля в `subscriptions`:
   - `traffic_usage_bytes` (агрегированный трафик)
   - `traffic_over_limit_at` (время превышения)
   - `traffic_over_limit_notified` (флаги уведомлений)
2. Создать `subscription_traffic_snapshots` (для дельт)
3. **НЕ добавлять** `traffic_limit_mb` в `subscriptions` (берется из тарифа)

### Этап 2: Изменение `monitor_v2ray_traffic_limits()`
1. Исключить ключи с `subscription_id`, которые принадлежат активным подпискам
2. Это критично: иначе будут дублироваться проверки

### Этап 3: Базовые функции
1. `calculate_subscription_traffic()` - агрегация трафика всех ключей
2. `get_subscription_traffic_limit()` - получение лимита из тарифа
3. `update_subscription_traffic()` - обновление трафика подписки

### Этап 4: Мониторинг
1. `monitor_subscription_traffic_limits()` - фоновая задача (аналогично `monitor_v2ray_traffic_limits`)
2. Уведомления пользователям (только о подписке)
3. Отключение всех ключей подписки после grace period

### Этап 5: UI/UX
1. Отображение в `/my_keys` (трафик и лимит подписки)
2. Уведомления в боте (только о подписке)
3. Админ-панель для управления

## 6. Примеры использования

### 6.1 Создание подписки с лимитом

```python
subscription_id = await create_subscription(
    user_id=12345,
    tariff_id=5,  # Тариф с traffic_limit_mb = 1000
    duration_sec=30 * 24 * 3600
)

# Лимит автоматически берется из тарифа при проверке
# НЕ нужно сохранять лимит в subscriptions
```

### 6.2 Проверка лимита при запросе подписки

```python
async def generate_subscription_content(token: str) -> Optional[str]:
    subscription = await get_subscription_by_token(token)
    
    # Получить лимит из тарифа
    limit_mb = get_tariff_traffic_limit(subscription.tariff_id)
    
    # Агрегировать трафик всех ключей
    total_usage = calculate_subscription_traffic(subscription.id)
    
    # Обновить traffic_usage_bytes в подписке
    update_subscription_traffic(subscription.id, total_usage)
    
    # Проверить лимит
    if limit_mb > 0:
        limit_bytes = limit_mb * 1024 * 1024
        if total_usage > limit_bytes:
            # Проверить grace period
            if subscription.traffic_over_limit_at:
                grace_end = subscription.traffic_over_limit_at + 86400
                if time.time() > grace_end:
                    return None  # Подписка отключена
    
    # Генерировать контент подписки
    ...
```

## 7. Ключевые принципы (финальные)

1. **Лимиты в тарифах:** Лимит хранится только в `tariffs.traffic_limit_mb`, не дублируется в `subscriptions`
2. **Агрегация трафика:** Суммируется `traffic_usage_bytes` всех ключей подписки
3. **Приоритет подписки:** Если есть активная подписка, отдельные ключи не проверяются на лимит
4. **Уведомления:** Только о превышении лимита подписки, не отдельных ключей
5. **Логика как у ключей:** Используется та же система флагов, grace period, уведомлений
6. **Отключение:** Все ключи подписки отключаются вместе после grace period

## 8. Вопросы для обсуждения

1. **Периодичность обновления:** 10 минут достаточно (как у ключей)?
2. **Grace period:** Оставить 24 часа (как у ключей)?
3. **Продление:** Сбрасывать лимит при продлении подписки или накапливать?
4. **Уведомления:** На каких порогах отправлять (50%, 80%, 90%, 100%)?

