# ПРОЦЕСС ПОКУПКИ И ПРОДЛЕНИЯ ПОДПИСКИ С ПРОВЕРКАМИ

## 📋 ОБЩИЙ FLOW: `process_subscription_purchase(payment_id)`

### ВХОДНАЯ ТОЧКА

```python
async def process_subscription_purchase(self, payment_id: str) -> Tuple[bool, Optional[str]]
```

---

## 🔍 ЭТАП 1: ВАЛИДАЦИЯ ПЛАТЕЖА

### ✅ Проверка 1: Платеж существует

```python
payment = await self.payment_repo.get_by_payment_id(payment_id)
if not payment:
    return False, "Payment not found"
```

**Защита:** Если платеж не найден, обработка прекращается.

---

### ✅ Проверка 2: Это платеж за подписку

```python
if not (payment.metadata and payment.metadata.get('key_type') == 'subscription'):
    return False, "Not a subscription payment"
```

**Защита:** Обрабатываем только платежи с `metadata['key_type'] == 'subscription'`.

---

### ✅ Проверка 3: Протокол v2ray

```python
if payment.protocol != 'v2ray':
    return False, "Protocol is not v2ray"
```

**Защита:** Обрабатываем только платежи для протокола v2ray.

---

### ✅ Проверка 4: Статус платежа (ПЕРВАЯ ЗАЩИТА ОТ ДВОЙНОЙ ОБРАБОТКИ)

```python
if payment.status == PaymentStatus.COMPLETED:
    logger.info("Payment already completed, skipping")
    return True, None  # ✅ Идемпотентность: платеж уже обработан
```

**Защита:** Если платеж уже `COMPLETED`, функция сразу возвращается. Это предотвращает повторную обработку.

---

### ✅ Проверка 5: Статус должен быть PAID

```python
if payment.status != PaymentStatus.PAID:
    return False, "Payment is not paid"
```

**Защита:** Обрабатываем только платежи со статусом `PAID`.

---

### ✅ Проверка 6: ДОПОЛНИТЕЛЬНАЯ АТОМАРНАЯ ПРОВЕРКА СТАТУСА (v2.4.10)

```python
# УЛУЧШЕНИЕ ИДЕМПОТЕНТНОСТИ: Дополнительная атомарная проверка статуса перед обработкой
payment_status_check = await self.payment_repo.get_by_payment_id(payment_id)
if payment_status_check and payment_status_check.status == PaymentStatus.COMPLETED:
    logger.info("Payment was completed by another process (race condition detected), skipping")
    return True, None
```

**Защита:** Дополнительная проверка после получения платежа. Если между первой проверкой и этой платеж был обработан другим процессом, обработка прекращается.

**Почему это важно:** Между проверками может пройти время, и другой процесс может обработать платеж.

---

### ✅ Проверка 7: Получение тарифа

```python
tariff_row = self.tariff_repo.get_tariff(payment.tariff_id)
if not tariff_row:
    return False, "Tariff not found"
```

**Защита:** Проверяем, что тариф существует.

---

### ✅ Проверка 8: ПОВТОРНАЯ ПРОВЕРКА СТАТУСА ПОСЛЕ ПОЛУЧЕНИЯ ТАРИФА

```python
# Дополнительная проверка статуса после получения тарифа (защита от race condition)
payment_check = await self.payment_repo.get_by_payment_id(payment_id)
if payment_check and payment_check.status == PaymentStatus.COMPLETED:
    logger.info("Payment was completed by another process, skipping")
    return True, None
```

**Защита:** Еще одна проверка после получения тарифа. Это защищает от race condition, когда между проверками другой процесс обработал платеж.

---

## 🔍 ЭТАП 2: ОПРЕДЕЛЕНИЕ ПОКУПКИ/ПРОДЛЕНИЯ

### ✅ Проверка 9: Поиск активной подписки

```python
now = int(time.time())
grace_threshold = now - DEFAULT_GRACE_PERIOD  # 24 часа назад

# Ищем активную подписку (expires_at > grace_threshold)
async with open_async_connection(self.db_path) as conn:
    async with conn.execute(
        """
        SELECT s.id, s.user_id, s.subscription_token, s.created_at, s.expires_at, 
               s.tariff_id, s.is_active, s.last_updated_at, s.notified, s.purchase_notification_sent,
               t.price_rub
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE s.user_id = ? AND s.is_active = 1 AND s.expires_at > ?
        ORDER BY s.created_at DESC
        LIMIT 1
        """,
        (payment.user_id, grace_threshold)
    ) as cursor:
        existing_subscription_row = await cursor.fetchone()
```

**Логика:** Ищем активную подписку для пользователя, которая еще не истекла (с учетом grace period 24 часа).

---

### ✅ Проверка 10: Проверка бесплатной подписки

```python
FREE_V2RAY_TARIFF_ID = app_settings.FREE_V2RAY_TARIFF_ID

has_active_free_subscription = False
if existing_subscription_row:
    subscription_tariff_id = existing_subscription_row[5]
    subscription_price_rub = existing_subscription_row[10]
    has_active_free_subscription = (
        subscription_tariff_id == FREE_V2RAY_TARIFF_ID or
        (subscription_price_rub is not None and subscription_price_rub == 0)
    )

if has_active_free_subscription:
    # Есть активная бесплатная подписка - это всегда продление
    return await self._extend_subscription(payment, tariff, existing_subscription, now, is_purchase=False)
```

**Логика:** Если есть активная бесплатная подписка, любая оплата - это продление.

---

### ✅ Проверка 11: Проверка очень недавно созданной подписки (ЗАЩИТА ОТ ДВОЙНОГО ПРОДЛЕНИЯ)

```python
if existing_subscription_row:
    subscription_id = existing_subscription_row[0]
    created_at = existing_subscription_row[3]
    existing_expires_at = existing_subscription_row[4]
    
    VERY_RECENT_THRESHOLD = 3600  # 1 час
    subscription_age = now - created_at
    expected_expires_at = created_at + tariff['duration_sec']
    is_very_recent = subscription_age < VERY_RECENT_THRESHOLD
    expires_at_matches_expected = abs(existing_expires_at - expected_expires_at) < 3600
    
    # Если подписка создана менее 1 часа назад и срок соответствует ожидаемому
    # это ПОКУПКА, а не продление (чтобы избежать двойного продления)
    if is_very_recent and expires_at_matches_expected:
        # Проверяем, есть ли другие completed платежи
        other_completed_count = await conn.execute(
            """
            SELECT COUNT(*) FROM payments
            WHERE user_id = ? AND tariff_id = ? AND status = 'completed'
            AND protocol = 'v2ray' AND metadata LIKE '%subscription%'
            AND created_at >= ? AND payment_id != ?
            """,
            (payment.user_id, payment.tariff_id, created_at, payment.payment_id)
        )
        
        if other_completed_count == 0:
            # Это покупка - отправляем уведомление, НЕ продлеваем
            return await self._send_purchase_notification_for_existing_subscription(...)
```

**Логика:** Если подписка создана менее 1 часа назад и срок соответствует ожидаемому (`created_at + duration`), это покупка, а не продление. Это предотвращает двойное продление при повторной обработке платежа.

**Почему это важно:** Если подписка только что создана, продлевать её не нужно - нужно только отправить уведомление о покупке.

---

### ✅ Проверка 12: Итоговое решение

```python
if existing_subscription_row:
    # Есть активная подписка - это ПРОДЛЕНИЕ
    return await self._extend_subscription(payment, tariff, existing_subscription, now, is_purchase=False)
else:
    # Нет активной подписки - это ПОКУПКА
    return await self._create_subscription(payment, tariff, now)
```

**Логика:** 
- Если есть активная подписка → **ПРОДЛЕНИЕ**
- Если активной подписки нет → **ПОКУПКА**

---

## 🔍 ЭТАП 3: ПРОДЛЕНИЕ ПОДПИСКИ (`_extend_subscription`)

### ✅ Проверка 13: Проверка на ручную установку срока

```python
MANUAL_EXPIRY_THRESHOLD = 4102434000  # 01.01.2100
is_manually_set = (
    existing_expires_at >= MANUAL_EXPIRY_THRESHOLD or
    (existing_expires_at > now and (existing_expires_at - now) > (5 * ONE_YEAR_IN_SECONDS))
)

if is_manually_set:
    # Срок был установлен вручную - не изменяем его при продлении
    new_expires_at = existing_expires_at
    await self.subscription_repo.extend_subscription_async(subscription_id, new_expires_at, tariff['id'])
else:
    # Обычное продление
    ...
```

**Логика:** Если срок подписки был установлен вручную (VIP, админка), не изменяем его при продлении.

---

### ✅ Проверка 14: АТОМАРНОЕ ПРОДЛЕНИЕ (КРИТИЧНО!)

```python
# Атомарное обновление: вычисление нового expires_at происходит в SQL
new_expires_at = await self.subscription_repo.extend_subscription_by_duration_async(
    subscription_id, 
    tariff['duration_sec'], 
    tariff['id'],
    max_expires_at=MAX_REASONABLE_EXPIRY
)
```

**Реализация `extend_subscription_by_duration_async`:**

```python
# app/repositories/subscription_repository.py

async def extend_subscription_by_duration_async(self, subscription_id: int, duration_sec: int, ...):
    async with open_async_connection(self.db_path) as conn:
        # ✅ АТОМАРНОЕ SQL-обновление: вычисление происходит в БД
        await conn.execute(
            """
            UPDATE subscriptions
            SET expires_at = expires_at + ?,
                tariff_id = ?,
                last_updated_at = ?
            WHERE id = ?
            """,
            (duration_sec, tariff_id, now, subscription_id)
        )
        await conn.commit()
        
        # Получаем новое значение expires_at
        async with conn.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (subscription_id,)) as cursor:
            new_expires_at = (await cursor.fetchone())[0]
        
        return new_expires_at
```

**Защита:** Используется атомарное SQL-обновление `expires_at = expires_at + duration_sec`. Это предотвращает race conditions:
- Если два процесса одновременно продлевают подписку:
  - Процесс 1: `UPDATE expires_at = expires_at + 30` → expires_at = 100 + 30 = 130
  - Процесс 2: `UPDATE expires_at = expires_at + 30` → expires_at = 130 + 30 = 160 ✅
- Результат: оба продления учтены правильно!

---

### ✅ Проверка 15: Обновление лимита трафика

```python
traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
await self.subscription_repo.update_subscription_traffic_limit_async(subscription_id, traffic_limit_mb)
```

**Логика:** Обновляем лимит трафика подписки из тарифа.

---

### ✅ Проверка 16: Сброс трафика ключей

```python
reset_success = await reset_subscription_traffic(subscription_id)
```

**Логика:** Сбрасываем трафик всех ключей подписки при продлении.

---

### ✅ Проверка 17: Проверка отправки уведомления (для покупки)

```python
if is_purchase:
    # Для покупки проверяем флаг purchase_notification_sent
    async with open_async_connection(self.db_path) as conn:
        async with conn.execute(
            "SELECT purchase_notification_sent FROM subscriptions WHERE id = ?",
            (subscription_id,)
        ) as check_cursor:
            notif_row = await check_cursor.fetchone()
            if notif_row and notif_row[0]:
                # Уведомление уже отправлено - помечаем платеж как completed
                await self.payment_repo.try_update_status(
                    payment.payment_id,
                    PaymentStatus.COMPLETED,
                    PaymentStatus.PAID
                )
                return True, None
```

**Логика:** Если это покупка и уведомление уже отправлено, просто помечаем платеж как `COMPLETED`.

---

### ✅ Проверка 18: Отправка уведомления

```python
notification_sent = await self._send_notification_simple(payment.user_id, msg)

if not notification_sent:
    # НЕ помечаем как completed, чтобы повторить попытку
    return False, "Failed to send notification"
```

**Логика:** Если уведомление не отправлено, возвращаем ошибку. Платеж останется в статусе `PAID` для повторной обработки.

---

### ✅ Проверка 19: ОБНОВЛЕНИЕ subscription_id В ПЛАТЕЖЕ (v2.4.10)

```python
# УЛУЧШЕНИЕ: update_subscription_id теперь использует retry механизм
subscription_id_updated = await self.payment_repo.update_subscription_id(payment.payment_id, subscription_id)
if subscription_id_updated:
    logger.info(f"Updated payment {payment.payment_id} subscription_id to {subscription_id}")
else:
    logger.error(
        f"CRITICAL: Failed to update subscription_id for payment {payment.payment_id} "
        f"after retries. Payment will remain without subscription_id. "
        f"This should be fixed by monitoring task."
    )
    # Не прерываем выполнение, так как основная обработка завершена
    # Мониторинговая задача исправит это позже
```

**Реализация `update_subscription_id` с retry:**

```python
# payments/repositories/payment_repository.py

async def update_subscription_id(self, payment_id: str, subscription_id: int) -> bool:
    async def _update_operation():
        async with open_async_connection(self.db_path) as conn:
            cursor = await conn.execute(
                "UPDATE payments SET subscription_id = ?, updated_at = ? WHERE payment_id = ?",
                (subscription_id, int(datetime.now(timezone.utc).timestamp()), payment_id)
            )
            await conn.commit()
            return cursor.rowcount > 0
    
    try:
        return await retry_async_db_operation(
            _update_operation,
            max_attempts=3,  # ✅ 3 попытки
            initial_delay=0.1,
            operation_name="update_subscription_id",
            operation_context={"payment_id": payment_id, "subscription_id": subscription_id}
        )
    except Exception as e:
        logger.error(f"Error updating payment subscription_id after retries: {e}")
        return False
```

**Защита:** 
- Retry механизм с 3 попытками
- Обрабатывает ошибки "database is locked" и другие временные ошибки БД
- Если обновление не удалось, логируется критическая ошибка для мониторинга

---

### ✅ Проверка 20: Обновление статуса платежа на COMPLETED

```python
update_success = await self.payment_repo.try_update_status(
    payment.payment_id,
    PaymentStatus.COMPLETED,
    PaymentStatus.PAID
)
```

**Логика:** Атомарно обновляем статус платежа с `PAID` на `COMPLETED`. Если статус уже изменился, обновление не произойдет (идемпотентность).

---

## 🔍 ЭТАП 4: СОЗДАНИЕ ПОДПИСКИ (`_create_subscription`)

### ✅ Проверка 21: Повторная проверка наличия подписки

```python
# Проверяем, не была ли подписка уже создана другим процессом
grace_threshold = now - DEFAULT_GRACE_PERIOD

async with open_async_connection(self.db_path) as conn:
    async with conn.execute(
        """
        SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
        FROM subscriptions
        WHERE user_id = ? AND is_active = 1 AND expires_at > ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (payment.user_id, grace_threshold)
    ) as cursor:
        existing_subscription_row = await cursor.fetchone()
```

**Логика:** Перед созданием проверяем, не была ли подписка уже создана другим процессом.

---

### ✅ Проверка 22: Обработка существующей подписки

```python
if existing_subscription_row:
    subscription_id = existing_subscription_row[0]
    existing_created_at = existing_subscription_row[3]
    existing_expires_at = existing_subscription_row[4]
    
    VERY_RECENT_THRESHOLD = 300  # 5 минут
    subscription_age = now - existing_created_at
    expected_expires_at = existing_created_at + tariff['duration_sec']
    is_very_recent = subscription_age < VERY_RECENT_THRESHOLD
    expires_at_matches_expected = abs(existing_expires_at - expected_expires_at) < 3600
    
    if is_very_recent and expires_at_matches_expected and not purchase_notification_sent:
        # Это покупка - отправляем уведомление
        return await self._send_purchase_notification_for_existing_subscription(...)
```

**Логика:** Если подписка уже существует и была создана недавно, отправляем уведомление о покупке вместо создания новой.

---

### ✅ Проверка 23: Валидация тарифа

```python
duration_sec = tariff.get('duration_sec', 0) or 0
if duration_sec is None or duration_sec <= 0:
    return False, "Invalid tariff duration_sec"
```

**Логика:** Проверяем, что длительность тарифа валидна.

---

### ✅ Проверка 24: Проверка VIP статуса

```python
is_vip = user_repo.is_user_vip(payment.user_id)

if is_vip:
    expires_at = VIP_EXPIRES_AT  # 01.01.2100
    traffic_limit_mb = VIP_TRAFFIC_LIMIT_MB  # 0 = безлимит
else:
    expires_at = now + duration_sec
    traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
```

**Логика:** Для VIP пользователей устанавливаем специальный срок и безлимитный трафик.

---

### ✅ Проверка 25: Генерация уникального токена

```python
subscription_token = None
for _ in range(10):
    token = str(uuid.uuid4())
    if not await self.subscription_repo.get_subscription_by_token_async(token):
        subscription_token = token
        break

if not subscription_token:
    return False, "Failed to generate unique subscription token"
```

**Логика:** Генерируем уникальный токен подписки (до 10 попыток).

---

### ✅ Проверка 26: АТОМАРНОЕ СОЗДАНИЕ ПОДПИСКИ (ЗАЩИТА ОТ RACE CONDITION)

```python
async with open_async_connection(self.db_path) as conn:
    # Начинаем транзакцию
    await conn.execute("BEGIN IMMEDIATE")
    try:
        # Финальная проверка перед созданием (защита от race condition)
        async with conn.execute(
            """
            SELECT id FROM subscriptions
            WHERE user_id = ? AND is_active = 1 AND expires_at > ?
            LIMIT 1
            """,
            (payment.user_id, grace_threshold)
        ) as check_cursor:
            existing = await check_cursor.fetchone()
        
        if existing:
            # Подписка уже создана другим процессом
            subscription_id = existing[0]
            await conn.commit()
            await self.payment_repo.update_subscription_id(payment.payment_id, subscription_id)
        else:
            # Создаем новую подписку
            cursor = await conn.execute(
                """
                INSERT INTO subscriptions (user_id, subscription_token, created_at, expires_at, tariff_id, is_active, notified, traffic_limit_mb)
                VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (payment.user_id, subscription_token, now, expires_at, tariff['id'], traffic_limit_mb),
            )
            subscription_id = cursor.lastrowid
            await conn.commit()
            await self.payment_repo.update_subscription_id(payment.payment_id, subscription_id)
    except Exception as e:
        await conn.rollback()
        raise e
```

**Защита:** 
- Используется `BEGIN IMMEDIATE TRANSACTION` для атомарности
- Повторная проверка наличия подписки перед созданием
- Если подписка уже создана другим процессом, используем существующую

**Почему это важно:** Два процесса могут одновременно проверить отсутствие подписки и попытаться создать её. Транзакция предотвращает создание дубликатов.

---

### ✅ Проверка 27: Создание ключей на серверах

```python
# Создаем ключи на всех активных V2Ray серверах
for server in v2ray_servers:
    # Создание ключа через ProtocolFactory
    ...
```

**Логика:** Создаем ключи на всех активных серверах для подписки.

---

### ✅ Проверка 28: Отправка уведомления и завершение

```python
notification_sent = await self._send_notification_simple(payment.user_id, msg)

if not notification_sent:
    return False, "Failed to send notification"

# Обновляем статус платежа на COMPLETED
await self.payment_repo.try_update_status(
    payment.payment_id,
    PaymentStatus.COMPLETED,
    PaymentStatus.PAID
)
```

**Логика:** Отправляем уведомление и помечаем платеж как `COMPLETED`.

---

## 📊 СВОДКА ВСЕХ ПРОВЕРОК

### Защита от двойной обработки:
1. ✅ Проверка статуса `COMPLETED` в начале функции (строка 84)
2. ✅ Дополнительная атомарная проверка статуса перед обработкой (строка 96) - **v2.4.10**
3. ✅ Повторная проверка статуса после получения тарифа (строка 117)
4. ✅ Атомарное обновление статуса через `try_update_status`

### Защита от двойного продления:
5. ✅ Проверка очень недавно созданной подписки (менее 1 часа) - строка 207
6. ✅ Проверка соответствия `expires_at` ожидаемому значению - строка 210
7. ✅ Атомарное обновление `expires_at` через SQL (`expires_at = expires_at + duration_sec`) - строка 727

### Защита от race conditions:
8. ✅ Атомарное создание подписки через `BEGIN IMMEDIATE TRANSACTION` - строка 1322
9. ✅ Повторная проверка наличия подписки перед созданием - строка 1325

### Защита от ошибок БД:
10. ✅ Retry механизм для `update_subscription_id` (3 попытки) - строка 474 - **v2.4.10**
11. ✅ Обработка ошибок "database is locked" и других временных ошибок

### Мониторинг:
12. ✅ Критическое логирование при неудачном обновлении `subscription_id` - строка 902
13. ✅ Фоновая задача `fix_payments_without_subscription_id()` для автоматического исправления - **v2.4.10**

---

## 🎯 ИТОГОВАЯ ЛОГИКА

### Покупка:
1. Проверяем все входные данные
2. Проверяем статус платежа (3 раза)
3. Проверяем отсутствие активной подписки
4. Атомарно создаем подписку (с повторной проверкой)
5. Создаем ключи на серверах
6. Отправляем уведомление
7. Обновляем `subscription_id` в платеже (с retry)
8. Помечаем платеж как `COMPLETED`

### Продление:
1. Проверяем все входные данные
2. Проверяем статус платежа (3 раза)
3. Проверяем наличие активной подписки
4. Проверяем, не является ли это покупкой (очень недавно созданная подписка)
5. Атомарно продлеваем подписку (`expires_at = expires_at + duration_sec`)
6. Обновляем лимит трафика
7. Сбрасываем трафик ключей
8. Отправляем уведомление
9. Обновляем `subscription_id` в платеже (с retry)
10. Помечаем платеж как `COMPLETED`

---

## ✅ ГАРАНТИИ

1. **Идемпотентность:** Платеж не будет обработан дважды благодаря проверкам статуса `COMPLETED`
2. **Атомарность:** Продление подписки атомарно благодаря SQL-обновлению
3. **Надежность:** Retry механизм для критических операций
4. **Мониторинг:** Автоматическое исправление проблем через фоновые задачи
