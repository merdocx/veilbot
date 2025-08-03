# Исправление удаления старых ключей при перевыпуске

## Проблема
При перевыпуске ключей (кнопка "Перевыпустить ключ") старые ключи не удалялись с серверов из-за ошибки в коде.

## Найденные ошибки

### 1. Outline VPN
**Файл:** `bot.py`, строка 1641
**Проблема:** Использовался URL нового сервера вместо старого для удаления ключа

**Было:**
```python
await asyncio.get_event_loop().run_in_executor(None, delete_key, api_url, old_cert_sha256[0], key_data['id'])
```

**Стало:**
```python
cursor.execute("SELECT api_url, cert_sha256 FROM servers WHERE id = ?", (old_server_id,))
old_server_data = cursor.fetchone()
if old_server_data:
    old_api_url, old_cert_sha256 = old_server_data
    await asyncio.get_event_loop().run_in_executor(None, delete_key, old_api_url, old_cert_sha256, key_data['key_id'])
```

### 2. V2Ray
**Файл:** `bot.py`, строка 1670
**Проблема:** Использовался клиент нового сервера для удаления ключа со старого сервера

**Было:**
```python
await protocol_client.delete_user(old_uuid)
```

**Стало:**
```python
cursor.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (old_server_id,))
old_server_data = cursor.fetchone()
if old_server_data:
    old_api_url, old_api_key = old_server_data
    old_server_config = {'api_url': old_api_url, 'api_key': old_api_key}
    old_protocol_client = ProtocolFactory.create_protocol(protocol, old_server_config)
    await old_protocol_client.delete_user(old_uuid)
```

## Исправления
1. ✅ Для Outline: теперь получаем данные старого сервера и используем правильный URL
2. ✅ Для V2Ray: создаем отдельный клиент для старого сервера с правильным API ключом
3. ✅ Исправлен параметр `key_data['key_id']` вместо `key_data['id']` для Outline

## Результат
Теперь при перевыпуске ключей:
- Старые ключи корректно удаляются с исходных серверов
- Новые ключи создаются на других серверах той же страны
- Нет дублирования ключей на серверах

## Статус
- ✅ Исправления применены
- ✅ Сервис перезапущен
- ✅ Бот работает корректно 