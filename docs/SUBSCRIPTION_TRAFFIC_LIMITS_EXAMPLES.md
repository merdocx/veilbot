# Примеры реализации системы лимитов трафика для подписок

## 1. Схема данных

```
subscriptions
├── id
├── user_id
├── tariff_id                 ← Лимит берется из тарифа (tariffs.traffic_limit_mb)
├── traffic_usage_bytes       ← Сумма всех ключей (агрегируется)
├── traffic_over_limit_at     ← Когда превышен
└── traffic_over_limit_notified

tariffs
├── id
└── traffic_limit_mb          ← Лимит хранится здесь

v2ray_keys
├── id
├── subscription_id           ← Связь с подпиской
├── traffic_usage_bytes       ← Трафик конкретного ключа
└── ...

subscription_traffic_snapshots
├── subscription_id
├── total_bytes               ← Последняя известная сумма
└── updated_at
```

## 2. Примеры кода

### 2.1 Репозиторий: добавление методов

```python
# app/repositories/subscription_repository.py

class SubscriptionRepository:
    def update_subscription_traffic(self, subscription_id: int, usage_bytes: int) -> None:
        """Обновить трафик подписки"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            c.execute("""
                UPDATE subscriptions
                SET traffic_usage_bytes = ?,
                    last_updated_at = ?
                WHERE id = ?
            """, (usage_bytes, now, subscription_id))
            conn.commit()
    
    def get_subscription_traffic_sum(self, subscription_id: int) -> int:
        """Получить суммарный трафик всех ключей подписки"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT COALESCE(SUM(traffic_usage_bytes), 0)
                FROM v2ray_keys
                WHERE subscription_id = ?
            """, (subscription_id,))
            result = c.fetchone()
            return int(result[0] or 0) if result else 0
    
    def get_subscription_traffic_limit(self, subscription_id: int) -> int:
        """Получить лимит трафика подписки из тарифа (в байтах)"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT COALESCE(t.traffic_limit_mb, 0)
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                WHERE s.id = ?
            """, (subscription_id,))
            result = c.fetchone()
            if result and result[0]:
                return int(result[0]) * 1024 * 1024  # Конвертация МБ в байты
            return 0
    
    def get_subscriptions_with_traffic_limits(self, now: int) -> List[Tuple]:
        """Получить активные подписки с лимитами трафика из тарифов"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
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
            return c.fetchall()
```

### 2.2 Фоновая задача мониторинга

```python
# bot/services/background_tasks.py

TRAFFIC_NOTIFY_WARNING = 1  # Предупреждение о превышении
TRAFFIC_NOTIFY_DISABLED = 2  # Уведомление об отключении
TRAFFIC_DISABLE_GRACE = 86400  # 24 часа grace period

async def monitor_subscription_traffic_limits() -> None:
    """Контроль превышения трафиковых лимитов для подписок V2Ray."""
    
    async def job() -> None:
        now = int(time.time())
        repo = SubscriptionRepository()
        
        # Получить активные подписки с лимитами
        subscriptions = repo.get_subscriptions_with_traffic_limits(now)
        
        warn_notifications = []
        disable_notifications = []
        updates = []
        
        for sub in subscriptions:
            subscription_id, user_id, usage_bytes, over_limit_at, notified_flags, expires_at, tariff_id, limit_mb, tariff_name = sub
            
            # Агрегировать трафик всех ключей подписки
            total_usage = repo.get_subscription_traffic_sum(subscription_id)
            
            # Обновить usage_bytes в подписке
            repo.update_subscription_traffic(subscription_id, total_usage)
            
            # Получить лимит из тарифа
            limit_bytes = int(limit_mb) * 1024 * 1024 if limit_mb else 0
            over_limit = limit_bytes > 0 and total_usage > limit_bytes
            
            new_over_limit_at = over_limit_at
            new_notified_flags = notified_flags or 0
            
            if over_limit:
                if not new_over_limit_at:
                    new_over_limit_at = now
                
                # Отправить предупреждение
                if not (new_notified_flags & TRAFFIC_NOTIFY_WARNING):
                    limit_display = _format_bytes_short(limit_bytes)
                    usage_display = _format_bytes_short(total_usage)
                    deadline_ts = new_over_limit_at + TRAFFIC_DISABLE_GRACE
                    remaining = max(0, deadline_ts - now)
                    
                    message = (
                        "⚠️ Превышен лимит трафика для вашей подписки V2Ray.\n"
                        f"Тариф: {tariff_name or 'V2Ray'}\n"
                        f"Израсходовано: {usage_display} из {limit_display}.\n"
                        f"Подписка будет отключена через {format_duration(remaining)}.\n"
                        "Продлите доступ, чтобы сбросить лимит."
                    )
                    warn_notifications.append((user_id, message))
                    new_notified_flags |= TRAFFIC_NOTIFY_WARNING
                
                # Отключить подписку после grace period
                disable_deadline = new_over_limit_at + TRAFFIC_DISABLE_GRACE
                if now >= disable_deadline and not (new_notified_flags & TRAFFIC_NOTIFY_DISABLED):
                    # Отключить все ключи подписки
                    await disable_subscription_keys(subscription_id)
                    
                    # Деактивировать подписку
                    repo.deactivate_subscription(subscription_id)
                    
                    message = (
                        "❌ Ваша подписка V2Ray отключена из-за превышения лимита трафика.\n"
                        f"Тариф: {tariff_name or 'V2Ray'}\n"
                        "Продлите доступ, чтобы восстановить подписку."
                    )
                    disable_notifications.append((user_id, message))
                    new_notified_flags |= TRAFFIC_NOTIFY_DISABLED
            
            # Сохранить обновления
            updates.append((
                new_over_limit_at,
                new_notified_flags,
                subscription_id
            ))
        
        # Обновить БД
        if updates:
            with get_db_cursor(commit=True) as cursor:
                cursor.executemany("""
                    UPDATE subscriptions
                    SET traffic_over_limit_at = ?,
                        traffic_over_limit_notified = ?
                    WHERE id = ?
                """, updates)
        
        # Отправить уведомления
        bot = get_bot_instance()
        if bot:
            for user_id, message in warn_notifications + disable_notifications:
                await safe_send_message(
                    bot, user_id, message,
                    reply_markup=get_main_menu(user_id),
                    parse_mode="Markdown"
                )
    
    await _run_periodic(
        "monitor_subscription_traffic_limits",
        interval_seconds=600,  # 10 минут
        job=job,
        max_backoff=3600,
    )

async def disable_subscription_keys(subscription_id: int) -> None:
    """Отключить все ключи подписки"""
    repo = SubscriptionRepository()
    keys = repo.get_subscription_keys_for_deletion(subscription_id)
    
    for v2ray_uuid, api_url, api_key in keys:
        if v2ray_uuid and api_url and api_key:
            try:
                from vpn_protocols import V2RayProtocol
                protocol = V2RayProtocol(api_url, api_key)
                await protocol.delete_user(v2ray_uuid)
                await protocol.close()
            except Exception as e:
                logger.error(f"Failed to disable key {v2ray_uuid}: {e}")
```

### 2.3 Исключение ключей подписки из проверки

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

### 2.4 Проверка лимита при генерации подписки

```python
# bot/services/subscription_service.py

async def generate_subscription_content(self, token: str) -> Optional[str]:
    """Генерировать содержимое подписки с проверкой лимита"""
    
    subscription = await self.repository.get_subscription_by_token_async(token)
    if not subscription:
        return None
    
    subscription_id, user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified = subscription
    
    now = int(time.time())
    
    # Проверка активности
    if not is_active or expires_at <= now:
        return None
    
    # Обновить трафик подписки
    repo = SubscriptionRepository()
    total_usage = repo.get_subscription_traffic_sum(subscription_id)
    repo.update_subscription_traffic(subscription_id, total_usage)
    
    # Проверить лимит трафика
    cursor.execute("""
        SELECT traffic_limit_mb, traffic_over_limit_at
        FROM subscriptions
        WHERE id = ?
    """, (subscription_id,))
    limit_row = cursor.fetchone()
    
    if limit_row and limit_row[0] and limit_row[0] > 0:
        limit_bytes = limit_row[0] * 1024 * 1024
        over_limit_at = limit_row[1]
        
        if total_usage > limit_bytes:
            # Проверить grace period
            if over_limit_at:
                grace_end = over_limit_at + 86400  # 24 часа
                if now > grace_end:
                    logger.warning(f"Subscription {subscription_id} disabled due to traffic limit")
                    return None
    
    # Генерировать контент подписки
    # ... (существующий код)
```

### 2.5 Отображение в /my_keys

```python
# bot/handlers/keys.py

async def handle_my_keys_btn(message: types.Message):
    user_id = message.from_user.id
    
    # Получить подписку
    repo = SubscriptionRepository()
    subscription = repo.get_active_subscription(user_id)
    
    msg = ""
    
    if subscription:
        sub_id, user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified = subscription
        
        # Получить трафик подписки
        total_usage = repo.get_subscription_traffic_sum(sub_id)
        repo.update_subscription_traffic(sub_id, total_usage)
        
        # Получить лимит
        cursor.execute("SELECT traffic_limit_mb FROM subscriptions WHERE id = ?", (sub_id,))
        limit_row = cursor.fetchone()
        limit_mb = limit_row[0] if limit_row else 0
        
        # Форматировать информацию
        usage_str = _format_bytes(total_usage)
        limit_str = f"{limit_mb} ГБ" if limit_mb > 0 else "без ограничений"
        
        if limit_mb > 0:
            usage_percent = (total_usage / (limit_mb * 1024 * 1024)) * 100
            usage_str += f" ({usage_percent:.1f}%)"
        
        msg += (
            f"📋 *Ваша подписка V2Ray:*\n"
            f"🔗 https://veil-bot.ru/api/subscription/{token}\n"
            f"⏳ Осталось времени: {format_duration(expires_at - now)}\n"
            f"📊 Трафик: {usage_str} из {limit_str}\n\n"
        )
    
    # ... остальной код для отдельных ключей
```

## 3. Миграция БД

```python
# db.py или отдельный файл миграций

def migrate_add_traffic_limits_to_subscriptions():
    """Добавить поля для отслеживания трафика в subscriptions"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        # Проверяем существующие колонки
        cursor.execute("PRAGMA table_info(subscriptions)")
        columns = {row[1] for row in cursor.fetchall()}
        
        # НЕ добавляем traffic_limit_mb - лимит берется из тарифа!
        
        if 'traffic_usage_bytes' not in columns:
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN traffic_usage_bytes INTEGER DEFAULT 0")
        
        if 'traffic_over_limit_at' not in columns:
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN traffic_over_limit_at INTEGER")
        
        if 'traffic_over_limit_notified' not in columns:
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN traffic_over_limit_notified INTEGER DEFAULT 0")
        
        # Создать таблицу snapshots (для дельт, как у ключей)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscription_traffic_snapshots (
                subscription_id INTEGER PRIMARY KEY,
                total_bytes INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0,
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
            )
        """)
        
        conn.commit()
        logging.info("Migration: Added traffic tracking fields to subscriptions")
        
    except Exception as e:
        logging.error(f"Migration error: {e}")
        conn.rollback()
    finally:
        conn.close()
```

## 4. Инициализация при создании подписки

```python
# bot/services/subscription_service.py

async def create_subscription(...):
    # ... существующий код ...
    
    # После создания подписки
    subscription_id = await self.repository.create_subscription_async(...)
    
    # Лимит НЕ сохраняется в subscriptions - берется из тарифа при проверке
    # Никаких дополнительных действий не требуется
```

## 5. Тестирование

```python
# tests/test_subscription_traffic.py

async def test_subscription_traffic_aggregation():
    """Тест агрегации трафика подписки"""
    repo = SubscriptionRepository()
    
    # Создать подписку
    subscription_id = repo.create_subscription(
        user_id=12345,
        subscription_token="test-token",
        expires_at=int(time.time()) + 86400,
        tariff_id=1  # Тариф с лимитом 1000 МБ
    )
    
    # Создать ключи с трафиком
    create_key_with_traffic(subscription_id, 500 * 1024 * 1024)  # 500 МБ
    create_key_with_traffic(subscription_id, 300 * 1024 * 1024)  # 300 МБ
    
    # Проверить агрегацию
    total = repo.get_subscription_traffic_sum(subscription_id)
    assert total == 800 * 1024 * 1024  # 800 МБ
    
    # Проверить лимит
    subscription = repo.get_subscription_by_id(subscription_id)
    assert subscription.traffic_limit_mb == 1000
    assert subscription.traffic_usage_bytes == 800 * 1024 * 1024
```

