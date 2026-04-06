"""
Unit тесты для bot/services/key_management.py
"""
import pytest
import time
from unittest.mock import MagicMock, patch
from bot.services.key_management import (
    check_server_availability,
    find_alternative_server,
    extend_existing_key
)


class TestKeyManagement:
    """Тесты для функций управления ключами"""
    
    @patch('requests.get')
    def test_check_server_availability_v2ray_success(self, mock_get):
        """Тест проверки доступности V2Ray сервера - успех"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        
        result = check_server_availability("https://v2ray.com", "sha256", "v2ray")
        
        assert result is True
        mock_get.assert_called_once_with(
            "https://v2ray.com/", verify=False, timeout=10
        )
    
    @patch('requests.get')
    def test_check_server_availability_v2ray_http_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response
        assert check_server_availability("https://v2ray.com", "sha256", "v2ray") is False
    
    @patch('requests.get')
    def test_check_server_availability_exception(self, mock_get):
        """Тест проверки доступности сервера - исключение"""
        mock_get.side_effect = Exception("Connection error")
        
        result = check_server_availability("https://test.com", "sha256", "v2ray")
        
        assert result is False
    
    def test_find_alternative_server_with_country(self, mock_cursor):
        """Тест поиска альтернативного сервера с указанием страны"""
        # Добавляем два сервера в одной стране
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (1, "Server 1", "https://server1.com", "sha256", "server1.com", "key", "/path", 
              "Россия", "v2ray", 1))
        
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (2, "Server 2", "https://server2.com", "sha256", "server2.com", "key", "/path", 
              "Россия", "v2ray", 1))
        mock_cursor.connection.commit()
        
        # Ищем альтернативный сервер, исключая Server 1
        server = find_alternative_server(mock_cursor, "Россия", "v2ray", 1)
        
        assert server is not None
        assert server[0] == 2, "Должен быть найден Server 2"
        assert server[1] == "Server 2"
    
    def test_find_alternative_server_without_country(self, mock_cursor):
        """Тест поиска альтернативного сервера без указания страны"""
        # Добавляем серверы
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (1, "Server 1", "https://server1.com", "sha256", "server1.com", "key", "/path", 
              "Россия", "v2ray", 1))
        
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (2, "Server 2", "https://server2.com", "sha256", "server2.com", "key", "/path", 
              "США", "v2ray", 1))
        mock_cursor.connection.commit()
        
        # Ищем альтернативный сервер, исключая Server 1
        server = find_alternative_server(mock_cursor, None, "v2ray", 1)
        
        assert server is not None
        assert server[0] == 2
    
    def test_find_alternative_server_not_found(self, mock_cursor):
        """Тест поиска альтернативного сервера - не найден"""
        # Добавляем только один сервер
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (1, "Server 1", "https://server1.com", "sha256", "server1.com", "key", "/path", 
              "Россия", "v2ray", 1))
        mock_cursor.connection.commit()
        
        # Ищем альтернативный сервер, исключая единственный
        server = find_alternative_server(mock_cursor, "Россия", "v2ray", 1)
        
        assert server is None, "Альтернативный сервер не должен быть найден"
    
    def test_extend_existing_key_not_expired(self, mock_cursor):
        """Тест продления неистекшего ключа"""
        now = int(time.time())
        expiry_at = now + 86400  # Истекает через 24 часа
        duration = 2592000  # Продлеваем на 30 дней
        
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, country, protocol, active)
            VALUES (1, 'S1', 'https://h', 'c', 'd', 'k', '/v', 'RU', 'v2ray', 1)
        """)
        mock_cursor.execute("""
            INSERT INTO subscriptions (id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (1, 12345, "token-1", now, expiry_at, None))
        
        mock_cursor.execute("""
            INSERT INTO v2ray_keys (id, server_id, user_id, v2ray_uuid, created_at, subscription_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (1, 1, 12345, "uuid-test", now, 1))
        mock_cursor.connection.commit()
        
        mock_cursor.execute("SELECT id FROM v2ray_keys WHERE id = ?", (1,))
        key_row = mock_cursor.fetchone()
        # Получаем expiry_at из подписки для передачи в функцию
        mock_cursor.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (1,))
        sub_expiry = mock_cursor.fetchone()[0]
        existing_key = (key_row[0], sub_expiry)  # (id, expiry_at)
        
        # Продлеваем
        extend_existing_key(mock_cursor, existing_key, duration)
        mock_cursor.connection.commit()
        
        # ВАЖНО: Проверяем новое время истечения в подписке, а не в ключе
        mock_cursor.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (1,))
        new_expiry = mock_cursor.fetchone()[0]
        
        expected_expiry = expiry_at + duration
        assert new_expiry == expected_expiry, \
            f"Время истечения должно быть {expected_expiry}, получено {new_expiry}"
    
    def test_extend_existing_key_expired(self, mock_cursor):
        """Тест продления истекшего ключа"""
        now = int(time.time())
        expiry_at = now - 86400  # Истек 24 часа назад
        duration = 2592000  # Продлеваем на 30 дней
        
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, country, protocol, active)
            VALUES (1, 'S1', 'https://h', 'c', 'd', 'k', '/v', 'RU', 'v2ray', 1)
        """)
        mock_cursor.execute("""
            INSERT INTO subscriptions (id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (1, 12345, "token-1", now - 200000, expiry_at, None))
        
        mock_cursor.execute("""
            INSERT INTO v2ray_keys (id, server_id, user_id, v2ray_uuid, created_at, subscription_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (1, 1, 12345, "uuid-test", now - 200000, 1))
        mock_cursor.connection.commit()
        
        mock_cursor.execute("SELECT id FROM v2ray_keys WHERE id = ?", (1,))
        key_row = mock_cursor.fetchone()
        # Получаем expiry_at из подписки для передачи в функцию
        mock_cursor.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (1,))
        sub_expiry = mock_cursor.fetchone()[0]
        existing_key = (key_row[0], sub_expiry)  # (id, expiry_at)
        
        # Продлеваем
        extend_existing_key(mock_cursor, existing_key, duration)
        mock_cursor.connection.commit()
        
        # ВАЖНО: Проверяем новое время истечения в подписке (должно быть от текущего времени)
        mock_cursor.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (1,))
        new_expiry = mock_cursor.fetchone()[0]
        
        expected_min = now + duration - 10  # Минимум (с допуском на время выполнения)
        expected_max = now + duration + 10  # Максимум
        
        assert expected_min <= new_expiry <= expected_max, \
            f"Время истечения должно быть около {now + duration}, получено {new_expiry}"
    
    def test_extend_existing_key_with_email(self, mock_cursor):
        """Тест продления ключа с обновлением email"""
        now = int(time.time())
        expiry_at = now + 86400
        duration = 2592000
        email = "test@example.com"
        
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, country, protocol, active)
            VALUES (1, 'S1', 'https://h', 'c', 'd', 'k', '/v', 'RU', 'v2ray', 1)
        """)
        mock_cursor.execute("""
            INSERT INTO subscriptions (id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (1, 12345, "token-1", now, expiry_at + duration, None))
        
        mock_cursor.execute("""
            INSERT INTO v2ray_keys (id, server_id, user_id, v2ray_uuid, created_at, email, subscription_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (1, 1, 12345, "uuid-test", now, "old@example.com", 1))
        mock_cursor.connection.commit()
        
        mock_cursor.execute("SELECT id FROM v2ray_keys WHERE id = ?", (1,))
        key_row = mock_cursor.fetchone()
        mock_cursor.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (1,))
        sub_expiry = mock_cursor.fetchone()[0]
        existing_key = (key_row[0], sub_expiry)
        
        # Продлеваем с новым email
        extend_existing_key(mock_cursor, existing_key, duration, email=email)
        mock_cursor.connection.commit()
        
        # Проверяем email
        mock_cursor.execute("SELECT email FROM v2ray_keys WHERE id = ?", (1,))
        new_email = mock_cursor.fetchone()[0]
        assert new_email == email, f"Email должен быть {email}, получен {new_email}"
    
    def test_extend_existing_key_with_tariff_id(self, mock_cursor):
        """Тест продления ключа с обновлением tariff_id"""
        now = int(time.time())
        expiry_at = now + 86400
        duration = 2592000
        tariff_id = 5
        
        mock_cursor.execute(
            "INSERT INTO tariffs (id, name, price_rub, duration_sec) VALUES (1, 'T1', 1, 3600), (5, 'T5', 1, 3600)"
        )
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, country, protocol, active)
            VALUES (1, 'S1', 'https://h', 'c', 'd', 'k', '/v', 'RU', 'v2ray', 1)
        """)
        mock_cursor.execute("""
            INSERT INTO subscriptions (id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (1, 12345, "token-1", now, expiry_at + duration, None))
        
        mock_cursor.execute("""
            INSERT INTO v2ray_keys (id, server_id, user_id, v2ray_uuid, created_at, tariff_id, subscription_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (1, 1, 12345, "uuid-test", now, 1, 1))
        mock_cursor.connection.commit()
        
        mock_cursor.execute("SELECT id FROM v2ray_keys WHERE id = ?", (1,))
        key_row = mock_cursor.fetchone()
        mock_cursor.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (1,))
        sub_expiry = mock_cursor.fetchone()[0]
        existing_key = (key_row[0], sub_expiry)
        
        # Продлеваем с новым tariff_id
        extend_existing_key(mock_cursor, existing_key, duration, tariff_id=tariff_id)
        mock_cursor.connection.commit()
        
        # Проверяем tariff_id
        mock_cursor.execute("SELECT tariff_id FROM v2ray_keys WHERE id = ?", (1,))
        new_tariff_id = mock_cursor.fetchone()[0]
        assert new_tariff_id == tariff_id, f"Tariff ID должен быть {tariff_id}, получен {new_tariff_id}"
    
    def test_extend_existing_key_with_email_and_tariff_id(self, mock_cursor):
        """Тест продления ключа с обновлением email и tariff_id"""
        now = int(time.time())
        expiry_at = now + 86400
        duration = 2592000
        email = "new@example.com"
        tariff_id = 5
        
        mock_cursor.execute(
            "INSERT INTO tariffs (id, name, price_rub, duration_sec) VALUES (1, 'T1', 1, 3600), (5, 'T5', 1, 3600)"
        )
        mock_cursor.execute("""
            INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path, country, protocol, active)
            VALUES (1, 'S1', 'https://h', 'c', 'd', 'k', '/v', 'RU', 'v2ray', 1)
        """)
        mock_cursor.execute("""
            INSERT INTO subscriptions (id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (1, 12345, "token-1", now, expiry_at + duration, None))
        
        mock_cursor.execute("""
            INSERT INTO v2ray_keys (id, server_id, user_id, v2ray_uuid, created_at, email, tariff_id, subscription_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (1, 1, 12345, "uuid-test", now, "old@example.com", 1, 1))
        mock_cursor.connection.commit()
        
        mock_cursor.execute("SELECT id FROM v2ray_keys WHERE id = ?", (1,))
        key_row = mock_cursor.fetchone()
        mock_cursor.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (1,))
        sub_expiry = mock_cursor.fetchone()[0]
        existing_key = (key_row[0], sub_expiry)
        
        # Продлеваем с новыми данными
        extend_existing_key(mock_cursor, existing_key, duration, email=email, tariff_id=tariff_id)
        mock_cursor.connection.commit()
        
        # Проверяем оба поля
        mock_cursor.execute("SELECT email, tariff_id FROM v2ray_keys WHERE id = ?", (1,))
        row = mock_cursor.fetchone()
        assert row[0] == email
        assert row[1] == tariff_id

