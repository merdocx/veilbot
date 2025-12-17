# Оставшиеся места для рефакторинга expiry_at

## 📋 Список файлов с оставшимися изменениями

### 1. bot/services/key_creation.py (~14 мест)
- ❌ Строка 233-239: SELECT с k.expiry_at в двух запросах для outline
- ❌ Строка 343-347: SELECT с k.expiry_at для v2ray
- ❌ Строка 517-526: SELECT с k.expiry_at для v2ray (2 запроса)
- ❌ Строка 571-574: SELECT с k.expiry_at для outline (2 запроса)
- ❌ Строка 1107: SELECT id, expiry_at FROM keys

### 2. bot/services/key_management.py (~12 мест)
- ❌ Строки 204-210: UPDATE keys SET expiry_at (4 запроса)
- ❌ Строки 374, 386: UPDATE с expiry_at = ? в частях запросов
- ❌ Строки 1822-1824: SELECT COUNT(*) FROM keys/v2ray_keys WHERE expiry_at > ?
- ❌ Строки 1856-1858: SELECT COUNT(*) FROM keys/v2ray_keys WHERE expiry_at > ?
- ❌ INSERT запросы с expiry_at (нужно убрать из списка колонок)

### 3. bot/services/background_tasks.py (1 место)
- ❌ Строка 230: SELECT COUNT(*) FROM keys WHERE expiry_at > ?

### 4. validators.py (4 места)
- ❌ Строка 251: SELECT expiry_at FROM keys WHERE key_id = ?
- ❌ Строка 255: SELECT expiry_at FROM v2ray_keys WHERE v2ray_uuid = ?
- ❌ Строка 289: WHERE server_id = ? AND expiry_at > ? (keys)
- ❌ Строка 292: WHERE server_id = ? AND expiry_at > ? (v2ray_keys)

### 5. bot.py (2 места)
- ❌ Строка 188: SELECT id, expiry_at, access_url FROM keys WHERE expiry_at > ?
- ❌ Строка 581: SELECT COUNT(*) FROM keys WHERE server_id = ? AND expiry_at > ?

### 6. payments/services/payment_service.py (2 места)
- ❌ Строка 736: SELECT 1 FROM keys WHERE user_id = ? AND expiry_at > ?
- ❌ Строка 739: SELECT 1 FROM v2ray_keys WHERE user_id = ? AND expiry_at > ?

### 7. payments/services/subscription_purchase_service.py (6 мест)
- ❌ Строка 687-690: SELECT k.access_url, k.expiry_at WHERE k.expiry_at > ?
- ❌ Строка 896-899: SELECT k.access_url, k.expiry_at WHERE k.expiry_at > ?
- ❌ Строка 1445-1448: SELECT k.access_url, k.expiry_at WHERE k.expiry_at > ?

### 8. scripts/create_subscriptions_for_users_without.py (1 место)
- ❌ Строка 68: WHERE expiry_at IS NOT NULL

### 9. db.py (опционально)
- ⚠️ CREATE TABLE для keys и v2ray_keys - нужно убрать expiry_at из определения таблицы
- Это нужно только для новых установок, существующие БД будут обновлены миграцией

## 📊 Статистика

- **Всего файлов для обновления:** 8
- **Всего мест:** ~42

## 🔄 Паттерны изменений

### SELECT expiry_at:
```sql
-- Было:
SELECT k.id, k.expiry_at, ...
FROM keys k WHERE k.expiry_at > ?

-- Стало:
SELECT k.id, COALESCE(sub.expires_at, 0) as expiry_at, ...
FROM keys k
LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
WHERE sub.expires_at > ?
```

### UPDATE expiry_at:
- Заменить на обновление подписки через SubscriptionRepository.extend_subscription()

### INSERT с expiry_at:
- Убрать expiry_at из списка колонок
- Убрать значение из списка параметров

### WHERE expiry_at:
```sql
-- Было:
WHERE expiry_at > ? OR expiry_at <= ?

-- Стало:
JOIN subscriptions sub ON k.subscription_id = sub.id
WHERE sub.expires_at > ? OR sub.expires_at <= ?
```


