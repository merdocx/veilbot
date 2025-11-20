# Примеры реализации улучшений отправки уведомлений

## Вариант 2: Retry механизм (быстрое решение)

### Изменения в `subscription_purchase_service.py`

```python
async def _send_notification(self, user_id: int, message: str, max_retries: int = 3) -> bool:
    """Отправить уведомление пользователю с retry механизмом"""
    import asyncio
    
    for attempt in range(max_retries):
        try:
            bot = get_bot_instance()
            if not bot:
                logger.warning(f"Bot instance not available for user {user_id}, attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Экспоненциальная задержка: 1s, 2s, 4s
                continue
            
            result = await safe_send_message(
                bot,
                user_id,
                message,
                reply_markup=get_main_menu(user_id),
                disable_web_page_preview=True,
                parse_mode="Markdown"
            )
            
            if result:
                logger.info(f"Notification sent to user {user_id} on attempt {attempt + 1}")
                return True
            else:
                logger.warning(f"Failed to send notification to user {user_id}, attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                
        except Exception as e:
            logger.error(f"Error sending notification to user {user_id}, attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
    
    logger.error(f"Failed to send notification to user {user_id} after {max_retries} attempts")
    return False
```

### Изменения в методе `process_subscription_purchase`

```python
# Шаг 8: Отправляем уведомление пользователю
subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_token}"
msg = (
    f"✅ *Подписка V2Ray успешно создана!*\n\n"
    f"🔗 *Ссылка подписки:*\n"
    f"`{subscription_url}`\n\n"
    f"⏳ *Срок действия:* {format_duration(tariff['duration_sec'])}\n\n"
    f"💡 *Как использовать:*\n"
    f"1. Откройте приложение V2Ray\n"
    f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
    f"3. Вставьте ссылку выше\n"
    f"4. Все серверы будут добавлены автоматически"
)

notification_sent = await self._send_notification(payment.user_id, msg)

# Шаг 9: Помечаем платеж как completed и удаляем флаг обработки
payment.mark_as_completed()
if payment.metadata:
    payment.metadata.pop('_processing_subscription', None)
    # Сохраняем информацию о статусе отправки уведомления
    if not notification_sent:
        payment.metadata['_notification_failed'] = True
        payment.metadata['_notification_retry_count'] = payment.metadata.get('_notification_retry_count', 0) + 1
await self.payment_repo.update(payment)

if not notification_sent:
    logger.warning(
        f"Subscription purchase completed but notification failed: payment={payment_id}, "
        f"user={payment.user_id}, subscription={subscription_id}"
    )
```

---

## Вариант 4: Комбинированный подход (полное решение)

### 1. Миграция БД: добавление поля `purchase_notification_sent`

```python
# В db.py или отдельном файле миграций
def add_purchase_notification_sent_field():
    """Добавить поле purchase_notification_sent в таблицу subscriptions"""
    conn = sqlite3.connect(DATABASE_PATH)
    c = conn.cursor()
    try:
        c.execute("""
            ALTER TABLE subscriptions 
            ADD COLUMN purchase_notification_sent INTEGER DEFAULT 0
        """)
        conn.commit()
        logger.info("Added purchase_notification_sent field to subscriptions table")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            logger.info("Column purchase_notification_sent already exists")
        else:
            raise
    finally:
        conn.close()
```

### 2. Обновление `SubscriptionRepository`

```python
def mark_purchase_notification_sent(self, subscription_id: int) -> None:
    """Пометить уведомление о покупке как отправленное"""
    with open_connection(self.db_path) as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE subscriptions SET purchase_notification_sent = 1 WHERE id = ?",
            (subscription_id,),
        )
        conn.commit()

def get_subscriptions_without_purchase_notification(self, limit: int = 50) -> List[Tuple]:
    """Получить подписки без отправленного уведомления о покупке"""
    with open_connection(self.db_path) as conn:
        c = conn.cursor()
        now = int(time.time())
        c.execute("""
            SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id
            FROM subscriptions
            WHERE purchase_notification_sent = 0 
              AND is_active = 1
              AND created_at > ?  -- Только недавно созданные (за последние 7 дней)
            ORDER BY created_at ASC
            LIMIT ?
        """, (now - 7 * 86400, limit))
        return c.fetchall()
```

### 3. Обновление `SubscriptionPurchaseService`

```python
# В методе process_subscription_purchase после создания подписки:

# Шаг 8: Отправляем уведомление пользователю
subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_token}"
msg = (
    f"✅ *Подписка V2Ray успешно создана!*\n\n"
    f"🔗 *Ссылка подписки:*\n"
    f"`{subscription_url}`\n\n"
    f"⏳ *Срок действия:* {format_duration(tariff['duration_sec'])}\n\n"
    f"💡 *Как использовать:*\n"
    f"1. Откройте приложение V2Ray\n"
    f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
    f"3. Вставьте ссылку выше\n"
    f"4. Все серверы будут добавлены автоматически"
)

notification_sent = await self._send_notification(payment.user_id, msg)

# Обновляем флаг отправки уведомления в подписке
if notification_sent:
    await self.subscription_repo.mark_purchase_notification_sent_async(subscription_id)
else:
    logger.warning(
        f"Failed to send purchase notification for subscription {subscription_id}, "
        f"user {payment.user_id}. Will retry via background task."
    )

# Шаг 9: Помечаем платеж как completed и удаляем флаг обработки
payment.mark_as_completed()
if payment.metadata:
    payment.metadata.pop('_processing_subscription', None)
await self.payment_repo.update(payment)
```

### 4. Фоновая задача для повторной отправки

```python
# В bot/services/background_tasks.py

async def retry_failed_subscription_notifications() -> None:
    """Повторная отправка уведомлений о покупке подписки"""
    
    async def job() -> None:
        bot = get_bot_instance()
        if not bot:
            logger.warning("Bot instance not available for retry_subscription_notifications")
            return
        
        subscription_repo = SubscriptionRepository()
        tariff_repo = TariffRepository()
        
        # Получаем подписки без отправленного уведомления
        subscriptions = subscription_repo.get_subscriptions_without_purchase_notification(limit=20)
        
        if not subscriptions:
            return
        
        logger.info(f"Found {len(subscriptions)} subscriptions without purchase notification")
        
        for sub_row in subscriptions:
            (
                sub_id, user_id, token, created_at, expires_at, tariff_id
            ) = sub_row
            
            try:
                # Получаем тариф
                tariff_row = tariff_repo.get_tariff(tariff_id)
                if not tariff_row:
                    logger.warning(f"Tariff {tariff_id} not found for subscription {sub_id}")
                    continue
                
                tariff = {
                    'id': tariff_row[0],
                    'name': tariff_row[1],
                    'duration_sec': tariff_row[2],
                    'price_rub': tariff_row[3],
                }
                
                # Формируем сообщение
                subscription_url = f"https://veil-bot.ru/api/subscription/{token}"
                msg = (
                    f"✅ *Подписка V2Ray успешно создана!*\n\n"
                    f"🔗 *Ссылка подписки:*\n"
                    f"`{subscription_url}`\n\n"
                    f"⏳ *Срок действия:* {format_duration(tariff['duration_sec'])}\n\n"
                    f"💡 *Как использовать:*\n"
                    f"1. Откройте приложение V2Ray\n"
                    f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
                    f"3. Вставьте ссылку выше\n"
                    f"4. Все серверы будут добавлены автоматически"
                )
                
                # Отправляем уведомление
                result = await safe_send_message(
                    bot,
                    user_id,
                    msg,
                    reply_markup=get_main_menu(user_id),
                    disable_web_page_preview=True,
                    parse_mode="Markdown"
                )
                
                if result:
                    subscription_repo.mark_purchase_notification_sent(sub_id)
                    logger.info(f"Successfully sent purchase notification for subscription {sub_id} to user {user_id}")
                else:
                    logger.warning(f"Failed to send purchase notification for subscription {sub_id} to user {user_id}")
                    
            except Exception as e:
                logger.error(f"Error retrying notification for subscription {sub_id}: {e}", exc_info=True)
    
    await _run_periodic(
        "retry_failed_subscription_notifications",
        interval_seconds=300,  # Каждые 5 минут
        job=job,
        max_backoff=1800,
    )
```

### 5. Регистрация задачи в `bot/main.py`

```python
from bot.services.background_tasks import (
    # ... существующие импорты ...
    retry_failed_subscription_notifications,
)

background_tasks = [
    # ... существующие задачи ...
    retry_failed_subscription_notifications(),
]
```

---

## Приоритет реализации

1. **Немедленно:** Вариант 2 (retry механизм) - быстрое улучшение без изменений БД
2. **В течение недели:** Вариант 4 (полное решение) - миграция БД + фоновая задача


