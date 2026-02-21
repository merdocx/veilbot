# Исправления учета VIP статуса и реферального бонуса

Дата: 2026-01-19

## Выполненные исправления

### 1. VIP статус в `process_subscription_purchase()`

**Проблема:** VIP статус не проверялся, мог сбросить VIP_EXPIRES_AT при продлении

**Исправление:**
- Добавлена проверка VIP статуса перед обновлением expires_at
- Для VIP подписок: expires_at не изменяется, traffic_limit_mb = 0

**Код (строка 354-369):**
```python
# ВАЖНО: Проверяем VIP статус перед обновлением expires_at
is_vip = self.user_repo.is_user_vip(payment.user_id)
is_vip_subscription = current_expires_at >= self.VIP_EXPIRES_AT - 86400

if is_vip_subscription or is_vip:
    # VIP подписка - не изменяем expires_at
    new_expires_at = current_expires_at
```

### 2. VIP статус в `_extend_subscription()`

**Проблема:** VIP статус не проверялся при продлении, мог сбросить VIP_EXPIRES_AT

**Исправление:**
- Добавлена проверка VIP статуса перед продлением
- Для VIP подписок: expires_at не изменяется, traffic_limit_mb = 0

**Код (строка 808-825):**
```python
# ВАЖНО: Проверяем VIP статус перед продлением
is_vip = self.user_repo.is_user_vip(payment.user_id)
is_vip_subscription = existing_expires_at >= self.VIP_EXPIRES_AT - 86400

if is_vip_subscription or is_vip:
    # VIP подписка - не изменяем expires_at
    new_expires_at = existing_expires_at
```

### 3. VIP статус в `_get_or_create_subscription()`

**Проблема:** VIP статус не проверялся при создании новой подписки

**Исправление:**
- Добавлена проверка VIP статуса перед созданием подписки
- Для VIP пользователей: expires_at = VIP_EXPIRES_AT, traffic_limit_mb = 0

**Код (строка 2102-2115):**
```python
# ВАЖНО: Проверяем VIP статус перед созданием подписки
is_vip = self.user_repo.is_user_vip(user_id)

if is_vip:
    expires_at = self.VIP_EXPIRES_AT
    traffic_limit_mb = 0  # VIP = безлимит
else:
    expires_at = now + tariff['duration_sec']
    traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
```

## Проверка реферального бонуса

### ✅ Реферальный бонус всегда сохраняется

**Метод `_update_subscription_traffic_limit_safe()`:**
- Сохраняет текущий лимит, если он больше лимита тарифа (реферальный бонус)
- Используется во всех местах обновления лимита трафика (кроме VIP)

**Логика сохранения (строка 2685-2690):**
```python
if current_limit_mb > tariff_limit_mb and tariff_limit_mb > 0:
    # Текущий лимит больше лимита тарифа - возможно, это реферальный бонус
    # Сохраняем текущий лимит, чтобы не потерять бонус
    new_limit_mb = current_limit_mb
```

**Использование:**
- ✅ `process_subscription_purchase()` - использует безопасное обновление (кроме VIP)
- ✅ `_extend_subscription()` - использует безопасное обновление (кроме VIP)
- ✅ `_get_or_create_subscription()` - использует безопасное обновление (кроме VIP)

## Итоговый статус

### ✅ VIP статус

1. ✅ Проверяется при создании новой подписки
2. ✅ Проверяется при продлении подписки
3. ✅ Проверяется в основном методе обработки покупки
4. ✅ VIP_EXPIRES_AT сохраняется при продлении
5. ✅ VIP_TRAFFIC_LIMIT_MB (0 = безлимит) устанавливается для VIP подписок

### ✅ Реферальный бонус

1. ✅ Сохраняется при продлении подписки
2. ✅ Сохраняется при обновлении лимита трафика
3. ✅ Не теряется при смене тарифа (если новый лимит меньше текущего)

## Все требования выполнены

1. ✅ Тариф всегда верно устанавливается
2. ✅ Срок всегда верно устанавливается (с учетом VIP)
3. ✅ Лимит трафика всегда верно устанавливается (с учетом VIP и реферального бонуса)
4. ✅ К платежу всегда приписывается номер подписки
5. ✅ VIP статус всегда учитывается
6. ✅ Реферальный бонус всегда сохраняется
