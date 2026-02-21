"""
Исправленные интеграционные тесты для платежного модуля

Тестирование работы с реальной базой данных и внешними сервисами.
"""

import pytest
import asyncio
import os
from datetime import datetime

from ..config import PaymentConfig, initialize_payment_module
from ..services.payment_service import PaymentService
from ..repositories.payment_repository import PaymentRepository
from ..models.payment import Payment, PaymentStatus
from ..models.enums import PaymentCurrency, PaymentProvider


@pytest.mark.asyncio
class TestPaymentIntegration:
    """Интеграционные тесты платежного модуля"""
    
    @pytest.fixture
    def payment_config(self):
        """Конфигурация для тестов"""
        return PaymentConfig(
            yookassa_shop_id="123456",
            yookassa_api_key="test_api_key",
            yookassa_return_url="https://t.me/test_bot",
            yookassa_test_mode=True,
            database_path="vpn_test.db"
        )
    
    @pytest.fixture
    def payment_service(self, payment_config):
        """Сервис платежей для тестов"""
        factory = initialize_payment_module(payment_config)
        return factory.create_payment_service()
    
    @pytest.fixture
    def payment_repo(self, payment_config):
        """Репозиторий платежей для тестов"""
        factory = initialize_payment_module(payment_config)
        return factory.create_payment_repository()
    
    async def test_payment_creation_integration(self, payment_service, payment_repo):
        """Тест создания платежа с реальной БД"""
        # Arrange
        user_id = 123456789
        tariff_id = 1
        amount = 10000  # 100 рублей
        email = "test@example.com"
        
        # Act
        payment_id, confirmation_url = await payment_service.create_payment(
            user_id=user_id,
            tariff_id=tariff_id,
            amount=amount,
            email=email,
            country="RU",
            protocol="outline"
        )
        
        # Assert
        assert payment_id is not None
        assert confirmation_url is not None
        
        # Проверяем запись в БД
        payment = await payment_repo.get_by_payment_id(payment_id)
        assert payment is not None
        assert payment.user_id == user_id
        assert payment.tariff_id == tariff_id
        assert payment.amount == amount
        assert payment.email == email
        assert payment.status == PaymentStatus.PENDING
    
    async def test_payment_status_update_integration(self, payment_service, payment_repo):
        """Тест обновления статуса платежа"""
        # Arrange
        payment = Payment(
            payment_id="test_payment_integration",
            user_id=123456789,
            tariff_id=1,
            amount=10000,
            email="test@example.com",
            status=PaymentStatus.PENDING
        )
        
        await payment_repo.create(payment)
        
        # Act
        success = await payment_repo.update_status(
            payment.payment_id, 
            PaymentStatus.PAID
        )
        
        # Assert
        assert success is True
        
        # Проверяем обновление в БД
        updated_payment = await payment_repo.get_by_payment_id(payment.payment_id)
        assert updated_payment.status == PaymentStatus.PAID
    
    async def test_payment_statistics_integration(self, payment_service):
        """Тест получения статистики платежей"""
        # Act
        stats = await payment_service.get_payment_statistics(days=30)
        
        # Assert
        assert isinstance(stats, dict)
        assert 'total_payments' in stats
        assert 'paid_payments' in stats
        assert 'pending_payments' in stats
        assert 'failed_payments' in stats
        assert 'total_amount' in stats
        assert 'success_rate' in stats
    
    async def test_yookassa_service_integration(self, payment_config):
        """Тест интеграции с YooKassa сервисом"""
        # Arrange
        factory = initialize_payment_module(payment_config)
        yookassa_service = factory.create_yookassa_service()
        
        # Act & Assert
        # Проверяем, что сервис создается без ошибок
        assert yookassa_service is not None
        assert yookassa_service.is_test_mode() is True
        
        # Проверяем, что можем получить информацию о платеже (должно вернуть None для несуществующего)
        payment_info = await yookassa_service.get_payment_info("non_existent_payment")
        assert payment_info is None
    
    async def test_webhook_service_integration(self, payment_config):
        """Тест интеграции с webhook сервисом"""
        # Arrange
        factory = initialize_payment_module(payment_config)
        webhook_service = factory.create_webhook_service()
        
        # Act & Assert
        # Проверяем, что сервис создается без ошибок
        assert webhook_service is not None
        
        # Проверяем получение логов
        logs = await webhook_service.get_webhook_logs(limit=10)
        assert isinstance(logs, list)


@pytest.mark.asyncio
class TestPaymentMigration:
    """Тесты миграции платежей"""
    
    async def test_migration_dry_run(self):
        """Тест сухой миграции"""
        from ..migration.migrate_payments import run_migration
        
        # Act
        stats = await run_migration(
            old_db_path="vpn.db",
            new_db_path="vpn_test.db",
            dry_run=True
        )
        
        # Assert
        assert isinstance(stats, dict)
        assert 'total' in stats
        assert 'success' in stats
        assert 'failed' in stats
        assert stats['total'] >= 0
        assert stats['success'] >= 0
        assert stats['failed'] >= 0


class TestPaymentConfiguration:
    """Тесты конфигурации"""
    
    def test_config_from_env(self):
        """Тест создания конфигурации из переменных окружения"""
        # Arrange
        os.environ['YOOKASSA_SHOP_ID'] = '123456'
        os.environ['YOOKASSA_API_KEY'] = 'test_key'
        os.environ['YOOKASSA_RETURN_URL'] = 'https://t.me/test_bot'
        
        # Act
        config = PaymentConfig.from_env()
        
        # Assert
        assert config.yookassa_shop_id == '123456'
        assert config.yookassa_api_key == 'test_key'
        assert config.yookassa_return_url == 'https://t.me/test_bot'
    
    def test_config_validation(self):
        """Тест валидации конфигурации"""
        # Arrange
        config = PaymentConfig(
            yookassa_shop_id="123456",
            yookassa_api_key="test_key",
            yookassa_return_url="https://t.me/test_bot"
        )
        
        # Act & Assert
        assert config.validate() is True
    
    def test_config_validation_failure(self):
        """Тест неудачной валидации конфигурации"""
        # Arrange
        config = PaymentConfig(
            yookassa_shop_id="",  # Пустой shop_id
            yookassa_api_key="test_key",
            yookassa_return_url="https://t.me/test_bot"
        )
        
        # Act & Assert
        with pytest.raises(ValueError):
            config.validate()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
