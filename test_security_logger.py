#!/usr/bin/env python3
"""
Тест системы безопасности и логирования VeilBot
"""

import unittest
import tempfile
import os
import json
import logging
from datetime import datetime
from security_logger import (
    SecurityLogger, SecurityEvent, log_payment_attempt, 
    log_payment_success, log_payment_failure, log_key_creation,
    log_suspicious_activity
)

class TestSecurityLogger(unittest.TestCase):
    """Тесты для системы безопасности"""
    
    def setUp(self):
        """Настройка тестов"""
        # Создаем временный файл для логов
        self.temp_log_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log')
        self.temp_log_file.close()
        
        # Создаем логгер с временным файлом
        self.security_logger = SecurityLogger(log_file=self.temp_log_file.name)
        
        # Очищаем кэш перед каждым тестом
        self.security_logger.suspicious_activity_cache.clear()
    
    def tearDown(self):
        """Очистка после тестов"""
        # Удаляем временный файл
        if os.path.exists(self.temp_log_file.name):
            os.unlink(self.temp_log_file.name)
    
    def test_security_event_creation(self):
        """Тест создания события безопасности"""
        event = SecurityEvent(
            timestamp=datetime.now().isoformat(),
            event_type='payment_attempt',
            user_id=123456789,
            action='payment_creation',
            success=True,
            details={'amount': 1000, 'protocol': 'outline'},
            risk_score=15
        )
        
        self.assertEqual(event.user_id, 123456789)
        self.assertEqual(event.event_type, 'payment_attempt')
        self.assertEqual(event.risk_score, 15)
        self.assertTrue(event.success)
    
    def test_payment_attempt_logging(self):
        """Тест логирования попытки платежа"""
        user_id = 123456789
        amount = 1000  # 10 рублей в копейках
        protocol = 'outline'
        
        # Логируем попытку платежа
        self.security_logger.log_payment_attempt(
            user_id=user_id,
            amount=amount,
            protocol=protocol,
            country='RU',
            email='test@example.com'
        )
        
        # Проверяем, что лог создан
        self.assertTrue(os.path.exists(self.temp_log_file.name))
        
        # Читаем лог и проверяем содержимое
        with open(self.temp_log_file.name, 'r', encoding='utf-8') as f:
            log_content = f.read()
        
        self.assertIn('SECURITY_EVENT', log_content)
        self.assertIn(str(user_id), log_content)
        self.assertIn(protocol, log_content)
        self.assertIn('payment_attempt', log_content)
    
    def test_payment_success_logging(self):
        """Тест логирования успешного платежа"""
        user_id = 123456789
        payment_id = 'pay_123456'
        amount = 1000
        protocol = 'outline'
        
        self.security_logger.log_payment_success(
            user_id=user_id,
            payment_id=payment_id,
            amount=amount,
            protocol=protocol,
            country='RU'
        )
        
        with open(self.temp_log_file.name, 'r', encoding='utf-8') as f:
            log_content = f.read()
        
        self.assertIn('payment_success', log_content)
        self.assertIn(payment_id, log_content)
        self.assertIn(str(user_id), log_content)
    
    def test_payment_failure_logging(self):
        """Тест логирования неудачного платежа"""
        user_id = 123456789
        amount = 1000
        protocol = 'outline'
        error = 'Payment failed'
        
        self.security_logger.log_payment_failure(
            user_id=user_id,
            amount=amount,
            protocol=protocol,
            error=error,
            country='RU'
        )
        
        with open(self.temp_log_file.name, 'r', encoding='utf-8') as f:
            log_content = f.read()
        
        self.assertIn('payment_failure', log_content)
        self.assertIn(error, log_content)
        self.assertIn(str(user_id), log_content)
    
    def test_key_creation_logging(self):
        """Тест логирования создания ключа"""
        user_id = 123456789
        key_id = 'key_123'
        protocol = 'outline'
        server_id = 1
        tariff_id = 1
        
        self.security_logger.log_key_creation(
            user_id=user_id,
            key_id=key_id,
            protocol=protocol,
            server_id=server_id,
            tariff_id=tariff_id
        )
        
        with open(self.temp_log_file.name, 'r', encoding='utf-8') as f:
            log_content = f.read()
        
        self.assertIn('key_creation', log_content)
        self.assertIn(key_id, log_content)
        self.assertIn(str(user_id), log_content)
    
    def test_suspicious_activity_logging(self):
        """Тест логирования подозрительной активности"""
        user_id = 123456789
        activity_type = 'rate_limit_exceeded'
        details = 'Too many payment attempts'
        
        self.security_logger.log_suspicious_activity(
            user_id=user_id,
            activity_type=activity_type,
            details=details
        )
        
        with open(self.temp_log_file.name, 'r', encoding='utf-8') as f:
            log_content = f.read()
        
        self.assertIn('suspicious_activity', log_content)
        self.assertIn(activity_type, log_content)
        self.assertIn(details, log_content)
        self.assertIn('SECURITY_ALERT', log_content)
    
    def test_rate_limiting(self):
        """Тест ограничения частоты запросов"""
        user_id = 123456789
        
        # Проверяем, что первые 5 попыток проходят
        for i in range(5):
            result = self.security_logger._check_rate_limit(user_id, 'payment_attempts')
            self.assertTrue(result, f"Rate limit should allow attempt {i+1}")
        
        # 6-я попытка должна быть заблокирована
        result = self.security_logger._check_rate_limit(user_id, 'payment_attempts')
        self.assertFalse(result, "Rate limit should block 6th attempt")
    
    def test_risk_score_calculation(self):
        """Тест вычисления оценки риска"""
        # Нормальный платеж
        risk_score = self.security_logger._get_risk_score(
            'payment_attempt', 123456789, 'payment_creation',
            {'amount': 1000, 'protocol': 'outline', 'country': 'RU'}
        )
        self.assertLess(risk_score, 30, "Normal payment should have low risk")
        
        # Высокорисковый платеж
        risk_score = self.security_logger._get_risk_score(
            'payment_attempt', 123456789, 'payment_creation',
            {'amount': 15000, 'protocol': 'v2ray', 'country': 'US'}
        )
        self.assertGreater(risk_score, 30, "High-risk payment should have high risk score")
    
    def test_user_risk_profile(self):
        """Тест получения профиля риска пользователя"""
        user_id = 123456789
        
        # Добавляем несколько неудачных попыток
        self.security_logger.suspicious_activity_cache[user_id] = {
            'recent_failures': 3
        }
        
        profile = self.security_logger.get_user_risk_profile(user_id)
        
        self.assertEqual(profile['user_id'], user_id)
        self.assertEqual(profile['recent_failures'], 3)
        self.assertEqual(profile['risk_level'], 'high')
    
    def test_cleanup_old_data(self):
        """Тест очистки старых данных"""
        user_id = 123456789
        
        # Добавляем старые данные
        self.security_logger.suspicious_activity_cache[f"{user_id}_payment_attempts"] = {
            'timestamps': [0],  # Очень старая запись
            'count': 1
        }
        
        # Очищаем старые данные
        self.security_logger.cleanup_old_data()
        
        # Проверяем, что старые данные удалены
        self.assertNotIn(f"{user_id}_payment_attempts", self.security_logger.suspicious_activity_cache)
    
    def test_convenience_functions(self):
        """Тест удобных функций"""
        user_id = 123456789
        amount = 1000
        protocol = 'outline'
        
        # Тестируем удобные функции
        try:
            log_payment_attempt(user_id, amount, protocol)
            log_payment_success(user_id, 'pay_123', amount, protocol)
            log_payment_failure(user_id, amount, protocol, 'test error')
            log_key_creation(user_id, 'key_123', protocol, 1, 1)
            log_suspicious_activity(user_id, 'test', 'test details')
        except Exception as e:
            self.fail(f"Convenience functions should not raise exceptions: {e}")

class TestSecurityLoggerIntegration(unittest.TestCase):
    """Интеграционные тесты системы безопасности"""
    
    def test_log_file_creation(self):
        """Тест создания файла логов"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as temp_file:
            log_file_path = temp_file.name
        
        try:
            # Создаем логгер
            logger = SecurityLogger(log_file=log_file_path)
            
            # Проверяем, что файл создан
            self.assertTrue(os.path.exists(log_file_path))
            
            # Логируем событие
            logger.log_payment_attempt(123456789, 1000, 'outline')
            
            # Проверяем, что файл содержит данные
            with open(log_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self.assertIn('SECURITY_EVENT', content)
            
        finally:
            if os.path.exists(log_file_path):
                os.unlink(log_file_path)
    
    def test_multiple_events_logging(self):
        """Тест логирования множественных событий"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as temp_file:
            log_file_path = temp_file.name
        
        try:
            logger = SecurityLogger(log_file=log_file_path)
            
            # Логируем несколько событий
            events = [
                ('payment_attempt', 123456789, 1000, 'outline'),
                ('payment_success', 123456789, 'pay_123', 1000, 'outline'),
                ('key_creation', 123456789, 'key_123', 'outline', 1, 1),
            ]
            
            for event_type, *args in events:
                if event_type == 'payment_attempt':
                    logger.log_payment_attempt(*args)
                elif event_type == 'payment_success':
                    logger.log_payment_success(*args)
                elif event_type == 'key_creation':
                    logger.log_key_creation(*args)
            
            # Проверяем, что все события записаны
            with open(log_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self.assertEqual(content.count('SECURITY_EVENT'), 3)
            
        finally:
            if os.path.exists(log_file_path):
                os.unlink(log_file_path)

if __name__ == '__main__':
    # Настройка логирования для тестов
    logging.basicConfig(level=logging.INFO)
    
    # Запуск тестов
    unittest.main(verbosity=2)
