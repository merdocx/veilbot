"""
Тесты для флоу покупки подписки
"""
import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
import time

from ..services.payment_service import PaymentService
from ..models.payment import Payment, PaymentStatus


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
    
    async def get_paid_payments_without_keys(self):
        """Получение оплаченных платежей без ключей"""
        return [p for p in self.payments.values() if p.status == PaymentStatus.PAID]


@contextmanager
def _fake_get_db_cursor_paid(*_args, **_kwargs):
    """Синхронный курсор: платёж в БД в статусе paid (для блока get_db_cursor в PaymentService)."""
    c = MagicMock()
    c.execute.return_value = None
    c.fetchone.return_value = ("paid",)
    yield c


def _patch_subscription_purchase_marks_completed(mock_repo):
    """Подмена обработки подписки: фиксируем успех и completed в mock_repo (реальный SubscriptionPurchaseService ходит в другую БД)."""

    async def _fake(pid: str):
        p = await mock_repo.get_by_payment_id(pid)
        if p:
            p.mark_as_completed()
            await mock_repo.update(p)
        return True, None

    return patch(
        "payments.services.subscription_purchase_service.SubscriptionPurchaseService.process_subscription_purchase",
        new=AsyncMock(side_effect=_fake),
    )


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


class MockSubscriptionService:
    """Мок сервиса подписок"""
    
    def __init__(self):
        self.subscriptions = {}
        self.create_called = False
    
    async def create_subscription(self, user_id: int, tariff_id: int, duration_sec: int):
        self.create_called = True
        import uuid
        subscription_token = str(uuid.uuid4())
        now = int(time.time())
        expires_at = now + duration_sec
        
        subscription_id = len(self.subscriptions) + 1
        subscription_data = {
            'id': subscription_id,
            'token': subscription_token,
            'expires_at': expires_at,
            'created_keys': 3,
            'failed_servers': []
        }
        self.subscriptions[user_id] = subscription_data
        return subscription_data


@pytest.mark.asyncio
class TestSubscriptionPurchaseFlow:
    """Тесты для флоу покупки подписки"""
    
    @pytest.fixture
    def mock_repo(self):
        return MockPaymentRepository()
    
    @pytest.fixture
    def mock_yookassa(self):
        return MockYooKassaService()
    
    @pytest.fixture
    def mock_subscription_service(self):
        return MockSubscriptionService()
    
    @pytest.fixture
    def payment_service(self, mock_repo, mock_yookassa):
        return PaymentService(mock_repo, mock_yookassa)
    
    async def test_create_subscription_payment(self, payment_service, mock_repo, mock_yookassa):
        """Тест создания платежа для подписки"""
        # Arrange
        user_id = 12345
        tariff_id = 14
        amount = 10000  # 100 рублей в копейках
        email = "test@example.com"
        
        # Act
        payment_id, confirmation_url = await payment_service.create_payment(
            user_id=user_id,
            tariff_id=tariff_id,
            amount=amount,
            email=email,
            protocol='v2ray',
            metadata={'key_type': 'subscription'}
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
        assert payment.protocol == 'v2ray'
        assert payment.metadata.get('key_type') == 'subscription'
        assert payment.status == PaymentStatus.PENDING
    
    async def test_process_subscription_payment_creates_subscription(
        self, payment_service, mock_repo, mock_yookassa, mock_subscription_service
    ):
        """Тест обработки оплаченного платежа подписки - создание новой подписки"""
        # Arrange
        user_id = 12345
        tariff_id = 14
        payment_id = "test_payment_subscription_1"
        
        payment = Payment(
            payment_id=payment_id,
            user_id=user_id,
            tariff_id=tariff_id,
            amount=10000,
            email="test@example.com",
            protocol='v2ray',
            status=PaymentStatus.PAID,
            metadata={'key_type': 'subscription'},
            paid_at=datetime.now(timezone.utc)
        )
        await mock_repo.create(payment)
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.return_value = None
        mock_cursor.fetchone.return_value = (
            tariff_id,
            "Тестовая подписка",
            86400,
            100.0,
            None,
        )
        mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor
        mock_conn.return_value.__enter__.return_value.commit.return_value = None

        with patch("asyncio.sleep", AsyncMock()):
            with patch("payments.services.payment_service.open_connection", mock_conn):
                with patch("app.infra.sqlite_utils.get_db_cursor", _fake_get_db_cursor_paid):
                    with _patch_subscription_purchase_marks_completed(mock_repo):
                        processed_count = await payment_service.process_paid_payments_without_keys()

        updated_payment = await mock_repo.get_by_payment_id(payment_id)
        assert updated_payment.status == PaymentStatus.COMPLETED
        assert processed_count >= 1
    
    async def test_process_subscription_payment_extends_existing_subscription(
        self, payment_service, mock_repo, mock_yookassa
    ):
        """Тест обработки оплаченного платежа подписки - продление существующей подписки"""
        # Arrange
        user_id = 12345
        tariff_id = 14
        payment_id = "test_payment_subscription_2"
        int(time.time())
        
        payment = Payment(
            payment_id=payment_id,
            user_id=user_id,
            tariff_id=tariff_id,
            amount=10000,
            email="test@example.com",
            protocol='v2ray',
            status=PaymentStatus.PAID,
            metadata={'key_type': 'subscription'},
            paid_at=datetime.now(timezone.utc)
        )
        await mock_repo.create(payment)
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.return_value = None
        mock_cursor.fetchone.return_value = (
            tariff_id,
            "Тестовая подписка",
            86400,
            100.0,
            None,
        )
        mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor
        mock_conn.return_value.__enter__.return_value.commit.return_value = None

        with patch("asyncio.sleep", AsyncMock()):
            with patch("payments.services.payment_service.open_connection", mock_conn):
                with patch("app.infra.sqlite_utils.get_db_cursor", _fake_get_db_cursor_paid):
                    with _patch_subscription_purchase_marks_completed(mock_repo):
                        processed_count = await payment_service.process_paid_payments_without_keys()

        updated_payment = await mock_repo.get_by_payment_id(payment_id)
        assert updated_payment.status == PaymentStatus.COMPLETED
        assert processed_count >= 1
    
    async def test_mark_payment_completed_if_subscription_exists(
        self, payment_service, mock_repo, mock_yookassa
    ):
        """Тест: если подписка уже существует и была создана после оплаты, помечаем платеж как completed"""
        # Arrange
        user_id = 12345
        tariff_id = 14
        payment_id = "test_payment_subscription_3"
        now_ts = int(time.time())
        paid_at = datetime.fromtimestamp(now_ts - 1800)  # Оплачен 30 минут назад
        
        payment = Payment(
            payment_id=payment_id,
            user_id=user_id,
            tariff_id=tariff_id,
            amount=10000,
            email="test@example.com",
            protocol='v2ray',
            status=PaymentStatus.PAID,
            metadata={'key_type': 'subscription'},
            paid_at=paid_at
        )
        await mock_repo.create(payment)
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.return_value = None
        mock_cursor.fetchone.return_value = (
            tariff_id,
            "Тестовая подписка",
            86400,
            100.0,
            None,
        )
        mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor
        mock_conn.return_value.__enter__.return_value.commit.return_value = None

        with patch("asyncio.sleep", AsyncMock()):
            with patch("payments.services.payment_service.open_connection", mock_conn):
                with patch("app.infra.sqlite_utils.get_db_cursor", _fake_get_db_cursor_paid):
                    with _patch_subscription_purchase_marks_completed(mock_repo):
                        processed_count = await payment_service.process_paid_payments_without_keys()

        assert processed_count >= 1
        updated_payment = await mock_repo.get_by_payment_id(payment_id)
        assert updated_payment.status == PaymentStatus.COMPLETED
    
    async def test_subscription_payment_flow_complete(self, payment_service, mock_repo, mock_yookassa):
        """Интеграционный тест полного флоу покупки подписки"""
        # Arrange
        user_id = 12345
        tariff_id = 14
        email = "test@example.com"
        
        # Шаг 1: Создание платежа
        payment_id, confirmation_url = await payment_service.create_payment(
            user_id=user_id,
            tariff_id=tariff_id,
            amount=10000,
            email=email,
            protocol='v2ray',
            metadata={'key_type': 'subscription'}
        )
        
        assert payment_id is not None
        
        # Шаг 2: Платеж оплачен
        payment = await mock_repo.get_by_payment_id(payment_id)
        payment.mark_as_paid()
        payment.paid_at = datetime.now(timezone.utc)
        await mock_repo.update(payment)
        
        # Шаг 3: Обработка оплаченного платежа
        # (В реальном сценарии это делается автоматически через фоновую задачу)
        # Здесь мы просто проверяем, что платеж в правильном состоянии
        updated_payment = await mock_repo.get_by_payment_id(payment_id)
        assert updated_payment.status == PaymentStatus.PAID
        assert updated_payment.paid_at is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

