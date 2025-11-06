# Тесты VeilBot

## Структура тестов

```
tests/
├── conftest.py              # Конфигурация pytest и фикстуры
├── validators/              # Тесты валидаторов
│   └── test_input_validator.py
├── bot/
│   ├── services/            # Тесты сервисов бота
│   │   ├── test_free_tariff.py
│   │   ├── test_tariff_service.py
│   │   ├── test_key_creation.py
│   │   └── test_key_management.py
│   └── handlers/            # Интеграционные тесты handlers (TODO)
└── utils/                   # Тесты утилит (TODO)
```

## Запуск тестов

### Все тесты
```bash
pytest tests/ -v
```

### Конкретный модуль
```bash
pytest tests/validators/ -v
pytest tests/bot/services/ -v
```

### Конкретный тест
```bash
pytest tests/validators/test_input_validator.py::TestInputValidator::test_validate_email_valid -v
```

### С покрытием кода
```bash
pytest tests/ --cov=bot --cov=validators --cov-report=html
```

## Типы тестов

### Unit тесты
- **validators**: Тесты валидации входных данных
- **bot/services**: Тесты бизнес-логики сервисов
- Быстрые, изолированные, используют моки

### Интеграционные тесты
- **bot/handlers**: Тесты обработчиков Telegram сообщений
- Требуют моки для aiogram и БД
- Проверяют взаимодействие компонентов

## Фикстуры

### `temp_db`
Создает временную SQLite базу данных для тестов с полной структурой таблиц.

### `mock_cursor`
Курсор для работы с временной БД.

### `mock_message`
Мок Telegram сообщения (aiogram.types.Message).

### `mock_bot`
Мок Telegram бота (aiogram.Bot).

## Покрытие

Текущее покрытие: ~15-20% (в процессе улучшения)

**Покрыто**:
- ✅ Валидаторы (InputValidator)
- ✅ Бесплатные тарифы (free_tariff)
- ✅ Работа с тарифами (tariff_service)
- ✅ Создание ключей (key_creation - частично)
- ✅ Управление ключами (key_management - частично)

**В планах**:
- ⏳ Handlers (интеграционные тесты)
- ⏳ Фоновые задачи (background_tasks)
- ⏳ Обработка платежей (payment_service)

## Маркеры

Используйте маркеры для категоризации тестов:

```python
@pytest.mark.unit
def test_something():
    ...

@pytest.mark.integration
def test_handler():
    ...

@pytest.mark.slow
def test_long_operation():
    ...

@pytest.mark.critical
def test_important_feature():
    ...
```

Запуск по маркерам:
```bash
pytest -m unit          # Только unit тесты
pytest -m "not slow"    # Исключить медленные тесты
pytest -m critical       # Только критичные тесты
```

