# Отчет об использовании expiry_at в проекте

**Дата проверки:** 2025-12-17  
**Дата исправления:** 2025-12-17  
**Статус миграции:** Поле `expiry_at` удалено из таблиц `keys` и `v2ray_keys`  
**Статус исправлений:** ✅ Все проблемные места исправлены

## ✅ Уже исправлено (используют JOIN с subscriptions)

### 1. Репозитории (app/repositories/)
- ✅ `key_repository.py` - все методы используют JOIN
- ✅ `subscription_repository.py` - использует JOIN

### 2. Валидаторы
- ✅ `validators.py:251, 257, 289, 292` - используют JOIN с subscriptions

### 3. Обработчики платежей
- ✅ `payments/services/payment_service.py:736, 739` - используют JOIN
- ✅ `payments/services/subscription_purchase_service.py:688, 896, 1445` - используют JOIN

### 4. Базовые обработчики
- ✅ `bot.py:188` - использует JOIN
- ✅ `bot/services/background_tasks.py:230` - использует JOIN
- ✅ `bot/handlers/keys.py` - использует JOIN

## ✅ Исправлено (2025-12-17)

Все проблемные места успешно исправлены. Поле `expiry_at` больше не используется в INSERT и UPDATE запросах.

## ❌ Требовали исправления (ИСПРАВЛЕНО)

### 1. bot/services/key_management.py

#### Проблема 1: UPDATE expiry_at (строка 247)
```python
# Строка 247 - имеет try/except для обратной совместимости, но лучше убрать
cursor.execute("UPDATE keys SET expiry_at = ? WHERE id = ?", (new_expiry, existing_key[0]))
```
**Статус:** Оборачивает в try/except для обратной совместимости, но лучше убрать или использовать обновление подписки

#### Проблема 2: INSERT с expiry_at (множество мест)
- **Строка 722**: `INSERT INTO keys (..., expiry_at, ...)` - создание ключа при смене протокола
- **Строка 821**: `INSERT INTO v2ray_keys (..., expiry_at, ...)` - создание V2Ray ключа
- **Строка 1049**: `INSERT INTO keys (..., expiry_at, ...)` - смена страны
- **Строка 1133**: `INSERT INTO v2ray_keys (..., expiry_at, ...)` - смена страны V2Ray
- **Строка 1350**: `INSERT INTO keys (..., expiry_at, ...)` - смена протокола
- **Строка 1404**: `INSERT INTO v2ray_keys (..., expiry_at, ...)` - смена протокола V2Ray
- **Строка 1611**: `INSERT INTO keys (..., expiry_at, ...)` - продление с другой страной
- **Строка 1695**: `INSERT INTO v2ray_keys (..., expiry_at, ...)` - продление V2Ray
- **Строка 1967**: `INSERT INTO keys (..., expiry_at, ...)` - продление Outline
- **Строка 2077**: `INSERT INTO v2ray_keys (..., expiry_at, ...)` - продление V2Ray

**Решение:** Убрать `expiry_at` из списка колонок и значений. Срок действия берется из подписки.

#### Проблема 3: UPDATE expiry_at в ключах подписки (строки 1026, 1037)
```python
# Строки 1026, 1037 - пытаются обновить expiry_at в ключах
UPDATE v2ray_keys SET expiry_at = ? WHERE subscription_id = ?
UPDATE keys SET expiry_at = ? WHERE subscription_id = ?
```
**Решение:** Эти UPDATE не нужны - срок действия берется из подписки. Нужно убрать или обернуть в try/except.

### 2. bot/services/key_creation.py

#### Проблема: UPDATE expiry_at (строки 1026, 1037)
```python
# Строки 1026, 1037 - обновление expiry_at при продлении через реферала
UPDATE v2ray_keys SET expiry_at = ? WHERE subscription_id = ?
UPDATE keys SET expiry_at = ? WHERE subscription_id = ?
```
**Решение:** Убрать - срок действия берется из подписки.

### 3. bot/services/subscription_service.py

#### Проблема: UPDATE expiry_at (строки 603, 614)
```python
# Строки 603, 614 - обновление expiry_at при продлении подписки
UPDATE v2ray_keys SET expiry_at = ? WHERE subscription_id = ?
UPDATE keys SET expiry_at = ? WHERE subscription_id = ?
```
**Решение:** Убрать - срок действия берется из подписки. Подписка уже обновлена выше в коде.

### 4. payments/services/subscription_purchase_service.py

#### Проблема: UPDATE expiry_at (строки 572, 583)
```python
# Строки 572, 583 - обновление expiry_at при создании подписки
UPDATE v2ray_keys SET expiry_at = ?, traffic_limit_mb = ? WHERE subscription_id = ?
UPDATE keys SET expiry_at = ?, traffic_limit_mb = ? WHERE subscription_id = ?
```
**Решение:** Убрать `expiry_at` из UPDATE - срок действия берется из подписки.

### 5. bot/services/key_management.py (дополнительные UPDATE)

#### Проблема: UPDATE expiry_at в динамических запросах
- **Строка 343**: `("expiry_at = ?", new_expiry)` - при перемещении ключа на альтернативный сервер (Outline)
- **Строка 401**: `("expiry_at = ?", new_expiry)` - при перемещении ключа на альтернативный сервер (V2Ray)
- **Строка 422**: `("expiry_at = ?", new_expiry)` - при продлении ключа (Outline)
- **Строка 434**: `("expiry_at = ?", new_expiry)` - при продлении ключа (V2Ray)
- **Строка 473**: `SET server_id = ?, access_url = ?, key_id = ?, expiry_at = ?, ...` - UPDATE ключа
- **Строка 493**: `SET server_id = ?, v2ray_uuid = ?, expiry_at = ?, ...` - UPDATE V2Ray ключа

**Решение:** Убрать `expiry_at` из всех UPDATE запросов. Если нужно обновить срок действия, обновлять подписку.

### 6. bot/services/background_tasks.py

#### Проблема: INSERT с expiry_at (строка 476)
```python
# Строка 476 - создание ключа при обработке платежей
"INSERT INTO keys (server_id, user_id, access_url, expiry_at, ...)"
```
**Решение:** Убрать `expiry_at` из INSERT. Эти ключи должны быть связаны с подписками через `subscription_id`.

### 7. bot.py

#### Проблема: INSERT с expiry_at (строка 234)
```python
# Строка 234 - создание ключа в старой функции create_new_key_flow
"INSERT INTO keys (server_id, user_id, access_url, expiry_at, ...)"
```
**Решение:** Убрать `expiry_at` из INSERT.

### 8. scripts/create_subscriptions_for_users_without.py

#### Проблема: WHERE expiry_at (строка 72)
```python
# Строка 72 - фильтрация по expiry_at
WHERE expiry_at IS NOT NULL AND expiry_at > 0
```
**Статус:** ✅ Это уже исправлено - используется `COALESCE(sub.expires_at, 0) as expiry_at`, так что это не проблема.

### 6. Тесты

#### Проблема: Тесты используют expiry_at
- `tests/bot/services/test_key_management.py` - INSERT с expiry_at
- `tests/bot/services/test_key_creation_flow.py` - INSERT с expiry_at
- `tests/bot/services/test_background_tasks.py` - INSERT с expiry_at

**Решение:** Обновить тесты для работы без expiry_at (использовать subscription_id).

## 📊 Статистика

- **Всего файлов с проблемами:** 5 (без учета тестов)
  - `bot/services/key_management.py` - 22 места (10 INSERT + 12 UPDATE)
  - `bot/services/key_creation.py` - 2 места (UPDATE)
  - `bot/services/subscription_service.py` - 2 места (UPDATE)
  - `payments/services/subscription_purchase_service.py` - 2 места (UPDATE)
  - `bot/services/background_tasks.py` - 1 место (INSERT)
  - `bot.py` - 1 место (INSERT)
- **Всего INSERT с expiry_at:** 12 мест
- **Всего UPDATE expiry_at:** 18 мест
- **Тесты требуют обновления:** 3 файла

## 🔄 Паттерны исправления

### INSERT запросы:
```python
# Было:
"INSERT INTO keys (server_id, user_id, access_url, expiry_at, traffic_limit_mb, ...)"
"VALUES (?, ?, ?, ?, ?, ...)"
(server_id, user_id, access_url, expiry_at, traffic_limit_mb, ...)

# Стало:
"INSERT INTO keys (server_id, user_id, access_url, traffic_limit_mb, ...)"
"VALUES (?, ?, ?, ?, ...)"
(server_id, user_id, access_url, traffic_limit_mb, ...)
```

### UPDATE запросы:
```python
# Было:
"UPDATE keys SET expiry_at = ? WHERE subscription_id = ?"

# Стало:
# Убрать полностью или использовать:
# from bot.services.subscription_service import SubscriptionService
# service = SubscriptionService()
# service.extend_subscription(subscription_id, additional_duration)
```

## ⚠️ Важные замечания

1. **Все ключи теперь связаны с подписками** - нет standalone ключей
2. **Срок действия всегда берется из subscriptions.expires_at**
3. **При продлении нужно обновлять подписку, а не ключи**
4. **INSERT запросы без subscription_id - это баг** - все ключи должны быть связаны с подпиской

