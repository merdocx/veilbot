# Очистка формата V2Ray ключей от лишней информации

## Проблема
При нажатии на "Сменить приложение" отображалось много лишней информации: имя, uuid, qr-код данные. Это происходило из-за использования старого формата конфигурации V2Ray в нескольких местах кода.

## Найденные проблемы

### 1. Старый формат конфигурации V2Ray
В нескольких местах использовался старый WebSocket формат вместо нового Reality протокола:

**Проблемные места:**
- Строка 618: `handle_free_tariff_with_protocol` - продление существующего ключа
- Строка 665: `create_new_key_flow_with_protocol` - создание нового ключа
- Строка 601: `handle_free_tariff_with_protocol` - продление Outline ключа

### 2. Неправильные вызовы функций форматирования
Использовалась старая функция `format_key_message_with_protocol` вместо унифицированной `format_key_message_unified`.

## Исправления

### 1. Обновлен формат конфигурации V2Ray

**Было (старый WebSocket формат):**
```python
config = f"vless://{v2ray_uuid}@{domain}:443?path={path}&security=tls&type=ws#VeilBot-V2Ray"
```

**Стало (новый Reality формат):**
```python
config = f"vless://{v2ray_uuid}@{domain}:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
```

### 2. Добавлена передача email в get_user_config

**Было:**
```python
config = await protocol_client.get_user_config(user_data['uuid'], {
    'domain': server[4],
    'port': 443,
    'path': server[6] or '/v2ray'
})
```

**Стало:**
```python
config = await protocol_client.get_user_config(user_data['uuid'], {
    'domain': server[4],
    'port': 443,
    'path': server[6] or '/v2ray',
    'email': email or f"user_{user_id}@veilbot.com"
})
```

### 3. Заменены все вызовы форматирования

**Было:**
```python
format_key_message_with_protocol(config, protocol, tariff)
```

**Стало:**
```python
format_key_message_unified(config, protocol, tariff)
```

## Исправленные функции

### 1. `handle_free_tariff_with_protocol`
- ✅ Обновлен формат конфигурации V2Ray при продлении
- ✅ Добавлена передача email в get_user_config
- ✅ Заменен вызов форматирования

### 2. `create_new_key_flow_with_protocol`
- ✅ Добавлена передача email в get_user_config
- ✅ Заменен вызов форматирования

### 3. `process_pending_paid_payments`
- ✅ Заменен вызов форматирования в автоматической выдаче ключей

## Результат

### ✅ Правильный формат V2Ray ключа:
```
vless://8287ec93-b222-47cb-9aeb-bc73898a3c27@veil-bird.ru:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=eeA7CJSPNzlYKqXAsRfFNwtcpG2wXOtgDLPqaXBV13c&sid=2680beb40ea2fde0&spx=/&type=tcp&flow=#nvipetrenko@gmail.com
```

### ✅ Единообразное сообщение при смене приложения:
```
🛡️ Ваш ключ V2Ray VLESS (коснитесь, чтобы скопировать):
`vless://...`

📦 Тариф: 1 месяц
⏱ Срок действия: 30 дней

🔧 Как подключиться:
1. Установите V2Ray клиент:
   • App Store
   • Google Play
2. Откройте приложение и нажмите «+»
3. Выберите «Импорт из буфера обмена»
4. Вставьте ключ выше
```

## Преимущества исправления

### 1. Консистентность
- Единый формат V2Ray ключей во всех функциях
- Использование унифицированной функции форматирования
- Правильные параметры Reality протокола

### 2. Чистота отображения
- Убрана лишняя техническая информация
- Только необходимая информация для пользователя
- Правильный email в конфигурации

### 3. Совместимость
- Актуальный Reality протокол
- Поддержка современных V2Ray клиентов
- Лучшая обфускация трафика

## Статус
- ✅ Все старые форматы конфигурации обновлены
- ✅ Добавлена передача email во всех местах
- ✅ Заменены все вызовы форматирования
- ✅ Сервис перезапущен и работает
- ✅ Формат полностью очищен от лишней информации

## Заключение
Теперь при нажатии на "Сменить приложение" отображается только необходимая информация без лишних технических деталей. V2Ray ключи используют правильный формат Reality протокола с реальным email пользователя во всех функциях бота. 