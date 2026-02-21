"""
Unit тесты для bot/services/tariff_service.py
"""
import pytest
from bot.services.tariff_service import get_tariff_by_name_and_price


class TestTariffService:
    """Тесты для функций работы с тарифами"""
    
    def test_get_tariff_by_name_and_price_found(self, mock_cursor):
        """Тест поиска тарифа по имени и цене - тариф найден"""
        # Добавляем тестовый тариф
        mock_cursor.execute("""
            INSERT INTO tariffs (name, price_rub, duration_sec, price_crypto_usd)
            VALUES (?, ?, ?, ?)
        """, ("Базовый", 100.0, 2592000, 1.0))
        mock_cursor.connection.commit()
        
        # Ищем тариф
        tariff = get_tariff_by_name_and_price(mock_cursor, "Базовый", 100.0)
        
        assert tariff is not None, "Тариф должен быть найден"
        assert tariff["name"] == "Базовый"
        assert tariff["price_rub"] == 100.0
        assert tariff["duration_sec"] == 2592000
    
    def test_get_tariff_by_name_and_price_not_found(self, mock_cursor):
        """Тест поиска тарифа - тариф не найден"""
        # Ищем несуществующий тариф
        tariff = get_tariff_by_name_and_price(mock_cursor, "Несуществующий", 999.0)
        
        assert tariff is None, "Тариф не должен быть найден"
    
    def test_get_tariff_by_name_and_price_wrong_price(self, mock_cursor):
        """Тест поиска тарифа с неправильной ценой"""
        # Добавляем тариф
        mock_cursor.execute("""
            INSERT INTO tariffs (name, price_rub, duration_sec)
            VALUES (?, ?, ?)
        """, ("Базовый", 100.0, 2592000))
        mock_cursor.connection.commit()
        
        # Ищем с неправильной ценой
        tariff = get_tariff_by_name_and_price(mock_cursor, "Базовый", 200.0)
        
        assert tariff is None, "Тариф с неправильной ценой не должен быть найден"
    
    def test_get_tariff_by_name_and_price_with_crypto(self, mock_cursor):
        """Тест поиска тарифа с крипто-ценой"""
        # Добавляем тариф с крипто-ценой
        mock_cursor.execute("""
            INSERT INTO tariffs (name, price_rub, duration_sec, price_crypto_usd)
            VALUES (?, ?, ?, ?)
        """, ("Premium", 500.0, 2592000, 5.0))
        mock_cursor.connection.commit()
        
        # Ищем тариф
        tariff = get_tariff_by_name_and_price(mock_cursor, "Premium", 500.0)
        
        assert tariff is not None
        assert tariff["price_crypto_usd"] == 5.0

