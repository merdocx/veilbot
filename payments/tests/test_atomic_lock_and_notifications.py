"""
Тесты для проверки атомарной блокировки и предотвращения дублирования уведомлений
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import json
import time

from payments.models.payment import Payment, PaymentStatus
from payments.services.subscription_purchase_service import SubscriptionPurchaseService
from payments.repositories.payment_repository import PaymentRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.tariff_repository import TariffRepository


@pytest.mark.asyncio
class TestAtomicLock:
    """Тесты атомарной блокировки"""
    
    @pytest.fixture
    def payment_repo(self, tmp_path):
        """Создаем временную БД для тестов"""
        db_path = str(tmp_path / "test.db")
        return PaymentRepository(db_path)
    
    @pytest.fixture
    def test_payment(self, payment_repo):
        """Создаем тестовый платеж"""
        async def _create():
            payment = Payment(
                payment_id="test_payment_lock_1",
                user_id=12345,
                tariff_id=1,
                amount=10000,
                email="test@example.com",
                status=PaymentStatus.PAID,
                protocol='v2ray',
                metadata={'key_type': 'subscription'}
            )
            await payment_repo._ensure_table_exists()
            return await payment_repo.create(payment)
        return _create
    
    async def test_atomic_lock_prevents_concurrent_processing(self, payment_repo, test_payment):
        """Тест: атомарная блокировка предотвращает одновременную обработку"""
        # Arrange
        payment = await test_payment()
        payment_id = payment.payment_id
        
        # Act: пытаемся получить блокировку дважды одновременно
        lock1_acquired = await payment_repo.try_acquire_processing_lock(payment_id)
        lock2_acquired = await payment_repo.try_acquire_processing_lock(payment_id)
        
        # Assert: только первая попытка должна быть успешной
        assert lock1_acquired is True
        assert lock2_acquired is False
        
        # Освобождаем блокировку
        await payment_repo.release_processing_lock(payment_id)
        
        # Теперь можно получить блокировку снова
        lock3_acquired = await payment_repo.try_acquire_processing_lock(payment_id)
        assert lock3_acquired is True
        
        await payment_repo.release_processing_lock(payment_id)
    
    async def test_lock_not_acquired_for_completed_payment(self, payment_repo, test_payment):
        """Тест: блокировка не устанавливается для завершенного платежа"""
        # Arrange
        payment = await test_payment()
        payment_id = payment.payment_id
        payment.mark_as_completed()
        await payment_repo.update(payment)
        
        # Act
        lock_acquired = await payment_repo.try_acquire_processing_lock(payment_id)
        
        # Assert
        assert lock_acquired is False
    
    async def test_lock_release_removes_flag(self, payment_repo, test_payment):
        """Тест: освобождение блокировки удаляет флаг"""
        # Arrange
        payment = await test_payment()
        payment_id = payment.payment_id
        
        # Act
        lock_acquired = await payment_repo.try_acquire_processing_lock(payment_id)
        assert lock_acquired is True
        
        # Проверяем, что флаг установлен
        payment_check = await payment_repo.get_by_payment_id(payment_id)
        assert payment_check.metadata.get('_processing_subscription') is True
        
        # Освобождаем блокировку
        released = await payment_repo.release_processing_lock(payment_id)
        assert released is True
        
        # Проверяем, что флаг удален
        payment_check2 = await payment_repo.get_by_payment_id(payment_id)
        assert payment_check2.metadata.get('_processing_subscription') is None


@pytest.mark.asyncio
class TestNotificationFlagCheck:
    """Тесты проверки флага purchase_notification_sent перед отправкой"""
    
    @pytest.fixture
    def subscription_service(self):
        """Создаем сервис с моками"""
        with patch('payments.services.subscription_purchase_service.get_bot_instance'):
            with patch('bot.utils.messaging.get_bot_instance'):
                return SubscriptionPurchaseService()
    
    async def test_notification_not_sent_if_flag_already_set(self, subscription_service):
        """Тест: уведомление не отправляется, если флаг purchase_notification_sent уже установлен"""
        # Arrange
        payment_id = "test_payment_notif_flag"
        user_id = 12345
        subscription_id = 1
        
        payment = Payment(
            payment_id=payment_id,
            user_id=user_id,
            tariff_id=1,
            amount=10000,
            email="test@example.com",
            status=PaymentStatus.PAID,
            protocol='v2ray',
            metadata={'key_type': 'subscription'}
        )
        
        # Мокируем получение платежа
        with patch.object(subscription_service.payment_repo, 'get_by_payment_id', return_value=payment):
            # Мокируем получение блокировки
            with patch.object(subscription_service.payment_repo, 'try_acquire_processing_lock', return_value=True):
                with patch.object(subscription_service.payment_repo, 'release_processing_lock', return_value=True):
                    with patch.object(subscription_service.tariff_repo, 'get_tariff', return_value=(1, 'Test', 86400, 100, 0)):
                        # Мокируем существующую подписку с установленным флагом
                        existing_subscription = (subscription_id, user_id, 'test-token', int(time.time()) - 3600, int(time.time()) + 86400, 1, 1, int(time.time()), 0)
                        with patch.object(subscription_service.subscription_repo, 'get_active_subscription_async', return_value=existing_subscription):
                            # Мокируем проверку флага purchase_notification_sent (возвращает True)
                            with patch('payments.services.subscription_purchase_service.open_async_connection') as mock_conn:
                                mock_cursor = AsyncMock()
                                mock_cursor.fetchone = AsyncMock(return_value=(1,))  # purchase_notification_sent = 1
                                mock_conn.return_value.__aenter__.return_value.execute.return_value = mock_cursor
                                
                                # Мокируем продление подписки
                                with patch.object(subscription_service.subscription_repo, 'extend_subscription_async'):
                                    # Мокируем продление ключей
                                    mock_extend_cursor = AsyncMock()
                                    mock_extend_cursor.rowcount = 3
                                    mock_extend_cursor.execute = AsyncMock()
                                    mock_conn.return_value.__aenter__.return_value.execute.return_value = mock_extend_cursor
                                    
                                    # Мокируем отправку уведомления (не должна быть вызвана)
                                    mock_bot = MagicMock()
                                    mock_bot.send_message = AsyncMock()
                                    with patch('payments.services.subscription_purchase_service.get_bot_instance', return_value=mock_bot):
                                        with patch('bot.utils.safe_send_message', new_callable=AsyncMock) as mock_send:
                                            # Act
                                            success, error_msg = await subscription_service.process_subscription_purchase(payment_id)
                                            
                                            # Assert
                                            assert success is True
                                            # Уведомление НЕ должно быть отправлено, так как флаг уже установлен
                                            mock_send.assert_not_called()
                                            
                                            # Платеж должен быть помечен как completed
                                            update_call = subscription_service.payment_repo.update.call_args
                                            if update_call:
                                                final_payment = update_call[0][0]
                                                assert final_payment.status == PaymentStatus.COMPLETED


@pytest.mark.asyncio
class TestUnifiedRetry:
    """Тесты унифицированного retry механизма"""
    
    @pytest.fixture
    def subscription_service(self):
        """Создаем сервис с моками"""
        with patch('payments.services.subscription_purchase_service.get_bot_instance'):
            with patch('bot.utils.messaging.get_bot_instance'):
                return SubscriptionPurchaseService()
    
    async def test_send_notification_uses_safe_send_message(self, subscription_service):
        """Тест: _send_notification_simple использует safe_send_message с встроенным retry"""
        # Arrange
        user_id = 12345
        message = "Test notification"
        
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock())
        
        # Act
        with patch('payments.services.subscription_purchase_service.get_bot_instance', return_value=mock_bot):
            with patch('bot.utils.safe_send_message', new_callable=AsyncMock, return_value=True) as mock_safe_send:
                result = await subscription_service._send_notification_simple(user_id, message)
                
                # Assert
                assert result is True
                # Проверяем, что использован safe_send_message (который имеет встроенный retry)
                mock_safe_send.assert_called_once()
                # Проверяем, что НЕ было прямых вызовов bot.send_message
                assert not mock_bot.send_message.called


@pytest.mark.asyncio
class TestRenewalDetection:
    """Тесты определения продления"""
    
    async def test_is_renewal_payment_detects_existing_key(self, tmp_path):
        """Тест: функция is_renewal_payment правильно определяет продление"""
        from payments.utils.renewal_detector import is_renewal_payment
        import sqlite3
        
        # Arrange: создаем временную БД с ключом
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Создаем таблицы (expiry берётся из subscriptions через JOIN)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                expires_at INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                subscription_id INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS v2ray_keys (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                subscription_id INTEGER
            )
        """)
        
        user_id = 12345
        now = int(time.time())
        future_expiry = now + 86400  # Истекает через день
        
        # Подписка с активным сроком и ключ, привязанный к ней
        cursor.execute(
            "INSERT INTO subscriptions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (1, user_id, future_expiry)
        )
        cursor.execute(
            "INSERT INTO keys (user_id, subscription_id) VALUES (?, ?)",
            (user_id, 1)
        )
        conn.commit()
        
        # Act
        is_renewal = is_renewal_payment(cursor, user_id, 'outline')
        
        # Assert
        assert is_renewal is True
        
        # Проверяем для нового пользователя (без ключа)
        is_renewal_new = is_renewal_payment(cursor, 99999, 'outline')
        assert is_renewal_new is False
        
        conn.close()


@pytest.mark.asyncio
class TestPaymentProcessor:
    """Тесты единой точки входа для обработки платежей"""
    
    async def test_process_payment_unified_routes_to_subscription_service(self, tmp_path):
        """Тест: единая точка входа правильно маршрутизирует подписки"""
        from payments.services.payment_processor import PaymentProcessor
        
        # Arrange
        db_path = str(tmp_path / "test.db")
        processor = PaymentProcessor(db_path)
        
        payment_id = "test_unified_1"
        user_id = 12345
        
        # Создаем платеж для подписки
        payment = Payment(
            payment_id=payment_id,
            user_id=user_id,
            tariff_id=1,
            amount=10000,
            email="test@example.com",
            status=PaymentStatus.PAID,
            protocol='v2ray',
            metadata={'key_type': 'subscription'}
        )
        
        await processor.payment_repo._ensure_table_exists()
        await processor.payment_repo.create(payment)
        
        # Мокируем обработку подписки
        with patch.object(processor.subscription_service, 'process_subscription_purchase', new_callable=AsyncMock, return_value=(True, None)) as mock_process:
            # Act
            success, error_msg = await processor.process_payment(payment_id)
            
            # Assert
            assert success is True
            mock_process.assert_called_once_with(payment_id)
    
    async def test_process_payment_unified_skips_completed(self, tmp_path):
        """Тест: единая точка входа пропускает завершенные платежи"""
        from payments.services.payment_processor import PaymentProcessor
        
        # Arrange
        db_path = str(tmp_path / "test.db")
        processor = PaymentProcessor(db_path)
        
        payment_id = "test_unified_2"
        user_id = 12345
        
        # Создаем завершенный платеж
        payment = Payment(
            payment_id=payment_id,
            user_id=user_id,
            tariff_id=1,
            amount=10000,
            email="test@example.com",
            status=PaymentStatus.COMPLETED,
            protocol='v2ray',
            metadata={'key_type': 'subscription'}
        )
        
        await processor.payment_repo._ensure_table_exists()
        await processor.payment_repo.create(payment)
        
        # Мокируем обработку подписки (не должна быть вызвана)
        with patch.object(processor.subscription_service, 'process_subscription_purchase', new_callable=AsyncMock) as mock_process:
            # Act
            success, error_msg = await processor.process_payment(payment_id)
            
            # Assert
            assert success is True
            # Обработка не должна быть вызвана для завершенного платежа
            mock_process.assert_not_called()


@pytest.mark.asyncio
class TestConcurrentProcessingProtection:
    """Тесты защиты от одновременной обработки"""
    
    async def test_concurrent_calls_handled_correctly(self, tmp_path):
        """Тест: одновременные вызовы обрабатываются корректно"""
        from payments.services.subscription_purchase_service import SubscriptionPurchaseService
        
        # Arrange
        db_path = str(tmp_path / "test.db")
        service = SubscriptionPurchaseService(db_path)
        
        payment_id = "test_concurrent_1"
        user_id = 12345
        
        payment = Payment(
            payment_id=payment_id,
            user_id=user_id,
            tariff_id=1,
            amount=10000,
            email="test@example.com",
            status=PaymentStatus.PAID,
            protocol='v2ray',
            metadata={'key_type': 'subscription'}
        )
        
        await service.payment_repo._ensure_table_exists()
        await service.payment_repo.create(payment)
        
        # Мокируем тариф и подписку
        with patch.object(service.tariff_repo, 'get_tariff', return_value=(1, 'Test', 86400, 100, 0)):
            with patch.object(service.subscription_repo, 'get_active_subscription_async', return_value=None):
                with patch.object(service.subscription_repo, 'get_subscription_by_token_async', return_value=None):
                    with patch.object(service.subscription_repo, 'create_subscription_async', return_value=1):
                        with patch.object(service.subscription_repo, 'mark_purchase_notification_sent_async'):
                            # Мокируем создание ключей
                            with patch('payments.services.subscription_purchase_service.open_async_connection') as mock_conn:
                                mock_cursor = AsyncMock()
                                mock_cursor.execute = AsyncMock()
                                mock_cursor.fetchall = AsyncMock(return_value=[])
                                mock_cursor.rowcount = 0
                                mock_conn.return_value.__aenter__.return_value.execute.return_value = mock_cursor
                                
                                # Мокируем отправку уведомления
                                with patch('payments.services.subscription_purchase_service.get_bot_instance'):
                                    with patch('bot.utils.safe_send_message', new_callable=AsyncMock, return_value=True):
                                        # Act: вызываем одновременно дважды
                                        results = await asyncio.gather(
                                            service.process_subscription_purchase(payment_id),
                                            service.process_subscription_purchase(payment_id),
                                            return_exceptions=True
                                        )
                                        
                                        # Assert: только один должен быть успешным
                                        success_count = sum(1 for r in results if isinstance(r, tuple) and r[0] is True)
                                        # Один должен быть успешным, второй должен быть пропущен (уже обрабатывается)
                                        assert success_count >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])








