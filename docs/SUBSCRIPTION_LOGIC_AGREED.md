# СОГЛАСОВАННЫЙ ВАРИАНТ: Упрощение логики подписок

**Дата согласования:** 2026-01-17  
**Статус:** ✅ СОГЛАСОВАНО - готово к разработке

---

## Согласованные решения

1. ✅ **Упрощенная логика (3 проверки)** - Да
2. ✅ **Пересчет вместо продления** - Да
3. ✅ **Проверка `subscription_id` для защиты от retry** - Да
4. ✅ **Универсальное уведомление** - Да
5. ✅ **VIP подписки не пересчитываются** - Да

---

## Финальная логика определения покупки vs продления

### Алгоритм обработки платежа (3 проверки)

```python
async def process_subscription_payment(payment_id: str):
    """
    Упрощенная универсальная функция обработки платежа подписки.
    
    Алгоритм:
    1. Проверка статуса платежа (completed → пропуск)
    2. Проверка subscription_id (защита от retry)
    3. Определение покупки/продления (есть подписка → продление, нет → покупка)
    4. Пересчет expires_at на основе всех платежей
    5. Атомарное обновление expires_at
    6. Отправка универсального уведомления
    """
    
    # Шаг 1: Проверка статуса completed (быстрая проверка)
    if payment.status == PaymentStatus.COMPLETED:
        return True, None  # Уже обработан
    
    if payment.status != PaymentStatus.PAID:
        return False, f"Payment is not paid (status: {payment.status.value})"
    
    # Шаг 2: КРИТИЧНО - Проверка subscription_id (защита от retry webhook)
    if payment.subscription_id is not None:
        # Платеж уже связан с подпиской → это retry webhook
        # Подписка уже создана/продлена при первой обработке
        logger.info(
            f"Payment {payment_id} already linked to subscription {payment.subscription_id}, "
            f"this is a retry webhook. Skipping processing."
        )
        return await send_notification_only_if_needed(payment)
    
    # Шаг 3: Определение покупки/продления (упрощенная логика)
    grace_threshold = now - DEFAULT_GRACE_PERIOD  # 24 часа назад
    existing_subscription = await get_active_subscription(payment.user_id, grace_threshold)
    
    if existing_subscription:
        # Есть активная подписка → ПРОДЛЕНИЕ
        subscription_id = existing_subscription.id
        subscription = existing_subscription
        was_created = False
    else:
        # Нет активной подписки → ПОКУПКА (создать подписку)
        subscription = await get_or_create_subscription(payment.user_id, payment, tariff)
        subscription_id = subscription.id
        was_created = True
    
    # Шаг 4: Пересчет expires_at на основе ВСЕХ платежей
    all_payments = await get_all_completed_payments_for_subscription(subscription_id)
    new_expires_at = calculate_subscription_expires_at(
        all_payments,
        subscription.created_at,
        payment.user_id
    )
    
    # Шаг 5: Атомарное обновление expires_at (только если изменился)
    if abs(subscription.expires_at - new_expires_at) > 60:  # Допускаем разницу до 1 минуты
        await recalculate_and_update_subscription_expires_at(
            subscription_id,
            all_payments,
            subscription.created_at,
            payment.user_id
        )
    
    # Шаг 6: Обновление subscription_id в платеже (связывание)
    await payment_repo.update_subscription_id(payment.payment_id, subscription_id)
    
    # Шаг 7: Отправка универсального уведомления (один раз на платеж)
    await send_notification_if_needed(payment, subscription, tariff, new_expires_at, was_created)
    
    # Шаг 8: Атомарное обновление статуса на completed
    await payment_repo.try_update_status(
        payment_id,
        PaymentStatus.COMPLETED,
        PaymentStatus.PAID
    )
    
    return True, None
```

---

## Детали реализации

### 1. Проверка статуса completed

**Цель:** Быстрая проверка для idempotency.

```python
if payment.status == PaymentStatus.COMPLETED:
    logger.info(f"Payment {payment_id} already completed, skipping")
    return True, None
```

### 2. Проверка subscription_id (защита от retry)

**Цель:** Защита от retry webhook после обработки платежа.

**Почему это работает:**
- `subscription_id` обновляется **раньше**, чем статус `completed`
- Даже если обработка упала после создания подписки, `subscription_id` уже установлен
- Это явный признак, что платеж уже обработан

```python
if payment.subscription_id is not None:
    # Платеж уже связан с подпиской → retry webhook
    # Не продлевать, только отправить уведомление (если не отправлено)
    logger.info(
        f"Payment {payment_id} already linked to subscription {payment.subscription_id}, "
        f"skipping processing (retry webhook)"
    )
    return await send_notification_only_if_needed(payment)
```

**Когда может быть retry после `paid`:**
- Ошибка обработки подписки (подписка создана, но обработка упала)
- Ошибка обновления статуса `completed` (подписка обработана, но статус не обновлен)
- YooKassa автоматически отправляет retry для гарантии доставки
- Race condition при параллельной обработке

### 3. Определение покупки/продления (упрощенная логика)

**Правило:**
- Если есть активная подписка (с grace period) → **ПРОДЛЕНИЕ**
- Если нет активной подписки → **ПОКУПКА**

**Grace period:** 24 часа (подписка считается активной, даже если истекла менее 24 часов назад)

```python
grace_threshold = now - DEFAULT_GRACE_PERIOD  # 24 часа назад

existing_subscription = await get_active_subscription(
    user_id=payment.user_id,
    grace_threshold=grace_threshold
)

if existing_subscription:
    # ПРОДЛЕНИЕ
    subscription = existing_subscription
    was_created = False
else:
    # ПОКУПКА (создать подписку)
    subscription = await get_or_create_subscription(
        user_id=payment.user_id,
        payment=payment,
        tariff=tariff
    )
    was_created = True
```

**Разница между покупкой и продлением:**

| Аспект | Покупка | Продление |
|--------|---------|-----------|
| Создание подписки | ✅ Создается новая | ❌ Используется существующая |
| Создание ключей | ✅ Создаются ключи | ❌ Ключи уже есть |
| Outline ключи | ✅ Отправляются (если `payments_count == 1`) | ❌ Не отправляются |
| Пересчет `expires_at` | ✅ Пересчитывается | ✅ Пересчитывается (одинаково) |
| Уведомление | ✅ "Подписка обновлена!" | ✅ "Подписка обновлена!" (одинаково) |

### 4. Пересчет expires_at (вместо продления)

**Формула:**
```python
def calculate_subscription_expires_at(
    payments: List[Payment],
    subscription_created_at: int,
    user_id: int
) -> int:
    """
    Пересчет expires_at на основе ВСЕХ платежей с учетом разных тарифов и реферальных бонусов.
    
    Формула:
    - Базовая дата = max(первый_платеж, created_at)
    - Суммарная длительность = sum(duration_sec всех платежей) - учитываются разные тарифы
    - Реферальные бонусы = 30 дней × количество рефералов (bonus_issued=1, с completed платежами)
    - expires_at = базовая_дата + суммарная_длительность + реферальные_бонусы
    """
    
    # VIP подписки не пересчитываются
    if is_vip_user(user_id):
        return VIP_EXPIRES_AT  # 4102434000 (01.01.2100)
    
    # Шаг 1: Собираем все completed платежи
    completed_payments = [p for p in payments if p.status == PaymentStatus.COMPLETED]
    
    if not completed_payments:
        return subscription_created_at  # Нет платежей → expires_at = created_at
    
    # Шаг 2: Базовая дата = первый платеж или created_at (что больше)
    first_payment_date = min(p.created_at for p in completed_payments)
    base_date = max(first_payment_date, subscription_created_at)
    
    # Шаг 3: Суммарная длительность всех платежей (учитываются РАЗНЫЕ тарифы)
    # Каждый платеж может быть с разным тарифом (30 дней, 90 дней, и т.д.)
    total_duration_sec = 0
    for payment in completed_payments:
        tariff_duration = get_tariff_duration(payment.tariff_id)
        total_duration_sec += tariff_duration
    
    # Пример расчета с разными тарифами:
    # - Платеж 1: тариф 30 дней (2592000 сек) → +30 дней
    # - Платеж 2: тариф 90 дней (7776000 сек) → +90 дней
    # - Платеж 3: тариф 30 дней (2592000 сек) → +30 дней
    # Итого: 150 дней (12960000 сек)
    
    # Шаг 4: Реферальные бонусы (30 дней × количество рефералов)
    referral_bonuses_sec = calculate_referral_bonuses(user_id, base_date)
    # Формула: referral_bonuses_sec = bonuses_count * REFERRAL_BONUS_DURATION
    # Где REFERRAL_BONUS_DURATION = 30 дней = 2592000 секунд
    # bonuses_count = количество рефералов с bonus_issued=1 и completed платежами
    
    # Шаг 5: Итоговый expires_at
    expires_at = base_date + total_duration_sec + referral_bonuses_sec
    
    return expires_at


def calculate_referral_bonuses(user_id: int, expires_at: int) -> int:
    """
    Рассчитать реферальные бонусы для пользователя.
    
    Условия для начисления бонуса:
    1. Реферал должен иметь bonus_issued = 1 в таблице referrals
    2. Реферал должен иметь completed платежи с amount > 0
    3. Платежи должны быть до текущего expires_at
    
    Формула:
    - Количество рефералов = COUNT(*) WHERE referrer_id = user_id AND bonus_issued = 1
    - Реферальные бонусы = bonuses_count * REFERRAL_BONUS_DURATION
    - REFERRAL_BONUS_DURATION = 30 дней = 2592000 секунд
    
    Пример:
    - У пользователя 3 реферала с bonus_issued=1
    - Бонус: 3 × 30 дней = 90 дней = 7776000 секунд
    """
    REFERRAL_BONUS_DURATION = 30 * 24 * 3600  # 30 дней в секундах
    
    # SQL запрос для подсчета рефералов с бонусами:
    # SELECT COUNT(*)
    # FROM referrals r
    # WHERE r.referrer_id = ?
    #   AND r.bonus_issued = 1
    #   AND EXISTS (
    #       SELECT 1 FROM payments p
    #       WHERE p.user_id = r.referred_id
    #         AND p.status = 'completed'
    #         AND p.amount > 0
    #         AND p.created_at <= ?
    #   )
    
    bonuses_count = get_referrals_count_with_bonuses(user_id, expires_at)
    referral_bonuses_sec = bonuses_count * REFERRAL_BONUS_DURATION
    
    return referral_bonuses_sec
```

**Пример расчета с разными тарифами и реферальными бонусами:**

```python
# Платежи:
# - Платеж 1: тариф "30 дней" (duration_sec = 2592000) → создан 2026-01-01 10:00:00
# - Платеж 2: тариф "90 дней" (duration_sec = 7776000) → создан 2026-01-31 10:00:00
# - Платеж 3: тариф "30 дней" (duration_sec = 2592000) → создан 2026-04-30 10:00:00

# Реферальные бонусы:
# - У пользователя 2 реферала с bonus_issued=1 и completed платежами
# - Бонус: 2 × 30 дней = 60 дней = 5184000 секунд

# Расчет:
# - Базовая дата = max(первый_платеж, created_at) = 2026-01-01 10:00:00
# - Суммарная длительность = 2592000 + 7776000 + 2592000 = 12960000 сек (150 дней)
# - Реферальные бонусы = 2 × 2592000 = 5184000 сек (60 дней)
# - expires_at = 2026-01-01 10:00:00 + 150 дней + 60 дней = 2026-06-10 10:00:00
```

**Важно:**
- ✅ **Разные тарифы учитываются:** Каждый платеж может быть с разным тарифом (30, 90, 365 дней и т.д.)
- ✅ **Суммируются все duration_sec:** Все платежи суммируются независимо от тарифа
- ✅ **Реферальные бонусы добавляются:** 30 дней × количество рефералов с `bonus_issued=1`
- ✅ **Рефералы должны иметь completed платежи:** Бонус начисляется только если реферал оплатил

**Преимущества пересчета:**
- ✅ Всегда правильный `expires_at` (на основе всех платежей)
- ✅ Нет накопления ошибок
- ✅ Идемпотентность (повторный расчет дает тот же результат)

### 5. VIP подписки (не пересчитываются)

**Логика:**
```python
VIP_EXPIRES_AT = 4102434000  # 01.01.2100
VIP_TRAFFIC_LIMIT_MB = 0     # Безлимит

if is_vip_user(user_id):
    # VIP подписка - не пересчитываем expires_at
    expires_at = VIP_EXPIRES_AT
    traffic_limit_mb = VIP_TRAFFIC_LIMIT_MB
else:
    # Обычная подписка - пересчитываем на основе платежей
    expires_at = calculate_subscription_expires_at(...)
```

**Проверка VIP статуса:**
- Перед расчетом `expires_at` проверяем `user.is_vip`
- Если VIP - возвращаем `VIP_EXPIRES_AT` без расчета
- Если не VIP - выполняем обычный расчет

### 6. Создание ключей при первом платеже

**Логика:** Ключи создаются только если подписка **создана** (не существовала).

```python
async def get_or_create_subscription(
    user_id: int, 
    payment: Payment, 
    tariff: Dict[str, Any]
) -> tuple:
    """
    Получить существующую подписку или создать новую.
    
    Returns:
        (subscription, was_created: bool)
    """
    subscription = get_active_subscription(user_id)
    
    if subscription:
        return subscription, False  # Подписка существовала
    else:
        # Создаем новую подписку
        subscription = create_subscription(...)
        
        # Создаем ключи на всех активных серверах
        if was_created:
            create_keys_for_subscription(subscription.id, ...)
        
        return subscription, True  # Подписка создана
```

**Условие создания ключей:**
- Создаем ключи только если `was_created = True`
- Если подписка существовала - ключи уже есть, не создаем

### 7. Отправка запасных Outline ключей

**Логика:** Отправлять только при **первом платеже** (чтобы не спамить пользователей).

```python
payments_count = len(get_all_completed_payments_for_subscription(subscription_id))

if payments_count == 1 and was_created:
    # Первый платеж - отправляем Outline ключи
    send_outline_backup_keys_notification(...)
else:
    # Последующие платежи - не отправляем (ключи уже есть)
    pass
```

**Текст уведомления:**
```
🎁 *Также мы подготовили для вас запасные Outline ключи (потребуется скачать другое приложение):*

[Список Outline ключей по серверам]
```

### 8. Универсальное уведомление

**Текст уведомления (для всех платежей):**
```
✅ *Подписка обновлена!*

🔗 *Ссылка (коснитесь, чтобы скопировать):*
`{subscription_url}`

⏳ *Срок действия:* до {datetime.fromtimestamp(new_expires_at).strftime('%Y-%m-%d %H:%M:%S')}

💡 Подписка автоматически обновится в вашем приложении
```

**Логика:**
- Одинаковый текст для всех платежей (первый, второй, и т.д.)
- Показываем новый `expires_at` после пересчета
- Инструкция по использованию не нужна (подписка автоматически обновляется)

### 9. Защита от дублирования уведомлений

**Выбранное решение:** Использовать поле `notification_sent` в таблице `payments` (по одному флагу на платеж).

```python
# В таблице payments добавляем поле notification_sent (если еще нет)
# При отправке уведомления устанавливаем флаг
# Проверяем флаг перед отправкой
# Если флаг установлен - пропускаем отправку
```

**Логика:**
```python
if payment.notification_sent:
    logger.info(f"Notification already sent for payment {payment_id}, skipping")
    return True, None

# Отправляем уведомление
success = await send_notification(...)

if success:
    # Устанавливаем флаг
    await payment_repo.mark_notification_sent(payment_id)
```

### 10. Сброс трафика при обновлении подписки

**Выбранное решение:** Не сбрасывать трафик при пересчете.

**Обоснование:**
- Пересчет `expires_at` - это не продление, а пересчет на основе всех платежей
- Сброс трафика должен происходить только при явном продлении (если таковое понадобится)
- Иначе пользователь может потерять историю использования трафика

**Логика:**
```python
# Не вызываем reset_subscription_traffic() при пересчете
# Трафик остается неизменным
```

### 11. Обновление tariff_id и traffic_limit_mb

**Логика:** Использовать значения из **последнего платежа**.

```python
last_payment = max(payments, key=lambda p: p.created_at)

subscription.tariff_id = last_payment.tariff_id
subscription.traffic_limit_mb = get_tariff(last_payment.tariff_id).traffic_limit_mb
```

**Обоснование:**
- Подписка соответствует текущему активному тарифу
- Если пользователь перешел на другой тариф - подписка обновляется под него
- `traffic_limit_mb` берется из тарифа последнего платежа

---

## Итоговая структура

### Преимущества согласованного варианта

1. **Простота:**
   - 3 проверки вместо 28+
   - Понятная логика: есть подписка → продление, нет → покупка
   - Легко отлаживать и поддерживать

2. **Правильность:**
   - Всегда правильный `expires_at` (пересчет на основе всех платежей)
   - Нет накопления ошибок
   - Нет расхождений (как в подписке #272)

3. **Надежность:**
   - Защита от retry через `subscription_id`
   - Атомарные операции (создание и обновление)
   - Идемпотентность (повторная обработка безопасна)

4. **Идемпотентность:**
   - Повторная обработка одного платежа дает тот же результат
   - Множественная обработка параллельных платежей безопасна
   - Пересчет всегда дает один результат

### Риски и их митигация

| Риск | Вероятность | Влияние | Митигация | Статус |
|------|-------------|---------|-----------|--------|
| Webhook retry первого платежа | ⚠️ Средняя | 🔴 Высокое | ✅ Проверка `subscription_id` | ✅ ЗАЩИЩЕНО |
| Накопление ошибок в `expires_at` | ❌ Нет | - | ✅ Пересчет вместо продления | ✅ РЕШЕНО |
| Race condition при создании | ✅ Низкая | ✅ Низкое | ✅ BEGIN IMMEDIATE | ✅ ЗАЩИЩЕНО |
| Параллельное продление | ✅ Низкая | ✅ Низкое | ✅ SQL-атомарность | ✅ ЗАЩИЩЕНО |

---

## Этапы внедрения

### Этап 1: Подготовка (без изменений в production)
- [ ] Добавить поле `notification_sent` в таблицу `payments` (миграция)
- [ ] Создать функцию `calculate_subscription_expires_at`
- [ ] Создать функцию `get_or_create_subscription`
- [ ] Создать функцию `recalculate_and_update_subscription_expires_at`
- [ ] Написать тесты для новой логики

### Этап 2: Тестирование
- [ ] Запустить миграцию на тестовой БД
- [ ] Проверить пересчет `expires_at` для тестовых подписок
- [ ] Проверить обработку нового платежа (покупка)
- [ ] Проверить обработку второго платежа (продление)
- [ ] Проверить обработку параллельных платежей
- [ ] Проверить защиту от retry (проверка `subscription_id`)

### Этап 3: Внедрение в production
- [ ] Выполнить миграцию в production
- [ ] Заменить логику в `process_subscription_purchase`
- [ ] Мониторинг первой недели после внедрения
- [ ] Проверка отсутствия расхождений в `expires_at`

### Этап 4: Очистка (после стабилизации)
- [ ] Удалить старый код (проверки `is_very_recent`, `expires_at_matches_expected`, etc.)
- [ ] Удалить неиспользуемые методы (`_extend_subscription`, `_create_subscription_as_renewal`)
- [ ] Обновить документацию

---

## Метрики успеха

1. **Отсутствие расхождений:** `expires_at` всегда совпадает с расчетом от платежей
2. **Простота кода:** Уменьшение количества проверок с 28+ до 3
3. **Производительность:** Обработка платежа < 500ms
4. **Надежность:** 0 ошибок при параллельной обработке платежей
5. **Идемпотентность:** Повторная обработка одного платежа безопасна

---

## Итоговое согласование

✅ **Все пункты согласованы:**

1. ✅ Упрощенная логика (3 проверки)
2. ✅ Пересчет вместо продления
3. ✅ Проверка `subscription_id` для защиты от retry
4. ✅ Универсальное уведомление
5. ✅ VIP подписки не пересчитываются

**Статус:** ✅ **ГОТОВО К РАЗРАБОТКЕ**

---

**Дата создания:** 2026-01-17  
**Дата согласования:** 2026-01-17  
**Статус:** ✅ СОГЛАСОВАНО  
**Приоритет:** Высокий (устранение критических ошибок расчета сроков)
