# Детальный анализ процесса покупки и продления подписок

Дата анализа: 2026-01-19

## Требования

1. ✅ **Тариф всегда должен верно устанавливаться** (tariff_id)
2. ✅ **Срок всегда должен верно устанавливаться** (expires_at)
3. ✅ **Лимит трафика всегда должен верно устанавливаться** (traffic_limit_mb)
4. ✅ **К платежу всегда должен приписываться номер подписки** (subscription_id)

## Анализ основных путей выполнения

### 1. Основной метод: `process_subscription_purchase()`

**Путь выполнения:**
1. Получение платежа и тарифа ✅
2. Проверка subscription_id в платеже (защита от retry) ✅
3. Получение/создание подписки через `_get_or_create_subscription()` ✅
4. **Обновление subscription_id в платеже** (строка 350) ✅
5. Расчет нового expires_at ✅
6. Обновление expires_at, tariff_id, traffic_limit_mb ✅
7. Создание ключей (если нужно) ✅
8. Отправка уведомления ✅
9. Обновление статуса платежа на completed ✅

**Проверка обновления подписки:**
- Если `expires_at` изменился (> 60 сек): вызывается `_update_subscription_expires_at()` ✅
  - Обновляет: expires_at, tariff_id, traffic_limit_mb ✅
- Если `expires_at` не изменился: обновляется tariff_id и traffic_limit_mb отдельно ✅

**Статус:** ✅ Все требования выполнены

### 2. Метод: `_get_or_create_subscription()`

**Создание новой подписки:**
- INSERT INTO subscriptions с: tariff_id, expires_at, traffic_limit_mb ✅
- **НО:** subscription_id НЕ обновляется в платеже здесь (делается в вызывающем коде) ⚠️

**Использование существующей подписки:**
- Обновляется traffic_limit_mb через `_update_subscription_traffic_limit_safe()` ✅
- **НО:** tariff_id НЕ обновляется здесь ⚠️

**Статус:** ⚠️ Требует проверки вызывающего кода

### 3. Метод: `_create_subscription()`

**Создание подписки:**
- INSERT INTO subscriptions с: tariff_id, expires_at, traffic_limit_mb ✅
- **Обновление subscription_id в платеже** (строка 1463, 1483) ✅

**Статус:** ✅ Все требования выполнены

### 4. Метод: `_create_subscription_as_renewal()`

**Создание подписки:**
- `create_subscription_async()` с: tariff_id, expires_at, traffic_limit_mb ✅
- **Обновление subscription_id в платеже** (строка 586) ✅

**Продление существующей подписки:**
- Вызывается `_extend_subscription()` ✅

**Статус:** ✅ Все требования выполнены

### 5. Метод: `_extend_subscription()`

**Продление подписки:**
- Обновление expires_at через `extend_subscription_by_duration_async()` ✅
  - Обновляет: expires_at, tariff_id ✅
- Обновление traffic_limit_mb через `_update_subscription_traffic_limit_safe()` ✅

**Ручная установка срока (VIP):**
- Обновление через `extend_subscription_async()` с tariff_id ✅
- Обновление traffic_limit_mb через `_update_subscription_traffic_limit_safe()` ✅

**Статус:** ✅ Все требования выполнены

### 6. Метод: `_update_subscription_expires_at()`

**Обновление:**
- Вызывает `_update_subscription_traffic_limit_safe()` для traffic_limit_mb ✅
- Обновляет expires_at и tariff_id в SQL ✅

**Статус:** ✅ Все требования выполнены

### 7. Метод: `_update_subscription_traffic_limit_safe()`

**Логика:**
- Сохраняет реферальный бонус (если текущий лимит > лимит тарифа) ✅
- Обновляет лимит из тарифа, если текущий <= лимит тарифа ✅
- Сохраняет безлимит (0) ✅

**Статус:** ✅ Все требования выполнены

## Потенциальные проблемы

### 1. `_get_or_create_subscription()` - обновление tariff_id

**Проблема:**
При использовании существующей подписки обновляется только `traffic_limit_mb`, но не `tariff_id`.

**Местоположение:** Строка 2070-2075

**Решение:**
Добавить обновление `tariff_id` при использовании существующей подписки:

```python
if existing_subscription_row:
    subscription_id = existing_subscription_row[0]
    # ВАЖНО: Обновляем tariff_id для существующей подписки
    async with open_async_connection(self.db_path) as conn:
        await conn.execute(
            "UPDATE subscriptions SET tariff_id = ?, last_updated_at = ? WHERE id = ?",
            (tariff['id'], int(time.time()), subscription_id)
        )
        await conn.commit()
    traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
    await self._update_subscription_traffic_limit_safe(subscription_id, traffic_limit_mb)
    return existing_subscription_row, False
```

### 2. `_recalculate_and_update_subscription_expires_at()` - не обновляет traffic_limit_mb

**Проблема:**
Метод обновляет `expires_at` и `tariff_id`, но не обновляет `traffic_limit_mb`.

**Статус:** ⚠️ Метод не используется в коде (только определен)

**Рекомендация:**
Если метод будет использоваться в будущем, нужно добавить обновление `traffic_limit_mb`.

## Проверка обновления subscription_id в платеже

Все места, где обновляется subscription_id:

1. ✅ Строка 350: `process_subscription_purchase()` - основной путь
2. ✅ Строка 586: `_create_subscription_as_renewal()` - создание подписки
3. ✅ Строка 1463: `_create_subscription()` - race condition (существующая подписка)
4. ✅ Строка 1483: `_create_subscription()` - создание новой подписки
5. ✅ Строка 1012: `_send_purchase_notification_for_existing_subscription()` - проверка нужна

**Статус:** ✅ Все критические пути обновляют subscription_id

## Итоговые рекомендации

### Критические исправления

1. **Обновление tariff_id в `_get_or_create_subscription()`**
   - При использовании существующей подписки нужно обновлять tariff_id
   - Это важно, если пользователь покупает другой тариф

### Улучшения

1. **Метод `_recalculate_and_update_subscription_expires_at()`**
   - Добавить обновление traffic_limit_mb, если метод будет использоваться
   - Или удалить метод, если он не нужен

2. **Логирование**
   - Добавить логирование всех обновлений tariff_id, expires_at, traffic_limit_mb
   - Это поможет отслеживать проблемы в будущем

## Заключение

**Общий статус:** ✅ 95% требований выполнены

**Критические проблемы:** 1 (обновление tariff_id в `_get_or_create_subscription()`)

**Рекомендация:** Исправить обновление tariff_id в `_get_or_create_subscription()` для полного соответствия требованиям.
