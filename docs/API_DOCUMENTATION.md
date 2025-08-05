# 📚 Документация API VPN сервера

## 🌐 Базовый URL
```
https://veil-bird.ru/api
```

## 🔐 Аутентификация
Все API эндпоинты (кроме корневого `/` и `/api/`) требуют аутентификации с помощью API ключа.

**API ключ загружается из переменных окружения** из файла `.env`

**Заголовок:** `X-API-Key: YOUR_API_KEY`

**Пример:**
```bash
curl -k -H "X-API-Key: YOUR_API_KEY" "https://veil-bird.ru/api/keys"
```

### 🔑 Получение API ключа
API ключ хранится в файле `/root/vpn-server/.env` в переменной `VPN_API_KEY`.

Для генерации нового API ключа используйте:
```bash
python3 /root/vpn-server/generate_api_key.py
```

### 🔒 Безопасность
- **HTTPS обязателен** - все соединения шифруются
- **API ключ секретный** - не передавайте третьим лицам
- **Регулярно меняйте ключ** - для повышения безопасности

## 📋 Общие заголовки
```http
Content-Type: application/json
Accept: application/json
```

## 📊 Коды ответов
- `200` - Успешный запрос
- `400` - Неверный запрос
- `401` - Неавторизованный доступ
- `404` - Ресурс не найден
- `500` - Внутренняя ошибка сервера
- `503` - Сервис недоступен

---

## 🔑 Управление ключами

### 1. Создание ключа
**POST** `/api/keys`

Создает новый VPN ключ с указанным именем и назначает индивидуальный порт.

#### Запрос:
```json
{
  "name": "Мой VPN ключ"
}
```

#### Ответ:
```json
{
  "id": "84570736-8bf5-47af-92d4-3a08f2693ef8",
  "name": "Мой VPN ключ",
  "uuid": "44ed718f-9f5d-4bd9-8585-e5a875cd3858",
  "created_at": "2025-08-02T15:22:39.822640",
  "is_active": true,
  "port": 10001
}
```

#### Пример:
```bash
curl -X POST "https://veil-bird.ru/api/keys" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"name": "Мой VPN ключ"}'
```

---

### 2. Получение списка ключей
**GET** `/api/keys`

Возвращает список всех VPN ключей с информацией о назначенных портах.

#### Ответ:
```json
[
  {
    "id": "84570736-8bf5-47af-92d4-3a08f2693ef8",
    "name": "Мой VPN ключ",
    "uuid": "44ed718f-9f5d-4bd9-8585-e5a875cd3858",
    "created_at": "2025-08-02T15:22:39.822640",
    "is_active": true,
    "port": 10001
  }
]
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/keys" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 3. Получение информации о ключе
**GET** `/api/keys/{key_id}`

Возвращает информацию о конкретном VPN ключе.

#### Параметры:
- `key_id` - ID или UUID ключа

#### Ответ:
```json
{
  "id": "84570736-8bf5-47af-92d4-3a08f2693ef8",
  "name": "Мой VPN ключ",
  "uuid": "44ed718f-9f5d-4bd9-8585-e5a875cd3858",
  "created_at": "2025-08-02T15:22:39.822640",
  "is_active": true,
  "port": 10001
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/keys/84570736-8bf5-47af-92d4-3a08f2693ef8" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 4. Удаление ключа
**DELETE** `/api/keys/{key_id}`

Удаляет VPN ключ и освобождает назначенный порт.

#### Параметры:
- `key_id` - ID или UUID ключа

#### Ответ:
```json
{
  "message": "Key deleted successfully"
}
```

#### Пример:
```bash
curl -X DELETE "https://veil-bird.ru/api/keys/84570736-8bf5-47af-92d4-3a08f2693ef8" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 5. Получение конфигурации клиента
**GET** `/api/keys/{key_id}/config`

Возвращает конфигурацию клиента для подключения к VPN.

#### Параметры:
- `key_id` - ID или UUID ключа

#### Ответ:
```json
{
  "key": {
    "id": "84570736-8bf5-47af-92d4-3a08f2693ef8",
    "name": "Мой VPN ключ",
    "uuid": "44ed718f-9f5d-4bd9-8585-e5a875cd3858",
    "created_at": "2025-08-02T15:22:39.822640",
    "is_active": true,
    "port": 10001
  },
  "client_config": "vless://44ed718f-9f5d-4bd9-8585-e5a875cd3858@veil-bird.ru:10001?security=reality&sni=www.microsoft.com&fp=chrome&pbk=...&sid=...&type=tcp#Мой%20VPN%20ключ"
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/keys/84570736-8bf5-47af-92d4-3a08f2693ef8/config" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## 📊 Мониторинг трафика

### 1. Получение трафика для всех ключей
**GET** `/api/traffic/simple`

Возвращает статистику трафика для всех активных ключей на основе активных соединений.

#### Ответ:
```json
{
  "status": "success",
  "data": {
    "ports": {
      "10001": {
        "port": 10001,
        "connections": 37,
        "total_bytes": 33323549,
        "rx_bytes": 16661774,
        "tx_bytes": 16661774,
        "total_formatted": "31.78 MB",
        "rx_formatted": "15.89 MB",
        "tx_formatted": "15.89 MB",
        "traffic_rate": 134128.74,
        "interface_traffic": {
          "rx_bytes": 5754086174,
          "tx_bytes": 5486110273,
          "total_bytes": 11240196447,
          "timestamp": 1754307939.2700043
        },
        "connection_details": [
          {
            "local": "[::ffff:146.103.100.14]:10001",
            "remote": "[::ffff:109.252.116.174]:1896",
            "state": "ESTAB"
          }
        ],
        "timestamp": 1754307939.2557962,
        "source": "simple_monitor",
        "method": "connection_based_estimation",
        "uuid": "e9828d67-08e2-4942-815d-61f41b3dacf7"
      }
    },
    "total_connections": 37,
    "total_bytes": 33323549,
    "timestamp": 1754307939.2557921
  },
  "timestamp": "2025-08-04T14:45:39.255792"
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/traffic/simple" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 2. Получение трафика для конкретного ключа
**GET** `/api/keys/{key_id}/traffic/simple`

Возвращает статистику трафика для конкретного ключа на основе активных соединений.

#### Параметры:
- `key_id` - ID или UUID ключа

#### Ответ:
```json
{
  "status": "success",
  "key": {
    "id": "11461131-0644-438d-9429-cb5e7f60fd80",
    "name": "nvipetrenko@gmail.con",
    "uuid": "e9828d67-08e2-4942-815d-61f41b3dacf7",
    "created_at": "2025-08-04T14:34:13.878594",
    "is_active": true,
    "port": 10001
  },
  "traffic": {
    "port": 10001,
    "connections": 37,
    "total_bytes": 33323549,
    "rx_bytes": 16661774,
    "tx_bytes": 16661774,
    "total_formatted": "31.78 MB",
    "rx_formatted": "15.89 MB",
    "tx_formatted": "15.89 MB",
    "traffic_rate": 134128.74,
    "interface_traffic": {
      "rx_bytes": 5754086174,
      "tx_bytes": 5486110273,
      "total_bytes": 11240196447,
      "timestamp": 1754307939.2700043
    },
    "connection_details": [
      {
        "local": "[::ffff:146.103.100.14]:10001",
        "remote": "[::ffff:109.252.116.174]:1896",
        "state": "ESTAB"
      }
    ],
    "timestamp": 1754307939.2557962,
    "source": "simple_monitor",
    "method": "connection_based_estimation",
    "uuid": "e9828d67-08e2-4942-815d-61f41b3dacf7"
  },
  "timestamp": "2025-08-04T14:45:39.255792"
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/keys/11461131-0644-438d-9429-cb5e7f60fd80/traffic/simple" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 3. Сброс статистики трафика для ключа
**POST** `/api/keys/{key_id}/traffic/simple/reset`

Сбрасывает статистику трафика для конкретного ключа.

#### Параметры:
- `key_id` - ID или UUID ключа

#### Ответ:
```json
{
  "status": "success",
  "message": "Traffic stats reset successfully",
  "key_id": "11461131-0644-438d-9429-cb5e7f60fd80",
  "timestamp": "2025-08-04T14:45:39.255792"
}
```

#### Пример:
```bash
curl -X POST "https://veil-bird.ru/api/keys/11461131-0644-438d-9429-cb5e7f60fd80/traffic/simple/reset" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 4. Получение системной сводки трафика
**GET** `/api/system/traffic/summary`

Возвращает сводку системного трафика по всем интерфейсам.

#### Ответ:
```json
{
  "summary": {
    "total_system_traffic": 10485760,
    "total_system_traffic_formatted": "10.0 MB",
    "active_ports": 2,
    "interface_summary": {
      "ens3": {
        "rx_bytes": 5242880,
        "tx_bytes": 5242880,
        "total_bytes": 10485760,
        "rx_formatted": "5.0 MB",
        "tx_formatted": "5.0 MB",
        "total_formatted": "10.0 MB"
      }
    },
    "timestamp": 1234567890
  },
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/system/traffic/summary" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## 🔌 Управление портами

### 1. Получение статуса портов
**GET** `/api/system/ports`

Возвращает информацию о назначениях портов и их использовании.

#### Ответ:
```json
{
  "port_assignments": {
    "used_ports": {
      "10001": {
        "uuid": "44ed718f-9f5d-4bd9-8585-e5a875cd3858",
        "key_id": "84570736-8bf5-47af-92d4-3a08f2693ef8",
        "key_name": "Мой VPN ключ",
        "assigned_at": "2025-08-02T15:22:39.822640",
        "is_active": true
      }
    },
    "port_assignments": {
      "44ed718f-9f5d-4bd9-8585-e5a875cd3858": {
        "port": 10001,
        "key_id": "84570736-8bf5-47af-92d4-3a08f2693ef8",
        "key_name": "Мой VPN ключ",
        "assigned_at": "2025-08-02T15:22:39.822640"
      }
    },
    "created_at": "2025-08-02T15:22:39.822640",
    "last_updated": "2025-08-02T15:22:39.822640"
  },
  "used_ports": 1,
  "available_ports": 19,
  "max_ports": 20,
  "port_range": "10001-10020",
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/system/ports" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 2. Сброс всех портов
**POST** `/api/system/ports/reset`

Сбрасывает все назначения портов (используется только в экстренных случаях).

#### Ответ:
```json
{
  "message": "All ports reset successfully",
  "status": "reset",
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X POST "https://veil-bird.ru/api/system/ports/reset" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 3. Получение статуса валидации портов
**GET** `/api/system/ports/status`

Возвращает результаты валидации назначений портов.

#### Ответ:
```json
{
  "validation": {
    "valid": true,
    "issues": [],
    "total_assignments": 1,
    "total_used_ports": 1
  },
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/system/ports/status" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## ⚙️ Управление конфигурацией Xray

### 1. Получение статуса конфигурации Xray
**GET** `/api/system/xray/config-status`

Возвращает статус конфигурации Xray и информацию о inbounds.

#### Ответ:
```json
{
  "config_status": {
    "total_inbounds": 4,
    "vless_inbounds": 3,
    "api_inbounds": 1,
    "port_assignments": {
      "used_ports": {
        "10001": {
          "uuid": "44ed718f-9f5d-4bd9-8585-e5a875cd3858",
          "key_id": "84570736-8bf5-47af-92d4-3a08f2693ef8",
          "key_name": "Мой VPN ключ",
          "assigned_at": "2025-08-02T15:22:39.822640",
          "is_active": true
        }
      },
      "port_assignments": {
        "44ed718f-9f5d-4bd9-8585-e5a875cd3858": {
          "port": 10001,
          "key_id": "84570736-8bf5-47af-92d4-3a08f2693ef8",
          "key_name": "Мой VPN ключ",
          "assigned_at": "2025-08-02T15:22:39.822640"
        }
      }
    },
    "config_valid": true,
    "timestamp": "2025-08-02T15:22:39.822640"
  },
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/system/xray/config-status" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 2. Синхронизация конфигурации Xray
**POST** `/api/system/xray/sync-config`

Синхронизирует конфигурацию Xray с ключами и перезапускает сервис.

#### Ответ:
```json
{
  "message": "Xray configuration synchronized successfully",
  "status": "synced",
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X POST "https://veil-bird.ru/api/system/xray/sync-config" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 3. Валидация синхронизации конфигурации
**GET** `/api/system/xray/validate-sync`

Проверяет соответствие конфигурации Xray с ключами.

#### Ответ:
```json
{
  "validation": {
    "synchronized": true,
    "key_uuids": ["44ed718f-9f5d-4bd9-8585-e5a875cd3858"],
    "config_uuids": ["44ed718f-9f5d-4bd9-8585-e5a875cd3858"],
    "missing_in_config": [],
    "extra_in_config": [],
    "total_keys": 1,
    "total_config_clients": 1
  },
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/system/xray/validate-sync" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## 🔧 Системные эндпоинты

### 1. Проверка и обновление настроек Reality
**POST** `/api/system/verify-reality`

Проверяет и обновляет настройки Reality в конфигурации Xray.

#### Ответ:
```json
{
  "message": "Reality settings verified and updated successfully",
  "status": "verified",
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X POST "https://veil-bird.ru/api/system/verify-reality" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 2. Принудительная синхронизация конфигурации
**POST** `/api/system/sync-config`

Принудительно синхронизирует конфигурацию Xray с keys.json.

#### Ответ:
```json
{
  "message": "Configuration synchronized successfully",
  "status": "synced",
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X POST "https://veil-bird.ru/api/system/sync-config" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

### 3. Получение статуса синхронизации конфигурации
**GET** `/api/system/config-status`

Возвращает статус синхронизации конфигурации.

#### Ответ:
```json
{
  "synchronized": true,
  "keys_json_count": 1,
  "config_json_count": 1,
  "keys_json_uuids": ["44ed718f-9f5d-4bd9-8585-e5a875cd3858"],
  "config_json_uuids": ["44ed718f-9f5d-4bd9-8585-e5a875cd3858"],
  "timestamp": 1234567890
}
```

#### Пример:
```bash
curl -X GET "https://veil-bird.ru/api/system/config-status" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## 📝 Примеры использования

### Создание ключа и получение конфигурации
```bash
# Создаем ключ
KEY_RESPONSE=$(curl -s -X POST "https://veil-bird.ru/api/keys" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"name": "Мой VPN ключ"}')

# Извлекаем ID и UUID
KEY_ID=$(echo $KEY_RESPONSE | python3 -c "import json, sys; print(json.load(sys.stdin)['id'])")
KEY_UUID=$(echo $KEY_RESPONSE | python3 -c "import json, sys; print(json.load(sys.stdin)['uuid'])")

# Получаем конфигурацию клиента
curl -s -X GET "https://veil-bird.ru/api/keys/$KEY_ID/config" \
  -H "X-API-Key: YOUR_API_KEY" | \
  python3 -c "import json, sys; print(json.load(sys.stdin)['client_config'])"
```

### Мониторинг трафика
```bash
# Получаем общую статистику трафика
curl -s -X GET "https://veil-bird.ru/api/traffic/simple" \
  -H "X-API-Key: YOUR_API_KEY"

# Получаем трафик конкретного ключа
curl -s -X GET "https://veil-bird.ru/api/keys/$KEY_ID/traffic/simple" \
  -H "X-API-Key: YOUR_API_KEY"
```

### Управление портами
```bash
# Проверяем статус портов
curl -s -X GET "https://veil-bird.ru/api/system/ports" \
  -H "X-API-Key: YOUR_API_KEY"

# Сбрасываем статистику трафика
curl -s -X POST "https://veil-bird.ru/api/keys/$KEY_ID/traffic/simple/reset" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## 🎯 Особенности системы мониторинга

### Преимущества простого мониторинга:
- ✅ **Надежность** - основан на активных соединениях
- 🔍 **Точность** - отслеживает реальные соединения
- ⚡ **Производительность** - кэширование 30 секунд
- 📊 **Детализация** - показывает состояния соединений
- 🛠️ **Простота** - один метод для всех случаев

### Поддерживаемые состояния соединений:
- **ESTAB** - установленные соединения
- **LAST-ACK** - завершающиеся соединения  
- **CLOSE-WAIT** - ожидающие закрытия

### Метод оценки трафика:
- **connection_based_estimation** - оценка на основе количества соединений
- **interface_traffic** - общий трафик интерфейса
- **traffic_rate** - скорость трафика в байтах/сек

---

## 📞 Поддержка

При возникновении проблем с API обращайтесь к документации системы или создайте issue в репозитории проекта.

**Версия API:** 2.0.0  
**Дата обновления:** 4 августа 2025  
**Статус:** ✅ Актуально (обновлено - удалены устаревшие endpoints) 