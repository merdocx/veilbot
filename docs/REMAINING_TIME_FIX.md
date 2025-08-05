# Исправление отображения "Осталось времени" при смене приложения

## Проблема
При нажатии на "Сменить приложение" показывалось неверное значение "Осталось времени". Вместо реального оставшегося времени отображалась полная длительность тарифа.

## Причина
В функции `format_key_message_unified` использовался `tariff['duration_sec']` (полная длительность тарифа), а не реальное оставшееся время ключа.

### Пример проблемы:
- Ключ был создан 15 дней назад на 30 дней
- Осталось времени: 15 дней
- Но показывалось: 30 дней (полная длительность тарифа)

## Исправление

### 1. Обновлена функция `format_key_message_unified`

**Было:**
```python
def format_key_message_unified(config: str, protocol: str, tariff: dict = None) -> str:
    # ...
    if tariff:
        message += (
            f"⏱ Осталось времени: *{format_duration(tariff['duration_sec'])}*\n\n"
        )
```

**Стало:**
```python
def format_key_message_unified(config: str, protocol: str, tariff: dict = None, remaining_time: int = None) -> str:
    # ...
    # Добавляем информацию о времени
    if remaining_time is not None:
        # Если передано оставшееся время, используем его
        message += (
            f"⏱ Осталось времени: *{format_duration(remaining_time)}*\n\n"
        )
    elif tariff:
        # Иначе используем длительность тарифа (для новых ключей)
        message += (
            f"⏱ Осталось времени: *{format_duration(tariff['duration_sec'])}*\n\n"
        )
```

### 2. Обновлены вызовы функции

**В `change_protocol_for_key` (смена приложения):**

**Outline:**
```python
# Было:
await message.answer(format_key_message_unified(key["accessUrl"], new_protocol, tariff), ...)

# Стало:
await message.answer(format_key_message_unified(key["accessUrl"], new_protocol, tariff, remaining), ...)
```

**V2Ray:**
```python
# Было:
await message.answer(format_key_message_unified(config, new_protocol, tariff), ...)

# Стало:
await message.answer(format_key_message_unified(config, new_protocol, tariff, remaining), ...)
```

**В `reissue_specific_key` (перевыпуск ключа):**

**Outline:**
```python
# Было:
await message.answer(format_key_message(key["accessUrl"]), ...)

# Стало:
await message.answer(format_key_message_unified(key["accessUrl"], protocol, tariff, remaining), ...)
```

**V2Ray:**
```python
# Было:
await message.answer(format_key_message_unified(config, protocol, tariff), ...)

# Стало:
await message.answer(format_key_message_unified(config, protocol, tariff, remaining), ...)
```

## Логика исправления

### 1. Вычисление оставшегося времени
```python
# Считаем оставшееся время
remaining = key_data['expiry_at'] - now
```

### 2. Передача в функцию форматирования
```python
# Передаем оставшееся время как отдельный параметр
format_key_message_unified(config, protocol, tariff, remaining)
```

### 3. Приоритет отображения
- **Если передан `remaining_time`** - показываем реальное оставшееся время
- **Если не передан** - показываем длительность тарифа (для новых ключей)

## Результат

### ✅ Правильное отображение времени:

**Было:**
```
🛡️ Ваш ключ V2Ray VLESS (коснитесь, чтобы скопировать):
vless://acf5b2c5-605a-44e9-9296-e593d876e6a6@veil-bird.ru:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=eeA7CJSPNzlYKqXAsRfFNwtcpG2wXOtgDLPqaXBV13c&sid=2680beb40ea2fde0&spx=/&type=tcp&flow=#nvipetrenko@gmail.com

⏱ Осталось времени: 30 дней

🔧 Как подключиться:
1. Установите V2Ray клиент:
   • App Store
   • Google Play
2. Откройте приложение и нажмите «+»
3. Выберите «Импорт из буфера обмена»
4. Вставьте ключ выше
```

**Стало:**
```
🛡️ Ваш ключ V2Ray VLESS (коснитесь, чтобы скопировать):
vless://acf5b2c5-605a-44e9-9296-e593d876e6a6@veil-bird.ru:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=eeA7CJSPNzlYKqXAsRfFNwtcpG2wXOtgDLPqaXBV13c&sid=2680beb40ea2fde0&spx=/&type=tcp&flow=#nvipetrenko@gmail.com

⏱ Осталось времени: 15 дней

🔧 Как подключиться:
1. Установите V2Ray клиент:
   • App Store
   • Google Play
2. Откройте приложение и нажмите «+»
3. Выберите «Импорт из буфера обмена»
4. Вставьте ключ выше
```

## Преимущества исправления

### 1. Точность информации
- ✅ Показывается реальное оставшееся время
- ✅ Пользователь видит актуальную информацию
- ✅ Нет путаницы с длительностью тарифа

### 2. Консистентность
- ✅ Единообразное отображение для всех операций
- ✅ Одинаковая логика для Outline и V2Ray
- ✅ Унифицированный формат сообщений

### 3. Обратная совместимость
- ✅ Для новых ключей показывается полная длительность
- ✅ Для существующих ключей - оставшееся время
- ✅ Функция работает в обоих режимах

## Затронутые функции

### ✅ Исправлены:
- `change_protocol_for_key` - смена приложения (Outline + V2Ray)
- `reissue_specific_key` - перевыпуск ключа (Outline + V2Ray)
- `format_key_message_unified` - унифицированное форматирование

### ✅ Проверено:
- Синтаксис всех измененных файлов
- Совместимость с существующим кодом
- Корректность вычисления времени

## Статус
- ✅ Функция `format_key_message_unified` обновлена
- ✅ Все вызовы функции исправлены
- ✅ Сервис перезапущен и работает
- ✅ Правильное отображение оставшегося времени

## Заключение
Теперь при смене приложения и перевыпуске ключей отображается корректное оставшееся время вместо полной длительности тарифа. Пользователи получают точную информацию о том, сколько времени осталось до истечения их ключа. 