# 🔧 Исправление проблемы с редактированием сервера Amsterdam 1

## 🎯 Проблема
При попытке деактивировать сервер Amsterdam 1 через админку изменения не сохранялись. В логах админки была ошибка валидации:

```
EDIT_SERVER_FAILED, Details: ID: 9, Validation error: 1 validation error for ServerForm
domain
  Value error, Invalid domain format [type=value_error, input_value='None', input_type=str]
```

## 🔍 Диагностика
Проблема заключалась в том, что поля формы, которые не заполнены, приходят как строки `"None"` вместо пустых строк. Валидатор `ServerForm` не мог корректно обработать такие значения.

## ✅ Решение

### 1. Исправление обработки формы
В функции `edit_server` добавлена обработка случаев, когда поля приходят как строки `"None"`:

```python
# Обработка случаев, когда поля приходят как строки "None"
if domain == "None":
    domain = ""
if api_key == "None":
    api_key = ""
if v2ray_path == "None":
    v2ray_path = "/v2ray"
```

### 2. Улучшение валидаторов
Обновлены валидаторы для корректной обработки пустых значений:

#### Валидатор domain:
```python
@validator('domain')
def validate_domain(cls, v):
    # Обработка случаев, когда v может быть "None" или None
    if v in [None, "None", ""]:
        return ""
    if not re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
        raise ValueError('Invalid domain format')
    return v.strip()
```

#### Валидатор api_key:
```python
@validator('api_key')
def validate_api_key(cls, v, values):
    # API key обязателен только для V2Ray серверов
    protocol = values.get('protocol', 'outline')
    if protocol == 'v2ray' and (not v or not v.strip() or v == "None"):
        raise ValueError("API key is required for V2Ray servers")
    # Обработка случаев, когда v может быть "None"
    if v in [None, "None", ""]:
        return ""
    return v.strip()
```

#### Валидатор v2ray_path:
```python
@validator('v2ray_path')
def validate_v2ray_path(cls, v):
    # Обработка случаев, когда v может быть "None" или None
    if v in [None, "None", ""]:
        return "/v2ray"
    if not v.startswith('/'):
        raise ValueError('V2Ray path must start with /')
    return v
```

### 3. Удаление дублирующегося валидатора
Удален дублирующийся валидатор для `v2ray_path`.

## 🧪 Тестирование
Создан и выполнен тест `test_server_edit.py`, который проверил:

- ✅ Outline сервер с пустыми V2Ray полями
- ✅ V2Ray сервер с корректными данными  
- ✅ Обработка строки "None"
- ✅ Правильная валидация API ключа для V2Ray

## 🚀 Результат
- Проблема с редактированием сервера Amsterdam 1 решена
- Админка перезапущена с исправлениями
- Все тесты валидации проходят успешно
- Теперь можно корректно деактивировать/активировать серверы

## 📋 Что можно делать теперь
1. ✅ Деактивировать сервер Amsterdam 1
2. ✅ Активировать сервер Amsterdam 1  
3. ✅ Редактировать любые другие серверы
4. ✅ Корректно обрабатывать пустые поля в формах

---

**Статус**: ✅ РЕШЕНО  
**Дата**: $(date)  
**Время решения**: ~15 минут
