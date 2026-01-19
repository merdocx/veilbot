# Анализ учета VIP статуса и реферального бонуса

Дата: 2026-01-19

## Обнаруженные проблемы

### 1. VIP статус не проверялся в критических методах

**Проблемы:**
1. ❌ `process_subscription_purchase()` - не проверял VIP статус, мог сбросить VIP expires_at
2. ❌ `_get_or_create_subscription()` - не проверял VIP статус при создании новой подписки
3. ❌ `_extend_subscription()` - не проверял VIP статус при продлении, мог сбросить VIP expires_at

**Последствия:**
- VIP подписки могли потерять VIP_EXPIRES_AT при продлении
- VIP подписки могли быть созданы без VIP_EXPIRES_AT
- VIP подписки могли потерять безлимит (traffic_limit_mb = 0)

## Выполненные исправления

### 1. Исправление `process_subscription_purchase()`

**Добавлено:**
- Проверка VIP статуса перед обновлением expires_at
- Проверка, является ли подписка VIP (expires_at >= VIP_EXPIRES_AT)
- Для VIP подписок: не изменяется expires_at, устанавливается traffic_limit_mb = 0

**Код:**
```python
# ВАЖНО: Проверяем VIP статус перед обновлением expires_at
is_vip = self.user_repo.is_user_vip(payment.user_id)
is_vip_subscription = current_expires_at >= self.VIP_EXPIRES_AT - 86400

if is_vip_subscription or is_vip:
    # VIP подписка - не изменяем expires_at
    new_expires_at = current_expires_at
    traffic_limit_mb = 0  # VIP = безлимит
```

### 2. Исправление `_extend_subscription()`

**Добавлено:**
- Проверка VIP статуса перед продлением
- Для VIP подписок: не изменяется expires_at, устанавливается traffic_limit_mb = 0

**Код:**
```python
# ВАЖНО: Проверяем VIP статус перед продлением
is_vip = self.user_repo.is_user_vip(payment.user_id)
is_vip_subscription = existing_expires_at >= self.VIP_EXPIRES_AT - 86400

if is_vip_subscription or is_vip:
    # VIP подписка - не изменяем expires_at
    new_expires_at = existing_expires_at
    traffic_limit_mb = 0  # VIP = безлимит
```

### 3. Исправление `_get_or_create_subscription()`

**Добавлено:**
- Проверка VIP статуса перед созданием новой подписки
- Для VIP пользователей: устанавливается VIP_EXPIRES_AT и traffic_limit_mb = 0

**Код:**
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
- Обновляет лимит из тарифа, если текущий лимит <= лимит тарифа
- Сохраняет безлимит (0) для VIP подписок

**Логика:**
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

## Рекомендации для тестирования

1. **VIP подписка - создание:**
   - Проверить, что expires_at = VIP_EXPIRES_AT
   - Проверить, что traffic_limit_mb = 0

2. **VIP подписка - продление:**
   - Проверить, что expires_at не изменяется
   - Проверить, что traffic_limit_mb остается 0

3. **Реферальный бонус - продление:**
   - Проверить, что реферальный бонус сохраняется
   - Пример: 200 ГБ (100 ГБ тариф + 100 ГБ бонус) остается 200 ГБ

4. **Реферальный бонус + VIP:**
   - Проверить, что VIP подписка имеет безлимит (0), независимо от реферального бонуса
