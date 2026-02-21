"""
Unit тесты для bot/services/key_creation.py
"""
import pytest
from unittest.mock import MagicMock, patch
from bot.services.key_creation import select_available_server_by_protocol


class TestKeyCreation:
    """Тесты для функций создания ключей"""
    
    def test_select_available_server_by_protocol_no_country(self, mock_cursor):
        """Тест выбора сервера без указания страны"""
        # Добавляем тестовый сервер
        mock_cursor.execute("""
            INSERT INTO servers (name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active, available_for_purchase, max_keys)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("Test Server", "https://test.com", "sha256", "test.com", "key", "/path", 
              "Россия", "outline", 1, 1, 100))
        mock_cursor.connection.commit()
        
        # Выбираем сервер
        server = select_available_server_by_protocol(mock_cursor, None, "outline", False)
        
        assert server is not None, "Сервер должен быть найден"
        assert len(server) == 7, "Сервер должен содержать 7 полей"
        assert server[1] == "Test Server", "Название сервера должно совпадать"
    
    def test_select_available_server_by_protocol_with_country(self, mock_cursor):
        """Тест выбора сервера с указанием страны"""
        # Добавляем серверы для разных стран
        mock_cursor.execute("""
            INSERT INTO servers (name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active, available_for_purchase, max_keys)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("Russia Server", "https://ru.com", "sha256", "ru.com", "key", "/path", 
              "Россия", "outline", 1, 1, 100))
        
        mock_cursor.execute("""
            INSERT INTO servers (name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active, available_for_purchase, max_keys)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("USA Server", "https://us.com", "sha256", "us.com", "key", "/path", 
              "США", "outline", 1, 1, 100))
        mock_cursor.connection.commit()
        
        # Выбираем сервер для России
        server = select_available_server_by_protocol(mock_cursor, "Россия", "outline", False)
        
        assert server is not None
        assert server[1] == "Russia Server", "Должен быть выбран сервер из России"
    
    def test_select_available_server_by_protocol_for_renewal(self, mock_cursor):
        """Тест выбора сервера для продления (for_renewal=True)"""
        # Добавляем сервер, который не доступен для покупки, но активен
        mock_cursor.execute("""
            INSERT INTO servers (name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active, available_for_purchase, max_keys)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("Renewal Server", "https://renewal.com", "sha256", "renewal.com", "key", "/path", 
              "Россия", "outline", 1, 0, 100))  # available_for_purchase = 0
        mock_cursor.connection.commit()
        
        # Для продления должен найти сервер даже если available_for_purchase = 0
        server = select_available_server_by_protocol(mock_cursor, None, "outline", True)
        
        assert server is not None, "Сервер должен быть найден для продления"
        assert server[1] == "Renewal Server"
    
    def test_select_available_server_by_protocol_not_available_for_purchase(self, mock_cursor):
        """Тест выбора сервера - сервер не доступен для покупки"""
        # Добавляем сервер, который не доступен для покупки
        mock_cursor.execute("""
            INSERT INTO servers (name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active, available_for_purchase, max_keys)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("Unavailable Server", "https://unavailable.com", "sha256", "unavailable.com", "key", "/path", 
              "Россия", "outline", 1, 0, 100))
        mock_cursor.connection.commit()
        
        # Для покупки (for_renewal=False) не должен найти сервер
        server = select_available_server_by_protocol(mock_cursor, None, "outline", False)
        
        assert server is None, "Сервер не должен быть найден для покупки"
    
    def test_select_available_server_by_protocol_v2ray(self, mock_cursor):
        """Тест выбора V2Ray сервера"""
        # Добавляем V2Ray сервер
        mock_cursor.execute("""
            INSERT INTO servers (name, api_url, cert_sha256, domain, api_key, v2ray_path, 
                                country, protocol, active, available_for_purchase, max_keys)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("V2Ray Server", "https://v2ray.com", "sha256", "v2ray.com", "key", "/v2ray", 
              "Россия", "v2ray", 1, 1, 100))
        mock_cursor.connection.commit()
        
        # Выбираем V2Ray сервер
        server = select_available_server_by_protocol(mock_cursor, None, "v2ray", False)
        
        assert server is not None
        assert server[1] == "V2Ray Server"
    
    def test_select_available_server_by_protocol_no_servers(self, mock_cursor):
        """Тест выбора сервера - серверы отсутствуют"""
        # Не добавляем серверы
        
        # Пытаемся выбрать сервер
        server = select_available_server_by_protocol(mock_cursor, None, "outline", False)
        
        assert server is None, "Сервер не должен быть найден, если их нет"

