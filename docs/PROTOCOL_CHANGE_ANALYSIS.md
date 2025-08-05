# Анализ логики смены протокола ("Сменить приложение")

## Требования
Кнопка "Сменить приложение" должна:
1. ✅ Если ключей несколько, пользователю предоставляется выбор ключа
2. ✅ Старый ключ удаляется из базы и с VPN сервера
3. ✅ Новый ключ создается на новом протоколе в той же стране

## Анализ кода

### Функция: `handle_change_app()`

#### 1. Получение всех активных ключей
```python
# Получаем все активные ключи пользователя (Outline + V2Ray)
cursor.execute("""
    SELECT k.id, k.expiry_at, k.server_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, 'outline' as key_type
    FROM keys k
    JOIN servers s ON k.server_id = s.id
    WHERE k.user_id = ? AND k.expiry_at > ?
    ORDER BY k.expiry_at DESC
""", (user_id, now))
```

#### 2. Проверка количества ключей
```python
if len(all_keys) == 1:
    # Если только один ключ, меняем его протокол сразу
    await change_protocol_for_key(message, user_id, all_keys[0])
else:
    # Если несколько ключей, показываем список для выбора
    await show_protocol_change_menu(message, user_id, all_keys)
```

**✅ Соответствует требованию 1:** Если ключей несколько, показывается меню выбора

### Функция: `show_protocol_change_menu()`

**Меню выбора ключа:**
```python
async def show_protocol_change_menu(message: types.Message, user_id: int, keys: list):
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for i, key in enumerate(keys):
        # Форматируем информацию о ключе
        protocol_icon = PROTOCOLS[key['protocol']]['icon']
        expiry_time = time.strftime('%d.%m.%Y %H:%M', time.localtime(key['expiry_at']))
        button_text = f"{protocol_icon} {key['country']} - {tariff_name} (до {expiry_time})"
        
        keyboard.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"change_protocol_{key['type']}_{key['id']}"
        ))
```

### Функция: `change_protocol_for_key()`

#### 1. Определение нового протокола
```python
# Определяем новый протокол (противоположный текущему)
new_protocol = "v2ray" if old_protocol == "outline" else "outline"
```

#### 2. Поиск сервера с новым протоколом
```python
# Ищем сервер той же страны с новым протоколом
cursor.execute("""
    SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key FROM servers 
    WHERE active = 1 AND country = ? AND protocol = ?
""", (country, new_protocol))
```

**✅ Соответствует требованию 3:** Новый ключ создается на новом протоколе в той же стране

#### 3. Удаление старого ключа

**Для Outline:**
```python
# Удаляем старый ключ из Outline сервера
cursor.execute("SELECT cert_sha256 FROM servers WHERE id = ?", (old_server_id,))
old_cert_sha256 = cursor.fetchone()
if old_cert_sha256:
    try:
        await asyncio.get_event_loop().run_in_executor(None, delete_key, api_url, old_cert_sha256[0], key_data['id'])
    except Exception as e:
        print(f"[WARNING] Не удалось удалить старый Outline ключ (возможно уже удален): {e}")

# Удаляем старый ключ из базы
cursor.execute("DELETE FROM keys WHERE id = ?", (key_data['id'],))
```

**Для V2Ray:**
```python
# Удаляем старый ключ из V2Ray сервера
cursor.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (old_server_id,))
old_server_data = cursor.fetchone()
if old_server_data:
    old_api_url, old_api_key = old_server_data
    server_config = {'api_url': old_api_url, 'api_key': old_api_key}
    protocol_client = ProtocolFactory.create_protocol(old_protocol, server_config)
    try:
        old_uuid = key_data['v2ray_uuid']
        await protocol_client.delete_user(old_uuid)
    except Exception as e:
        print(f"[WARNING] Не удалось удалить старый V2Ray ключ (возможно уже удален): {e}")

# Удаляем старый ключ из базы
cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_data['id'],))
```

**✅ Соответствует требованию 2:** Старый ключ удаляется из базы и с VPN сервера

#### 4. Создание нового ключа

**Для Outline:**
```python
# Создаём новый Outline ключ
key = await asyncio.get_event_loop().run_in_executor(None, create_key, api_url, cert_sha256)

# Добавляем новый ключ с тем же сроком действия и email
cursor.execute(
    "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    (new_server_id, user_id, key["accessUrl"], now + remaining, key["id"], now, old_email, key_data['tariff_id'])
)
```

**Для V2Ray:**
```python
# Создаём новый V2Ray ключ
server_config = {'api_url': api_url, 'api_key': api_key}
protocol_client = ProtocolFactory.create_protocol(new_protocol, server_config)
user_data = await protocol_client.create_user(old_email or f"user_{user_id}@veilbot.com")

# Добавляем новый ключ с тем же сроком действия и email
cursor.execute(
    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)",
    (new_server_id, user_id, user_data['uuid'], old_email or f"user_{user_id}@veilbot.com", now, now + remaining, key_data['tariff_id'])
)
```

## Проверка соответствия требованиям

### ✅ Требование 1: Выбор ключа при нескольких ключах
- **Логика:** `if len(all_keys) == 1: ... else: await show_protocol_change_menu(...)`
- **Меню:** Показывается список всех ключей с информацией о протоколе, стране, тарифе и сроке действия
- **Выбор:** Пользователь может выбрать конкретный ключ для смены протокола

### ✅ Требование 2: Удаление старого ключа
- **Из базы данных:** ✅ `DELETE FROM keys/v2ray_keys WHERE id = ?`
- **С VPN сервера:** ✅ Используется правильный URL старого сервера для удаления

### ✅ Требование 3: Создание нового ключа
- **Новый протокол:** ✅ `new_protocol = "v2ray" if old_protocol == "outline" else "outline"`
- **Та же страна:** ✅ `WHERE country = ? AND protocol = ?`
- **Новый сервер:** ✅ Выбирается сервер с новым протоколом в той же стране

## Дополнительные особенности

### Сохранение данных
- ✅ **Email:** Сохраняется тот же email
- ✅ **Срок действия:** Сохраняется оставшееся время
- ✅ **Тариф:** Сохраняется тот же тариф

### Обработка ошибок
- ✅ **Outline:** Ошибка удаления старого ключа не прерывает процесс
- ✅ **V2Ray:** Ошибка удаления старого ключа не прерывает процесс
- ✅ **Создание:** Обработка ошибок создания нового ключа

### Уведомления
- ✅ **Пользователю:** Отправляется новый ключ
- ✅ **Админу:** Отправляется уведомление о смене протокола

## Проблемы в коде

### ❌ ПРОБЛЕМА НАЙДЕНА!

**В функции `change_protocol_for_key()` для Outline:**
```python
# Строка 1472: используется неправильный URL для удаления
await asyncio.get_event_loop().run_in_executor(None, delete_key, api_url, old_cert_sha256[0], key_data['id'])
```

**Проблема:** Используется `api_url` от **нового** сервера, а не от старого! Это означает, что бот пытается удалить ключ с нового сервера, а не с того, где он был создан.

**Должно быть:**
```python
# Получить данные старого сервера
cursor.execute("SELECT api_url, cert_sha256 FROM servers WHERE id = ?", (old_server_id,))
old_server_data = cursor.fetchone()
if old_server_data:
    old_api_url, old_cert_sha256 = old_server_data
    await asyncio.get_event_loop().run_in_executor(None, delete_key, old_api_url, old_cert_sha256, key_data['key_id'])
```

## Заключение

**✅ Логика в целом соответствует требованиям:**

1. **Выбор ключа:** Корректно работает для нескольких ключей
2. **Удаление старого ключа:** Работает для V2Ray, но есть ошибка для Outline
3. **Создание нового ключа:** Корректно создается на новом протоколе в той же стране

**❌ Нужно исправить:** Ошибку удаления старого Outline ключа (используется неправильный URL сервера)

**Статус:** Требует исправления для Outline 