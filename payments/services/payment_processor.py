"""
Единая точка входа для обработки платежей
Определяет тип платежа и вызывает соответствующий обработчик
"""
import logging
from typing import Tuple, Optional
from ..models.payment import Payment, PaymentStatus
from ..repositories.payment_repository import PaymentRepository
from .subscription_purchase_service import SubscriptionPurchaseService

logger = logging.getLogger(__name__)


class PaymentProcessor:
    """Единый процессор для обработки всех типов платежей"""
    
    def __init__(self, db_path: Optional[str] = None):
        self.payment_repo = PaymentRepository(db_path)
        self.subscription_service = SubscriptionPurchaseService(db_path)
    
    async def process_payment(self, payment_id: str) -> Tuple[bool, Optional[str]]:
        """
        Единая точка входа для обработки платежа любого типа
        
        Args:
            payment_id: ID платежа
            
        Returns:
            Tuple[success, error_message]
        """
        try:
            # Получаем платеж
            payment = await self.payment_repo.get_by_payment_id(payment_id)
            if not payment:
                error_msg = f"Payment {payment_id} not found"
                logger.error(f"[PAYMENT_PROCESSOR] {error_msg}")
                return False, error_msg
            
            # Проверяем статус платежа
            if payment.status == PaymentStatus.COMPLETED:
                logger.info(f"[PAYMENT_PROCESSOR] Payment {payment_id} already completed, skipping")
                return True, None
            
            # Определяем тип платежа
            key_type = payment.metadata.get('key_type') if payment.metadata else None
            is_subscription = key_type == 'subscription' and payment.protocol == 'v2ray'
            
            if is_subscription:
                # Обработка подписки
                logger.info(f"[PAYMENT_PROCESSOR] Processing subscription payment {payment_id}")
                return await self.subscription_service.process_subscription_purchase(payment_id)
            else:
                # Обработка обычного ключа
                # Для обычных ключей обработка происходит через process_paid_payments_without_keys
                # или wait_for_payment_with_protocol, поэтому здесь просто возвращаем успех
                # если платеж уже оплачен
                if payment.status == PaymentStatus.PAID:
                    logger.info(f"[PAYMENT_PROCESSOR] Payment {payment_id} is paid, will be processed by key creation flow")
                    return True, None
                else:
                    error_msg = f"Payment {payment_id} is not paid (status: {payment.status.value})"
                    logger.warning(f"[PAYMENT_PROCESSOR] {error_msg}")
                    return False, error_msg
                    
        except Exception as e:
            error_msg = f"Error processing payment {payment_id}: {e}"
            logger.error(f"[PAYMENT_PROCESSOR] {error_msg}", exc_info=True)
            return False, error_msg


# Функция-обертка для удобства использования
async def process_payment_unified(payment_id: str, db_path: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Единая точка входа для обработки платежа (функция-обертка)
    
    Args:
        payment_id: ID платежа
        db_path: Путь к БД (опционально)
        
    Returns:
        Tuple[success, error_message]
    """
    processor = PaymentProcessor(db_path)
    return await processor.process_payment(payment_id)


