"""
Unit тесты для InputValidator
"""
import pytest
from validators import InputValidator, ValidationError


class TestInputValidator:
    """Тесты для InputValidator"""
    
    def setup_method(self):
        """Настройка перед каждым тестом"""
        self.validator = InputValidator()
    
    def test_validate_email_valid(self):
        """Тест валидации корректных email адресов"""
        valid_emails = [
            "test@example.com",
            "user.name@domain.co.uk",
            "user+tag@example.com",
            "user123@test-domain.com"
        ]
        
        for email in valid_emails:
            assert self.validator.validate_email(email) is True, f"Email {email} должен быть валидным"
    
    def test_validate_email_invalid(self):
        """Тест валидации некорректных email адресов"""
        invalid_emails = [
            "invalid",
            "@example.com",
            "user@",
            "user@domain",
            "user name@example.com",
            "user@exam ple.com",
            ""
        ]
        
        for email in invalid_emails:
            assert self.validator.validate_email(email) is False, f"Email {email} должен быть невалидным"
    
    def test_validate_sql_injection_safe(self):
        """Тест проверки на SQL инъекции - безопасные строки"""
        safe_strings = [
            "normal_string",
            "user@example.com",
            "12345",
            "Test String",
            "русский текст"
        ]
        
        for string in safe_strings:
            assert self.validator.validate_sql_injection(string) is True, \
                f"Строка '{string}' должна быть безопасной"
    
    def test_validate_sql_injection_unsafe(self):
        """Тест проверки на SQL инъекции - опасные строки"""
        unsafe_strings = [
            "'; DROP TABLE users; --",
            "' OR '1'='1",
            "'; INSERT INTO users VALUES (1, 'hacker'); --",
            "1' UNION SELECT * FROM users--",
            "admin'--"
        ]
        
        for string in unsafe_strings:
            assert self.validator.validate_sql_injection(string) is False, \
                f"Строка '{string}' должна быть обнаружена как SQL инъекция"
    
    def test_sanitize_string(self):
        """Тест очистки строки"""
        # Тест базовой очистки
        assert self.validator.sanitize_string("  test  ") == "test"
        assert self.validator.sanitize_string("\n\ttest\n\t") == "test"
        
        # Тест ограничения длины
        long_string = "a" * 200
        sanitized = self.validator.sanitize_string(long_string, max_length=50)
        assert len(sanitized) == 50
        
        # Тест удаления опасных символов
        dangerous = "test<script>alert('xss')</script>"
        sanitized = self.validator.sanitize_string(dangerous)
        assert "<script>" not in sanitized
    
    def test_validate_country(self):
        """Тест валидации названия страны"""
        valid_countries = [
            "Россия",
            "США",
            "United Kingdom",
            "Deutschland",
            "Germany"
        ]
        
        for country in valid_countries:
            assert self.validator.validate_country(country) is True, \
                f"Страна '{country}' должна быть валидной"
        
        # Недопустимые страны
        invalid_countries = [
            "",
            "'; DROP TABLE servers; --",
            "A",  # Слишком короткое (меньше 2 символов)
            "X",  # Слишком короткое
        ]
        
        for country in invalid_countries:
            assert self.validator.validate_country(country) is False, \
                f"Страна '{country}' должна быть невалидной"
    
    def test_validate_tariff_name(self):
        """Тест валидации названия тарифа"""
        valid_names = [
            "Базовый",
            "Premium 30 дней",
            "VIP-тариф",
            "Тариф 1"
        ]
        
        for name in valid_names:
            assert self.validator.validate_tariff_name(name) is True, \
                f"Название тарифа '{name}' должно быть валидным"
        
        invalid_names = [
            "",
            "'; DROP TABLE tariffs; --",
            "A",  # Слишком короткое (меньше 2 символов)
            "X",  # Слишком короткое
        ]
        
        for name in invalid_names:
            assert self.validator.validate_tariff_name(name) is False, \
                f"Название тарифа '{name}' должно быть невалидным"

