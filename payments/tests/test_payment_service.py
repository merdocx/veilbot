import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from datetime import datetime

from ..services.payment_service import PaymentService
from ..models.payment import Payment, PaymentStatus
from ..models.enums import PaymentCurrency, PaymentProvider


class MockPaymentRepository:
    """Мок репозитория платежей"""
    
    def __init__(self):
        self.payments = {}
        self.create_called = False
        self.update_called = False
    
    async def create(self, payment: Payment) -> Payment:
        self.create_called = True
        payment.id = len(self.payments) + 1
        self.payments[payment.payment_id] = payment
        return payment
    
    async def get_by_payment_id(self, payment_id: str) -> Payment:
        return self.payments.get(payment_id)
    
    async def update(self, payment: Payment) -> Payment:
        self.update_called = True
        self.payments[payment.payment_id] = payment
        return payment
    
    async def get_user_payments(self, user_id: int, limit: int = 100):
        """Получение платежей пользователя"""
        user_payments = [p for p in self.payments.values() if p.user_id == user_id]
        return user_payments[:limit]
    
    async def get_pending_payments(self):
        """Получение ожидающих платежей"""
        pending_payments = [p for p in self.payments.values() if p.status == PaymentStatus.PENDING]
        return pending_payments


class MockYooKassaService:
    """Мок YooKassa сервиса"""
    
    def __init__(self):
        self.payments = {}
        self.create_payment_called = False
        self.check_payment_called = False
    
    async def create_payment(self, amount: int, description: str, email: str, **kwargs):
        self.create_payment_called = True
        payment_id = f"test_payment_{len(self.payments) + 1}"
        self.payments[payment_id] = {
            "amount": amount,
            "description": description,
            "email": email,
            "status": "pending"
        }
        return payment_id, "https://test.payment.url"
    
    async def check_payment(self, payment_id: str) -> bool:
        self.check_payment_called = True
        payment = self.payments.get(payment_id)
        return payment and payment.get("status") == "succeeded"


@pytest.mark.asyncio
class TestPaymentService:
    """Тесты для PaymentService"""
    
    @pytest.fixture
    def mock_repo(self):
        return MockPaymentRepository()
    
    @pytest.fixture
    def mock_yookassa(self):
        return MockYooKassaService()
    
    @pytest.fixture
    def payment_service(self, mock_repo, mock_yookassa):
        return PaymentService(mock_repo, mock_yookassa)
    
    async def test_create_payment_success(self, payment_service, mock_repo, mock_yookassa):
        """Тест успешного создания платежа"""
        # Arrange
        user_id = 123
        tariff_id = 1
        amount = 10000
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
        assert mock_repo.create_called
        assert mock_yookassa.create_payment_called
        
        # Проверяем созданный платеж
        payment = await mock_repo.get_by_payment_id(payment_id)
        assert payment is not None
        assert payment.user_id == user_id
        assert payment.tariff_id == tariff_id
        assert payment.amount == amount
        assert payment.email == email
    
    async def test_create_payment_invalid_email(self, payment_service):
        """Тест создания платежа с невалидным email"""
        # Act
        payment_id, confirmation_url = await payment_service.create_payment(
            user_id=123,
            tariff_id=1,
            amount=10000,
            email="invalid-email",
            protocol="outline"
        )
        
        # Assert
        assert payment_id is None
        assert confirmation_url is None
    
    async def test_create_payment_invalid_amount(self, payment_service):
        """Тест создания платежа с невалидной суммой"""
        # Act
        payment_id, confirmation_url = await payment_service.create_payment(
            user_id=123,
            tariff_id=1,
            amount=-100,  # Отрицательная сумма
            email="test@example.com",
            protocol="outline"
        )
        
        # Assert
        assert payment_id is None
        assert confirmation_url is None
    
    async def test_process_payment_success(self, payment_service, mock_repo, mock_yookassa):
        """Тест обработки успешного платежа"""
        # Arrange
        payment = Payment(
            payment_id="test_payment_1",
            user_id=123,
            tariff_id=1,
            amount=10000,
            email="test@example.com",
            status=PaymentStatus.PENDING
        )
        await mock_repo.create(payment)
        
        # Создаем платеж в моке YooKassa
        await mock_yookassa.create_payment(10000, "test", "test@example.com")
        # Устанавливаем статус как оплаченный в моке
        mock_yookassa.payments["test_payment_1"]["status"] = "succeeded"
        
        # Act
        success = await payment_service.process_payment_success("test_payment_1")
        
        # Assert
        assert success is True
        assert mock_repo.update_called
        
        # Проверяем обновленный платеж
        updated_payment = await mock_repo.get_by_payment_id("test_payment_1")
        assert updated_payment.status == PaymentStatus.PAID
        assert updated_payment.paid_at is not None
    
    async def test_wait_for_payment_success(self, payment_service, mock_repo, mock_yookassa):
        """Тест ожидания успешного платежа"""
        # Arrange
        payment = Payment(
            payment_id="test_payment_1",
            user_id=123,
            tariff_id=1,
            amount=10000,
            email="test@example.com",
            status=PaymentStatus.PENDING
        )
        await mock_repo.create(payment)
        
        # Создаем платеж в моке YooKassa
        await mock_yookassa.create_payment(10000, "test", "test@example.com")
        # Устанавливаем статус как оплаченный в моке
        mock_yookassa.payments["test_payment_1"]["status"] = "succeeded"
        
        # Act
        success = await payment_service.wait_for_payment(
            payment_id="test_payment_1",
            timeout_minutes=1,
            check_interval_seconds=1
        )
        
        # Assert
        assert success is True
        assert mock_yookassa.check_payment_called
    
    async def test_wait_for_payment_timeout(self, payment_service, mock_repo, mock_yookassa):
        """Тест таймаута ожидания платежа"""
        # Arrange
        payment = Payment(
            payment_id="test_payment_1",
            user_id=123,
            tariff_id=1,
            amount=10000,
            email="test@example.com",
            status=PaymentStatus.PENDING
        )
        await mock_repo.create(payment)
        
        # Платеж остается в статусе pending
        
        # Act
        success = await payment_service.wait_for_payment(
            payment_id="test_payment_1",
            timeout_minutes=0,  # Немедленный таймаут
            check_interval_seconds=1
        )
        
        # Assert
        assert success is False
    
    async def test_get_user_payments(self, payment_service, mock_repo):
        """Тест получения платежей пользователя"""
        # Arrange
        payment1 = Payment(
            payment_id="test_payment_1",
            user_id=123,
            tariff_id=1,
            amount=10000,
            email="test@example.com"
        )
        payment2 = Payment(
            payment_id="test_payment_2",
            user_id=123,
            tariff_id=2,
            amount=20000,
            email="test@example.com"
        )
        await mock_repo.create(payment1)
        await mock_repo.create(payment2)
        
        # Act
        payments = await payment_service.get_user_payments(user_id=123)
        
        # Assert
        assert len(payments) == 2
        assert all(p.user_id == 123 for p in payments)
    
    async def test_get_pending_payments(self, payment_service, mock_repo):
        """Тест получения ожидающих платежей"""
        # Arrange
        pending_payment = Payment(
            payment_id="test_payment_1",
            user_id=123,
            tariff_id=1,
            amount=10000,
            email="test@example.com",
            status=PaymentStatus.PENDING
        )
        paid_payment = Payment(
            payment_id="test_payment_2",
            user_id=123,
            tariff_id=2,
            amount=20000,
            email="test@example.com",
            status=PaymentStatus.PAID
        )
        await mock_repo.create(pending_payment)
        await mock_repo.create(paid_payment)
        
        # Act
        pending_payments = await payment_service.get_pending_payments()
        
        # Assert
        assert len(pending_payments) == 1
        assert pending_payments[0].status == PaymentStatus.PENDING


if __name__ == "__main__":
    pytest.main([__file__])
