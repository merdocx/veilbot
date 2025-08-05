# Унификация сообщений при смене приложения

## Проблема
При смене приложения (протокола) сообщения для Outline и V2Ray имели разный формат:
- **Outline**: использовал `format_key_message()` - простой формат без информации о протоколе
- **V2Ray**: использовал `format_key_message_with_protocol()` - расширенный формат с информацией о протоколе и тарифе

## Решение
Создана унифицированная функция `format_key_message_unified()` для единообразного форматирования сообщений для обоих протоколов.

## Изменения

### 1. Новая функция форматирования

```python
def format_key_message_unified(config: str, protocol: str, tariff: dict = None) -> str:
    """Унифицированное форматирование сообщения для обоих протоколов"""
    protocol_info = PROTOCOLS[protocol]
    
    # Базовая структура сообщения
    message = (
        f"*Ваш ключ {protocol_info['icon']} {protocol_info['name']}* (коснитесь, чтобы скопировать):\n"
        f"`{config}`\n\n"
    )
    
    # Добавляем информацию о тарифе, если предоставлена
    if tariff:
        message += (
            f"📦 Тариф: *{tariff['name']}*\n"
            f"⏱ Срок действия: *{format_duration(tariff['duration_sec'])}*\n\n"
        )
    
    # Добавляем инструкции по подключению
    message += (
        f"🔧 *Как подключиться:*\n"
        f"{get_protocol_instructions(protocol)}"
    )
    
    return message
```

### 2. Обновление функции смены протокола

**Для Outline:**
```python
# Было:
await message.answer(format_key_message(key["accessUrl"]), ...)

# Стало:
await message.answer(format_key_message_unified(key["accessUrl"], new_protocol, tariff), ...)
```

**Для V2Ray:**
```python
# Было:
await message.answer(format_key_message_with_protocol(config, new_protocol, tariff), ...)

# Стало:
await message.answer(format_key_message_unified(config, new_protocol, tariff), ...)
```

## Единый формат сообщений

### Для Outline:
```
🔒 Ваш ключ Outline VPN (коснитесь, чтобы скопировать):
`ss://...`

📦 Тариф: Месяц
⏱ Срок действия: 30 дней

🔧 Как подключиться:
1. Установите Outline:
   • App Store
   • Google Play
2. Откройте приложение и нажмите «Добавить сервер» или «+»
3. Вставьте ключ выше
```

### Для V2Ray:
```
🛡️ Ваш ключ V2Ray VLESS (коснитесь, чтобы скопировать):
`vless://...`

📦 Тариф: Месяц
⏱ Срок действия: 30 дней

🔧 Как подключиться:
1. Установите V2Ray клиент:
   • App Store
   • Google Play
2. Откройте приложение и нажмите «+»
3. Выберите «Импорт из буфера обмена»
4. Вставьте ключ выше
```

## Преимущества унификации

### 1. Единообразие
- Одинаковая структура сообщений для обоих протоколов
- Консистентный пользовательский опыт
- Легче поддерживать и обновлять

### 2. Информативность
- Всегда отображается информация о протоколе (иконка + название)
- Информация о тарифе и сроке действия
- Специфичные инструкции для каждого протокола

### 3. Гибкость
- Функция принимает опциональный параметр `tariff`
- Можно использовать как с тарифом, так и без него
- Легко расширять для новых протоколов

## Статус
- ✅ Новая функция создана
- ✅ Outline сообщения обновлены
- ✅ V2Ray сообщения обновлены
- ✅ Сервис перезапущен
- ✅ Единый формат для обоих протоколов

## Заключение
Теперь при смене приложения пользователи получают единообразные сообщения с полной информацией о протоколе, тарифе и инструкциями по подключению, независимо от выбранного протокола.

---

## Дополнительные обновления (2025-08-03)

### Исправление формата V2Ray ключей

**Проблема:** При смене на V2Ray в конфигурации использовался хардкодированный email `#VeilBot-V2Ray` вместо реального email пользователя.

**Решение:** Обновлена функция `get_user_config` в V2Ray для использования реального email пользователя.

### Изменения в `vpn_protocols.py`

**Обновлена функция `get_user_config`:**
```python
# Добавлен параметр email в server_config
email = server_config.get('email', 'VeilBot-V2Ray')

# Обновлен fallback конфигурации
return f"vless://{user_id}@{domain}:{port}?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email}"
```

### Изменения в `bot.py`

**Обновлены функции `change_protocol_for_key` и `reissue_specific_key`:**
```python
# Передача email в server_config
config = await protocol_client.get_user_config(user_data['uuid'], {
    'domain': domain,
    'port': 443,
    'path': v2ray_path or '/v2ray',
    'email': old_email or f"user_{user_id}@veilbot.com"  # Добавлен email
})
```

### Результат

**Теперь V2Ray ключи имеют правильный формат:**
```
vless://8287ec93-b222-47cb-9aeb-bc73898a3c27@veil-bird.ru:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=eeA7CJSPNzlYKqXAsRfFNwtcpG2wXOtgDLPqaXBV13c&sid=2680beb40ea2fde0&spx=/&type=tcp&flow=#nvipetrenko@gmail.com
```

**Вместо старого формата:**
```
vless://8287ec93-b222-47cb-9aeb-bc73898a3c27@veil-bird.ru:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=eeA7CJSPNzlYKqXAsRfFNwtcpG2wXOtgDLPqaXBV13c&sid=2680beb40ea2fde0&spx=/&type=tcp&flow=#VeilBot-V2Ray
```

### Статус обновления
- ✅ Email пользователя передается в конфигурацию V2Ray
- ✅ Fallback конфигурация использует реальный email
- ✅ Функции смены протокола и перевыпуска обновлены
- ✅ Сервис перезапущен и работает
- ✅ Формат ключей соответствует требованиям

## Итоговое заключение
Теперь при смене приложения на V2Ray пользователи получают ключи с правильным email в конфигурации, что обеспечивает корректную работу и идентификацию ключей в клиентских приложениях. 