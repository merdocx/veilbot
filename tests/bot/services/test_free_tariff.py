"""
Unit тесты для bot/services/free_tariff.py
"""
import pytest
import time
from bot.services.free_tariff import (
    check_free_tariff_limit_by_protocol_and_country,
    check_free_tariff_limit,
    check_free_tariff_limit_by_protocol,
    record_free_key_usage
)


class TestFreeTariff:
    """Тесты для функций бесплатных тарифов"""
    
    def test_check_free_tariff_limit_new_user(self, mock_cursor):
        """Тест проверки лимита для нового пользователя"""
        user_id = 12345
        
        # Новый пользователь не должен иметь бесплатных ключей
        result = check_free_tariff_limit(mock_cursor, user_id)
        assert result is False, "Новый пользователь не должен иметь бесплатных ключей"
    
    def test_check_free_tariff_limit_after_usage(self, mock_cursor):
        """Тест проверки лимита после использования бесплатного ключа"""
        user_id = 12345
        protocol = "outline"
        
        # Записываем использование бесплатного ключа
        record_free_key_usage(mock_cursor, user_id, protocol)
        mock_cursor.connection.commit()
        
        # Теперь пользователь должен иметь лимит
        result = check_free_tariff_limit_by_protocol(mock_cursor, user_id, protocol)
        assert result is True, "Пользователь должен иметь лимит после использования бесплатного ключа"
    
    def test_check_free_tariff_limit_by_protocol_outline(self, mock_cursor):
        """Тест проверки лимита для конкретного протокола (Outline)"""
        user_id = 12345
        protocol = "outline"
        
        # Изначально лимита нет
        result = check_free_tariff_limit_by_protocol(mock_cursor, user_id, protocol)
        assert result is False
        
        # Записываем использование для Outline
        record_free_key_usage(mock_cursor, user_id, protocol)
        mock_cursor.connection.commit()
        
        # Теперь лимит есть для Outline
        result = check_free_tariff_limit_by_protocol(mock_cursor, user_id, protocol)
        assert result is True
        
        # Но для V2Ray лимита еще нет
        result_v2ray = check_free_tariff_limit_by_protocol(mock_cursor, user_id, "v2ray")
        assert result_v2ray is False
    
    def test_check_free_tariff_limit_by_protocol_and_country(self, mock_cursor):
        """Тест проверки лимита для протокола и страны"""
        user_id = 12345
        protocol = "outline"
        country = "Россия"
        
        # Изначально лимита нет
        result = check_free_tariff_limit_by_protocol_and_country(
            mock_cursor, user_id, protocol, country
        )
        assert result is False
        
        # Записываем использование для конкретной страны
        record_free_key_usage(mock_cursor, user_id, protocol, country)
        mock_cursor.connection.commit()
        
        # Теперь лимит есть для этой страны
        result = check_free_tariff_limit_by_protocol_and_country(
            mock_cursor, user_id, protocol, country
        )
        assert result is True
        
        # Но для другой страны лимита может не быть (если не было общего использования)
        result_other = check_free_tariff_limit_by_protocol_and_country(
            mock_cursor, user_id, protocol, "США"
        )
        # Это зависит от реализации - может быть True если есть общее использование протокола
    
    def test_record_free_key_usage(self, mock_cursor):
        """Тест записи использования бесплатного ключа"""
        user_id = 12345
        protocol = "outline"
        country = "Россия"
        
        # Записываем использование
        result = record_free_key_usage(mock_cursor, user_id, protocol, country)
        assert result is True, "Запись должна быть успешной"
        
        # Проверяем, что запись создана
        mock_cursor.execute("""
            SELECT COUNT(*) FROM free_key_usage 
            WHERE user_id = ? AND protocol = ? AND country = ?
        """, (user_id, protocol, country))
        count = mock_cursor.fetchone()[0]
        assert count == 1, "Должна быть создана одна запись"
        
        # Попытка повторной записи должна вернуть False (UNIQUE constraint)
        result_duplicate = record_free_key_usage(mock_cursor, user_id, protocol, country)
        assert result_duplicate is False, "Повторная запись должна вернуть False"
    
    def test_record_free_key_usage_without_country(self, mock_cursor):
        """Тест записи использования без указания страны"""
        user_id = 12345
        protocol = "outline"
        
        # Записываем использование без страны
        result = record_free_key_usage(mock_cursor, user_id, protocol, None)
        assert result is True
        
        # Проверяем запись
        mock_cursor.execute("""
            SELECT COUNT(*) FROM free_key_usage 
            WHERE user_id = ? AND protocol = ? AND country IS NULL
        """, (user_id, protocol))
        count = mock_cursor.fetchone()[0]
        assert count == 1

