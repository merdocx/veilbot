# Итоговый вариант упрощения логики подписок

## Упрощенная логика определения покупки vs продления

**Простое правило (2 проверки):**

1. **Проверка `subscription_id` в платеже:**
   - Если `payment.subscription_id != None` → Платеж уже обработан (retry webhook)
   - Действие: Не продлевать, только отправить уведомление (если не отправлено)

2. **Проверка наличия активной подписки:**
   - Если подписки нет (активной или с grace period) → **ПОКУПКА**
   - Если подписка есть (активная или с grace period, любая) → **ПРОДЛЕНИЕ**

## Когда может быть retry после статуса `paid`?

**Retry webhook после `paid` может произойти в следующих случаях:**

1. **Ошибка обработки подписки** (строки 104-112 в `webhook_service.py`):
   - Подписка создана успешно
   - Но обработка упала с ошибкой (exception)
   - Платеж остался в статусе `paid` (не обновлен на `completed`)
   - YooKassa отправляет retry webhook

2. **Ошибка обновления статуса `completed`:**
   - Подписка создана и обработана
   - Но обновление статуса `completed` упало (ошибка БД, транзакция откатилась)
   - Платеж остается в статусе `paid`
   - Retry webhook видит `paid` → обрабатывает повторно

3. **YooKassa автоматически отправляет retry:**
   - Даже если webhook обработался успешно
   - YooKassa может отправить retry для гарантии доставки
   - Это стандартное поведение платежных систем

4. **Race condition:**
   - Параллельная обработка одного платежа
   - Один процесс создал подписку, второй видит `paid` и обрабатывает повторно

**Вывод:** Retry **может** произойти даже после статуса `paid`, если:
- Обработка подписки упала (но подписка уже создана)
- Обновление статуса `completed` упало (но подписка уже создана)
- YooKassa отправил retry автоматически

**Решение:** Проверка `subscription_id` защищает от retry, так как `subscription_id` обновляется раньше, чем статус `completed`.

---

## Итоговый алгоритм обработки платежа

```python
async def process_subscription_payment(payment_id: str):
    """
    Упрощенная логика обработки платежа подписки.
    
    Алгоритм:
    1. Проверка статуса платежа (должен быть 'paid')
    2. Проверка subscription_id (защита от retry)
    3. Получение или создание подписки
    4. Пересчет expires_at на основе всех платежей
    5. Атомарное обновление expires_at
    6. Отправка уведомления (один раз на платеж)
    """
    
    # Шаг 1: Получаем платеж
    payment = await payment_repo.get_by_payment_id(payment_id)
    if not payment:
        return False, "Payment not found"
    
    # Шаг 2: Проверка статуса (быстрая проверка)
    if payment.status == PaymentStatus.COMPLETED:
        return True, None  # Уже обработан
    
    if payment.status != PaymentStatus.PAID:
        return False, f"Payment is not paid (status: {payment.status.value})"
    
    # Шаг 3: КРИТИЧНО - проверка subscription_id (защита от retry)
    if payment.subscription_id is not None:
        # Платеж уже связан с подпиской → это retry webhook
        # Не продлевать, только отправить уведомление (если не отправлено)
        logger.info(
            f"Payment {payment_id} already linked to subscription {payment.subscription_id}, "
            f"this is a retry webhook. Skipping processing."
        )
        return await send_notification_only_if_needed(payment)
    
    # Шаг 4: Получаем тариф
    tariff = await get_tariff(payment.tariff_id)
    if not tariff:
        return False, f"Tariff {payment.tariff_id} not found"
    
    # Шаг 5: Определяем покупка/продление (упрощенная логика)
    grace_threshold = now - DEFAULT_GRACE_PERIOD  # 24 часа назад
    existing_subscription = await get_active_subscription(payment.user_id, grace_threshold)
    
    if existing_subscription:
        # Есть активная подписка → ПРОДЛЕНИЕ
        subscription_id = existing_subscription.id
        subscription = existing_subscription
    else:
        # Нет активной подписки → ПОКУПКА (создать подписку)
        subscription = await get_or_create_subscription(payment.user_id, payment)
        subscription_id = subscription.id
    
    # Шаг 6: Пересчитываем expires_at на основе ВСЕХ платежей
    all_payments = await get_all_completed_payments_for_subscription(subscription_id)
    new_expires_at = calculate_subscription_expires_at(
        all_payments, 
        subscription.created_at,
        payment.user_id
    )
    
    # Шаг 7: Атомарно обновляем expires_at (только если изменился)
    if abs(subscription.expires_at - new_expires_at) > 60:  # Допускаем разницу до 1 минуты
        await recalculate_and_update_subscription_expires_at(
            subscription_id,
            all_payments,
            subscription.created_at,
            payment.user_id
        )
    
    # Шаг 8: Обновляем subscription_id в платеже (связываем платеж с подпиской)
    await payment_repo.update_subscription_id(payment.payment_id, subscription_id)
    
    # Шаг 9: Отправляем уведомление (один раз на платеж)
    await send_notification_if_needed(payment, subscription, tariff, new_expires_at)
    
    # Шаг 10: Атомарно обновляем статус на completed
    await payment_repo.try_update_status(
        payment_id,
        PaymentStatus.COMPLETED,
        PaymentStatus.PAID
    )
    
    return True, None
```

---

## Защита от retry: проверка `subscription_id`

**Почему проверка `subscription_id` эффективна:**

1. **`subscription_id` обновляется раньше, чем статус `completed`:**
   - Связывание платежа с подпиской происходит сразу после создания подписки
   - Статус `completed` обновляется в конце обработки
   - Даже если обработка упадет после обновления `subscription_id`, retry увидит связь

2. **Явный признак обработки:**
   - Если `subscription_id != None` → платеж точно обработан
   - Это надежнее, чем проверка статуса `completed` (который может не обновиться)

3. **Защищает от всех случаев retry:**
   - Retry от YooKassa
   - Ошибка обработки (после создания подписки)
   - Ошибка обновления статуса `completed`
   - Race condition

**Логика проверки `subscription_id`:**

```python
if payment.subscription_id is not None:
    # Платеж уже связан с подпиской
    # Это retry webhook или параллельная обработка
    # Подписка уже создана/продлена при первой обработке
    # Не нужно повторять обработку
    
    # Только проверим, нужно ли отправить уведомление
    # (например, если уведомление не отправилось при первой обработке)
    if not is_notification_sent(payment):
        await send_notification_only(...)
    
    return True, None  # Пропускаем обработку
```

---

## Итоговый вариант упрощенной логики

### Упрощенное определение покупка/продление

**Только 1 проверка (после проверки `subscription_id`):**

```python
# Проверка наличия активной подписки
existing_subscription = get_active_subscription(user_id, grace_threshold)

if existing_subscription:
    # ПРОДЛЕНИЕ
    subscription = existing_subscription
else:
    # ПОКУПКА (создать подписку)
    subscription = create_subscription(...)
```

**Все остальное одинаково:**
- Пересчет `expires_at` на основе всех платежей (и для покупки, и для продления)
- Атомарное обновление `expires_at`
- Отправка уведомления
- Обновление `subscription_id` в платеже
- Обновление статуса `completed`

### Разница между покупкой и продлением

**В упрощенной логике разница только в:**

1. **Создание подписки:**
   - Покупка: создаем новую подписку
   - Продление: используем существующую подписку

2. **Создание ключей:**
   - Покупка: создаем ключи на всех серверах (только если подписка создана)
   - Продление: ключи уже есть, не создаем

3. **Отправка Outline ключей:**
   - Покупка: отправляем Outline ключи (только если `payments_count == 1`)
   - Продление: не отправляем (чтобы не спамить)

**ВСЕ ОСТАЛЬНОЕ ОДИНАКОВО:**
- Пересчет `expires_at` - одинаковый для покупки и продления
- Обновление `expires_at` - одинаковое
- Отправка уведомления - одинаковое (но текст универсальный)
- Обновление `subscription_id` - одинаковое
- Обновление статуса `completed` - одинаковое

---

## Согласование итогового варианта

### ✅ Упрощенная логика определения покупки/продления

**Правило:**
1. Если `payment.subscription_id != None` → Пропускаем обработку (retry)
2. Если есть активная подписка (grace period) → **ПРОДЛЕНИЕ**
3. Если нет активной подписки → **ПОКУПКА**

**Все остальное одинаково для покупки и продления:**
- Пересчет `expires_at` на основе всех платежей
- Атомарное обновление `expires_at`
- Универсальное уведомление
- Обновление `subscription_id` и статуса

### ✅ Защита от retry

**Проверка `subscription_id` в начале обработки:**
```python
if payment.subscription_id is not None:
    # Платеж уже обработан → пропускаем
    return send_notification_only_if_needed(payment)
```

### ✅ Пересчет expires_at (не продление)

**Всегда пересчитываем на основе всех платежей:**
```python
all_payments = get_all_completed_payments_for_subscription(subscription_id)
new_expires_at = calculate_subscription_expires_at(all_payments, ...)
```

**Не используем:** `expires_at = expires_at + duration` (продление)

---

## Вопросы для согласования

1. ✅ **Упрощенная логика определения?** → Да: проверка `subscription_id` + проверка наличия подписки
2. ✅ **Пересчет вместо продления?** → Да: всегда пересчитываем `expires_at` на основе всех платежей
3. ✅ **Проверка `subscription_id` для защиты от retry?** → Да: обязательно
4. ✅ **Универсальное уведомление?** → Да: "Подписка обновлена!" для всех платежей
5. ✅ **VIP подписки?** → Да: не пересчитываем, остается `VIP_EXPIRES_AT`
6. ✅ **Создание ключей?** → Да: только если подписка создана (`was_created = True`)
7. ✅ **Outline ключи?** → Да: только при первом платеже (`payments_count == 1`)

---

**Готов к согласованию и разработке после вашего подтверждения.**
