"""
Тесты для флоу покупки подписки
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta, timezone
import time

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
    
    async def get_paid_payments_without_keys(self):
        """Получение оплаченных платежей без ключей"""
        return [p for p in self.payments.values() if p.status == PaymentStatus.PAID]


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
        
        # Мокаем получение тарифа
        tariff = {
            'id': tariff_id,
            'name': 'Тестовая подписка',
            'duration_sec': 86400,  # 1 день
            'price_rub': 100.0,
            'traffic_limit_mb': None
        }
        
        # Мокаем получение тарифа из БД и все необходимые зависимости
        with patch('payments.services.payment_service.open_connection') as mock_conn:
            mock_cursor = MagicMock()
            mock_cursor.execute.return_value = None
            mock_cursor.fetchone.return_value = (
                tariff_id, 'Тестовая подписка', 86400, 100.0, None
            )
            mock_cursor.rowcount = 0
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor
            mock_conn.return_value.__enter__.return_value.commit.return_value = None
            
            # Мокаем проверку существующей подписки (нет активной подписки)
            mock_check_cursor = MagicMock()
            mock_check_cursor.execute.return_value = None
            mock_check_cursor.fetchone.return_value = None
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_check_cursor
            
            # Мокаем SubscriptionService (импортируется внутри функции)
            with patch('bot.services.subscription_service.SubscriptionService') as mock_sub_service_class:
                mock_sub_service_instance = AsyncMock()
                mock_sub_service_instance.create_subscription = AsyncMock(return_value={
                    'id': 1,
                    'token': 'test-token',
                    'expires_at': int(time.time()) + 86400,
                    'created_keys': 3
                })
                mock_sub_service_class.return_value = mock_sub_service_instance
                
                # Мокаем get_bot_instance и safe_send_message (импортируются внутри функции)
                with patch('bot.core.get_bot_instance', return_value=None):
                    with patch('bot.utils.safe_send_message', new_callable=AsyncMock):
                        # Act
                        processed_count = await payment_service.process_paid_payments_without_keys()
                        
                        # Assert
                        # Проверяем, что платеж был обработан
                        updated_payment = await mock_repo.get_by_payment_id(payment_id)
                        # Платеж должен быть помечен как completed после создания подписки
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
        now_ts = int(time.time())
        
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
        
        # Мокаем получение тарифа
        tariff = {
            'id': tariff_id,
            'name': 'Тестовая подписка',
            'duration_sec': 86400,  # 1 день
            'price_rub': 100.0,
            'traffic_limit_mb': None
        }
        
        # Мокаем существующую подписку
        existing_subscription_id = 1
        existing_expires_at = now_ts + 3600  # Истекает через час
        subscription_token = "test-token-123"
        
        with patch('payments.services.payment_service.open_connection') as mock_conn:
            mock_cursor = MagicMock()
            mock_cursor.execute.return_value = None
            
            # Мокаем получение тарифа
            mock_cursor.fetchone.side_effect = [
                (tariff_id, 'Тестовая подписка', 86400, 100.0, None),  # Тариф
                (existing_subscription_id, existing_expires_at, now_ts - 3600),  # Существующая подписка (created_at)
                (existing_subscription_id, existing_expires_at),  # Подписка для продления
                (subscription_token,),  # Токен подписки
            ]
            
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor
            mock_conn.return_value.__enter__.return_value.commit.return_value = None
            
            # Мокаем get_bot_instance и safe_send_message (импортируются внутри функции)
            with patch('bot.core.get_bot_instance', return_value=None):
                with patch('bot.utils.safe_send_message', new_callable=AsyncMock):
                    # Act
                    processed_count = await payment_service.process_paid_payments_without_keys()
                    
                    # Assert
                    # Проверяем, что платеж был обработан
                    updated_payment = await mock_repo.get_by_payment_id(payment_id)
                    # Платеж должен быть помечен как completed после продления подписки
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
        
        # Мокаем получение тарифа
        tariff = {
            'id': tariff_id,
            'name': 'Тестовая подписка',
            'duration_sec': 86400,
            'price_rub': 100.0,
            'traffic_limit_mb': None
        }
        
        # Мокаем существующую подписку, созданную после оплаты
        existing_subscription_id = 1
        existing_expires_at = now_ts + 86400
        subscription_created_at = now_ts - 900  # Создана 15 минут назад (после оплаты)
        
        # Мокаем get_db_cursor (используется в коде)
        mock_db_cursor = MagicMock()
        mock_db_cursor.execute.return_value = None
        mock_db_cursor.fetchone.side_effect = [
            ('paid',),  # Статус платежа
            (tariff_id, 'Тестовая подписка', 86400, 100.0, None),  # Тариф
        ]
        mock_db_cursor.connection.commit.return_value = None
        
        # Мокаем open_connection для проверки подписки
        with patch('payments.services.payment_service.open_connection') as mock_conn:
            mock_check_cursor = MagicMock()
            mock_check_cursor.execute.return_value = None
            mock_check_cursor.fetchone.return_value = (
                existing_subscription_id, existing_expires_at, subscription_created_at
            )
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_check_cursor
            
            # Мокаем get_db_cursor
            with patch('payments.services.payment_service.get_db_cursor') as mock_get_db_cursor:
                mock_get_db_cursor.return_value.__enter__.return_value = mock_db_cursor
                mock_get_db_cursor.return_value.__exit__.return_value = None
                
                # Act
                processed_count = await payment_service.process_paid_payments_without_keys()
                
                # Assert
                assert processed_count >= 1
                updated_payment = await mock_repo.get_by_payment_id(payment_id)
                # Платеж должен быть помечен как completed, так как подписка уже существует
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

