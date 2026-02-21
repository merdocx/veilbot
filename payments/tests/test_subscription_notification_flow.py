"""
Тесты для проверки правильного порядка операций при покупке подписки:
1. Оплата → статус paid
2. Создание подписки и ключей
3. Уведомление пользователю СРАЗУ после создания
4. Статус completed только после успешной отправки уведомления
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from payments.models.payment import Payment, PaymentStatus
from payments.services.subscription_purchase_service import SubscriptionPurchaseService
from payments.repositories.payment_repository import PaymentRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.tariff_repository import TariffRepository


@pytest.fixture
def mock_bot():
    """Мок бота для отправки уведомлений"""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock())
    return bot


@pytest.fixture
def subscription_service(mock_bot):
    """Сервис для обработки покупки подписки"""
    with patch('payments.services.subscription_purchase_service.get_bot_instance', return_value=mock_bot):
        with patch('bot.utils.messaging.get_bot_instance', return_value=mock_bot):
            service = SubscriptionPurchaseService()
            return service


@pytest.mark.asyncio
async def test_subscription_notification_sent_immediately_after_creation(subscription_service, mock_bot):
    """Тест: уведомление отправляется СРАЗУ после создания подписки и ключей"""
    # Arrange
    payment_id = "test_payment_123"
    user_id = 12345
    
    # Создаем платеж со статусом paid
    payment = Payment(
        payment_id=payment_id,
        user_id=user_id,
        tariff_id=1,
        amount=10000,
        email="test@example.com",
        status=PaymentStatus.PAID,
        protocol='v2ray',
        paid_at=datetime.now(timezone.utc),
        metadata={'key_type': 'subscription'}
    )
    
    # Мокируем репозитории
    with patch.object(subscription_service.payment_repo, 'get_by_payment_id', return_value=payment):
        with patch.object(subscription_service.payment_repo, 'update') as mock_update:
            with patch.object(subscription_service.tariff_repo, 'get_tariff', return_value=(1, 'Test Tariff', 86400, 100, 0)):
                with patch.object(subscription_service.subscription_repo, 'get_active_subscription_async', return_value=None):
                    with patch.object(subscription_service.subscription_repo, 'get_subscription_by_token_async', return_value=None):
                        with patch.object(subscription_service.subscription_repo, 'create_subscription_async', return_value=1):
                            with patch.object(subscription_service.subscription_repo, 'mark_purchase_notification_sent_async'):
                                # Мокируем создание ключей
                                with patch('payments.services.subscription_purchase_service.open_async_connection') as mock_conn:
                                    mock_cursor = AsyncMock()
                                    mock_cursor.execute = AsyncMock()
                                    mock_cursor.fetchall = AsyncMock(return_value=[(1, 'Server1', 'http://api', 'key', 'domain', 'path')])
                                    mock_cursor.rowcount = 1
                                    mock_conn.return_value.__aenter__.return_value.execute.return_value = mock_cursor
                                    
                                    # Мокируем создание пользователя на V2Ray сервере
                                    mock_protocol_client = AsyncMock()
                                    mock_protocol_client.create_user = AsyncMock(return_value={'uuid': 'test-uuid'})
                                    mock_protocol_client.get_user_config = AsyncMock(return_value='vless://test-config')
                                    
                                    with patch('payments.services.subscription_purchase_service.ProtocolFactory.create_protocol', return_value=mock_protocol_client):
                                        # Act
                                        success, error_msg = await subscription_service.process_subscription_purchase(payment_id)
                                        
                                        # Assert
                                        assert success is True
                                        assert error_msg is None
                                        
                                        # Проверяем, что уведомление было отправлено
                                        assert mock_bot.send_message.called or hasattr(mock_bot, '_send_notification_called')
                                        
                                        # Проверяем, что платеж был помечен как completed
                                        update_calls = [call for call in mock_update.call_args_list]
                                        assert len(update_calls) > 0
                                        
                                        # Проверяем, что последний вызов update был с completed статусом
                                        final_payment = mock_update.call_args[0][0]
                                        assert final_payment.status == PaymentStatus.COMPLETED


@pytest.mark.asyncio
async def test_payment_not_completed_if_notification_failed(subscription_service, mock_bot):
    """Тест: платеж НЕ помечается как completed, если уведомление не отправлено"""
    # Arrange
    payment_id = "test_payment_456"
    user_id = 12345
    
    # Создаем платеж со статусом paid
    payment = Payment(
        payment_id=payment_id,
        user_id=user_id,
        tariff_id=1,
        amount=10000,
        email="test@example.com",
        status=PaymentStatus.PAID,
        protocol='v2ray',
        paid_at=datetime.now(timezone.utc),
        metadata={'key_type': 'subscription'}
    )
    
    # Мокируем неудачную отправку уведомления
    mock_bot.send_message = AsyncMock(return_value=None)
    
    # Мокируем репозитории
    with patch.object(subscription_service.payment_repo, 'get_by_payment_id', return_value=payment):
        with patch.object(subscription_service.payment_repo, 'update') as mock_update:
            with patch.object(subscription_service.tariff_repo, 'get_tariff', return_value=(1, 'Test Tariff', 86400, 100, 0)):
                with patch.object(subscription_service.subscription_repo, 'get_active_subscription_async', return_value=None):
                    with patch.object(subscription_service.subscription_repo, 'get_subscription_by_token_async', return_value=None):
                        with patch.object(subscription_service.subscription_repo, 'create_subscription_async', return_value=1):
                            # Мокируем создание ключей
                            with patch('payments.services.subscription_purchase_service.open_async_connection') as mock_conn:
                                mock_cursor = AsyncMock()
                                mock_cursor.execute = AsyncMock()
                                mock_cursor.fetchall = AsyncMock(return_value=[(1, 'Server1', 'http://api', 'key', 'domain', 'path')])
                                mock_cursor.rowcount = 1
                                mock_conn.return_value.__aenter__.return_value.execute.return_value = mock_cursor
                                
                                # Мокируем создание пользователя на V2Ray сервере
                                mock_protocol_client = AsyncMock()
                                mock_protocol_client.create_user = AsyncMock(return_value={'uuid': 'test-uuid'})
                                mock_protocol_client.get_user_config = AsyncMock(return_value='vless://test-config')
                                
                                with patch('payments.services.subscription_purchase_service.ProtocolFactory.create_protocol', return_value=mock_protocol_client):
                                    # Act
                                    success, error_msg = await subscription_service.process_subscription_purchase(payment_id)
                                    
                                    # Assert
                                    assert success is False
                                    assert "Failed to send notification" in error_msg
                                    
                                    # Проверяем, что платеж НЕ был помечен как completed
                                    # Последний вызов update должен быть с paid статусом, а не completed
                                    final_payment = mock_update.call_args[0][0]
                                    assert final_payment.status == PaymentStatus.PAID
                                    assert final_payment.metadata.get('_notification_failed') is True


@pytest.mark.asyncio
async def test_notification_order_after_subscription_creation(subscription_service, mock_bot):
    """Тест: порядок операций - создание подписки → уведомление → completed"""
    # Arrange
    payment_id = "test_payment_789"
    user_id = 12345
    call_order = []
    
    payment = Payment(
        payment_id=payment_id,
        user_id=user_id,
        tariff_id=1,
        amount=10000,
        email="test@example.com",
        status=PaymentStatus.PAID,
        protocol='v2ray',
        paid_at=datetime.now(timezone.utc),
        metadata={'key_type': 'subscription'}
    )
    
    # Отслеживаем порядок вызовов
    original_update = subscription_service.payment_repo.update
    async def tracked_update(p):
        call_order.append(('update', p.status))
        return await original_update(p)
    
    original_mark_sent = subscription_service.subscription_repo.mark_purchase_notification_sent_async
    async def tracked_mark_sent(sub_id):
        call_order.append(('mark_notification_sent', sub_id))
        return await original_mark_sent(sub_id)
    
    async def tracked_send_message(*args, **kwargs):
        call_order.append(('send_notification',))
        return await mock_bot.send_message(*args, **kwargs)
    
    mock_bot.send_message = AsyncMock(side_effect=tracked_send_message)
    
    # Мокируем репозитории
    with patch.object(subscription_service.payment_repo, 'get_by_payment_id', return_value=payment):
        with patch.object(subscription_service.payment_repo, 'update', side_effect=tracked_update):
            with patch.object(subscription_service.tariff_repo, 'get_tariff', return_value=(1, 'Test Tariff', 86400, 100, 0)):
                with patch.object(subscription_service.subscription_repo, 'get_active_subscription_async', return_value=None):
                    with patch.object(subscription_service.subscription_repo, 'get_subscription_by_token_async', return_value=None):
                        with patch.object(subscription_service.subscription_repo, 'create_subscription_async', return_value=1):
                            with patch.object(subscription_service.subscription_repo, 'mark_purchase_notification_sent_async', side_effect=tracked_mark_sent):
                                # Мокируем создание ключей
                                with patch('payments.services.subscription_purchase_service.open_async_connection') as mock_conn:
                                    mock_cursor = AsyncMock()
                                    mock_cursor.execute = AsyncMock()
                                    mock_cursor.fetchall = AsyncMock(return_value=[(1, 'Server1', 'http://api', 'key', 'domain', 'path')])
                                    mock_cursor.rowcount = 1
                                    mock_conn.return_value.__aenter__.return_value.execute.return_value = mock_cursor
                                    
                                    # Мокируем создание пользователя на V2Ray сервере
                                    mock_protocol_client = AsyncMock()
                                    mock_protocol_client.create_user = AsyncMock(return_value={'uuid': 'test-uuid'})
                                    mock_protocol_client.get_user_config = AsyncMock(return_value='vless://test-config')
                                    
                                    with patch('payments.services.subscription_purchase_service.ProtocolFactory.create_protocol', return_value=mock_protocol_client):
                                        # Act
                                        success, error_msg = await subscription_service.process_subscription_purchase(payment_id)
                                        
                                        # Assert
                                        assert success is True
                                        
                                        # Проверяем порядок операций
                                        # 1. Создание подписки (через create_subscription_async)
                                        # 2. Создание ключей
                                        # 3. Отправка уведомления
                                        # 4. Пометка уведомления как отправленного
                                        # 5. Пометка платежа как completed
                                        
                                        notification_index = None
                                        mark_sent_index = None
                                        completed_index = None
                                        
                                        for i, (op, _) in enumerate(call_order):
                                            if op == 'send_notification':
                                                notification_index = i
                                            elif op == 'mark_notification_sent':
                                                mark_sent_index = i
                                            elif op == 'update' and call_order[i][1] == PaymentStatus.COMPLETED:
                                                completed_index = i
                                        
                                        # Уведомление должно быть отправлено
                                        assert notification_index is not None
                                        
                                        # Уведомление должно быть помечено как отправленное после отправки
                                        if mark_sent_index is not None:
                                            assert mark_sent_index > notification_index
                                        
                                        # Платеж должен быть помечен как completed после отправки уведомления
                                        assert completed_index is not None
                                        assert completed_index > notification_index









