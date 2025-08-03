# Анализ логики перевыпуска ключей

## Требования
Кнопка "Перевыпустить ключ" должна:
1. ✅ Удалять старый ключ из базы и с VPN сервера
2. ✅ Создавать новый ключ на том же протоколе, в той же стране, но на другом сервере

## Анализ кода

### Функция: `reissue_specific_key()`

#### 1. Подготовка данных
```python
# Получаем данные старого ключа
old_server_id = key_data['server_id']
country = key_data['country']
old_email = key_data['email']
protocol = key_data['protocol']
```

#### 2. Поиск нового сервера
```python
# Ищем другой сервер той же страны и протокола
cursor.execute("""
    SELECT id, api_url, cert_sha256, domain, v2ray_path, api_key FROM servers 
    WHERE active = 1 AND country = ? AND protocol = ? AND id != ?
""", (country, protocol, old_server_id))
```
**✅ Соответствует требованиям:** Ищет сервер той же страны и протокола, но не тот же самый (`id != old_server_id`)

#### 3. Логика для Outline VPN

**Создание нового ключа:**
```python
# Создаём новый Outline ключ на НОВОМ сервере
key = await asyncio.get_event_loop().run_in_executor(None, create_key, api_url, cert_sha256)
```

**Удаление старого ключа:**
```python
# Удаляем старый ключ из СТАРОГО сервера
cursor.execute("SELECT api_url, cert_sha256 FROM servers WHERE id = ?", (old_server_id,))
old_server_data = cursor.fetchone()
if old_server_data:
    old_api_url, old_cert_sha256 = old_server_data
    await asyncio.get_event_loop().run_in_executor(None, delete_key, old_api_url, old_cert_sha256, key_data['key_id'])
```

**Обновление базы данных:**
```python
# Удаляем старый ключ из базы
cursor.execute("DELETE FROM keys WHERE id = ?", (key_data['id'],))

# Добавляем новый ключ с тем же сроком действия и email
cursor.execute(
    "INSERT INTO keys (server_id, user_id, access_url, expiry_at, key_id, created_at, email, tariff_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    (new_server_id, user_id, key["accessUrl"], now + remaining, key["id"], now, old_email, key_data['tariff_id'])
)
```

#### 4. Логика для V2Ray

**Создание нового ключа:**
```python
# Создаём новый V2Ray ключ на НОВОМ сервере
server_config = {'api_url': api_url, 'api_key': api_key}
protocol_client = ProtocolFactory.create_protocol(protocol, server_config)
user_data = await protocol_client.create_user(old_email or f"user_{user_id}@veilbot.com")
```

**Удаление старого ключа:**
```python
# Удаляем старый ключ из СТАРОГО сервера
cursor.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (old_server_id,))
old_server_data = cursor.fetchone()
if old_server_data:
    old_api_url, old_api_key = old_server_data
    old_server_config = {'api_url': old_api_url, 'api_key': old_api_key}
    old_protocol_client = ProtocolFactory.create_protocol(protocol, old_server_config)
    await old_protocol_client.delete_user(old_uuid)
```

**Обновление базы данных:**
```python
# Удаляем старый ключ из базы
cursor.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_data['id'],))

# Добавляем новый ключ с тем же сроком действия и email
cursor.execute(
    "INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)",
    (new_server_id, user_id, user_data['uuid'], old_email or f"user_{user_id}@veilbot.com", now, now + remaining, key_data['tariff_id'])
)
```

## Проверка соответствия требованиям

### ✅ Требование 1: Удаление старого ключа
- **Из базы данных:** ✅ `DELETE FROM keys/v2ray_keys WHERE id = ?`
- **С VPN сервера:** ✅ Используется правильный URL старого сервера для удаления

### ✅ Требование 2: Создание нового ключа
- **Тот же протокол:** ✅ `protocol = key_data['protocol']`
- **Та же страна:** ✅ `country = key_data['country']`
- **Другой сервер:** ✅ `WHERE id != old_server_id`

## Дополнительные особенности

### Сохранение данных
- ✅ **Email:** Сохраняется тот же email
- ✅ **Срок действия:** Сохраняется оставшееся время
- ✅ **Тариф:** Сохраняется тот же тариф

### Обработка ошибок
- ✅ **Outline:** Ошибка удаления старого ключа не прерывает процесс
- ✅ **V2Ray:** Ошибка удаления старого ключа не прерывает процесс
- ✅ **Cleanup:** При ошибке создания нового ключа удаляется созданный пользователь

### Уведомления
- ✅ **Пользователю:** Отправляется новый ключ
- ✅ **Админу:** Отправляется уведомление о перевыпуске

## Заключение

**✅ Логика полностью соответствует требованиям:**

1. **Удаление старого ключа:** Корректно удаляется из базы и с VPN сервера
2. **Создание нового ключа:** Создается на том же протоколе, в той же стране, но на другом сервере
3. **Сохранение данных:** Все важные данные (email, срок действия, тариф) сохраняются
4. **Обработка ошибок:** Настроена корректная обработка ошибок
5. **Уведомления:** Пользователь и админ получают уведомления

**Статус:** ✅ Готово к использованию 