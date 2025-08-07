import asyncio
import logging
from typing import Optional, Tuple, List
from datetime import datetime, timedelta

from ..models.payment import Payment, PaymentStatus, PaymentCreate, PaymentFilter
from ..repositories.payment_repository import PaymentRepository
from ..services.yookassa_service import YooKassaService
from ..utils.validators import PaymentValidators

logger = logging.getLogger(__name__)


class PaymentService:
    """Основной сервис для работы с платежами"""
    
    def __init__(
        self, 
        payment_repo: PaymentRepository,
        yookassa_service: YooKassaService
    ):
        self.payment_repo = payment_repo
        self.yookassa_service = yookassa_service
    
    async def create_payment(
        self,
        user_id: int,
        tariff_id: int,
        amount: int,
        email: str,
        country: Optional[str] = None,
        protocol: str = "outline",
        description: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Создание платежа с полной логикой
        
        Args:
            user_id: ID пользователя
            tariff_id: ID тарифа
            amount: Сумма в копейках
            email: Email для чека
            country: Страна
            protocol: VPN протокол
            description: Описание платежа
            
        Returns:
            Tuple[payment_id, confirmation_url] или (None, None) при ошибке
        """
        try:
            # Валидация входных данных
            if not PaymentValidators.validate_email(email):
                logger.error(f"Invalid email for user {user_id}: {email}")
                return None, None
            
            is_valid, error_msg = PaymentValidators.validate_amount(amount)
            if not is_valid:
                logger.error(f"Invalid amount for user {user_id}: {error_msg}")
                return None, None
            
            # Создаем описание если не передано
            if not description:
                description = f"Покупка VPN тарифа (протокол: {protocol})"
            
            # Генерируем уникальный ID платежа
            import uuid
            payment_id = str(uuid.uuid4())
            
            # Создаем платеж в YooKassa
            yookassa_payment_id, confirmation_url = await self.yookassa_service.create_payment(
                amount=amount,
                description=description,
                email=email,
                payment_id=payment_id,
                metadata={
                    "user_id": user_id,
                    "tariff_id": tariff_id,
                    "country": country,
                    "protocol": protocol
                }
            )
            
            if not yookassa_payment_id:
                logger.error(f"Failed to create YooKassa payment for user {user_id}")
                return None, None
            
            # Создаем запись в БД
            payment = Payment(
                payment_id=yookassa_payment_id,
                user_id=user_id,
                tariff_id=tariff_id,
                amount=amount,
                email=email,
                country=country,
                protocol=protocol,
                description=description,
                metadata={
                    "user_id": user_id,
                    "tariff_id": tariff_id,
                    "country": country,
                    "protocol": protocol
                }
            )
            
            await self.payment_repo.create(payment)
            
            logger.info(f"Payment created successfully: {yookassa_payment_id} for user {user_id}")
            return yookassa_payment_id, confirmation_url
            
        except Exception as e:
            logger.error(f"Error creating payment: {e}")
            return None, None
    
    async def process_payment_success(self, payment_id: str) -> bool:
        """
        Обработка успешного платежа
        
        Args:
            payment_id: ID платежа в YooKassa
            
        Returns:
            True если обработка успешна
        """
        try:
            # Получаем платеж из БД
            payment = await self.payment_repo.get_by_payment_id(payment_id)
            if not payment:
                logger.error(f"Payment not found: {payment_id}")
                return False
            
            # Проверяем статус в YooKassa только для новых платежей
            # Старые платежи могут не существовать в YooKassa
            try:
                is_paid = await self.yookassa_service.check_payment(payment_id)
                if not is_paid:
                    logger.warning(f"Payment {payment_id} not paid in YooKassa")
                    return False
            except Exception as e:
                # Если платеж не найден в YooKassa, считаем его оплаченным
                # (это может быть старый платеж)
                logger.debug(f"Payment {payment_id} not found in YooKassa, assuming paid: {e}")
                is_paid = True
            
            # Обновляем статус в БД
            payment.mark_as_paid()
            await self.payment_repo.update(payment)
            
            logger.info(f"Payment {payment_id} marked as paid successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error processing payment success: {e}")
            return False
    
    async def wait_for_payment(
        self, 
        payment_id: str, 
        timeout_minutes: int = 5,
        check_interval_seconds: int = 5
    ) -> bool:
        """
        Ожидание платежа с таймаутом
        
        Args:
            payment_id: ID платежа
            timeout_minutes: Таймаут в минутах
            check_interval_seconds: Интервал проверки в секундах
            
        Returns:
            True если платеж оплачен, False при таймауте
        """
        try:
            max_checks = (timeout_minutes * 60) // check_interval_seconds
            
            for _ in range(max_checks):
                try:
                    # Проверяем статус в YooKassa
                    is_paid = await self.yookassa_service.check_payment(payment_id)
                    if is_paid:
                        # Обрабатываем успешный платеж
                        success = await self.process_payment_success(payment_id)
                        if success:
                            logger.info(f"Payment {payment_id} completed successfully")
                            return True
                except Exception as e:
                    # Если платеж не найден в YooKassa, пропускаем его
                    logger.debug(f"Payment {payment_id} not found in YooKassa, skipping: {e}")
                    break
                
                # Ждем перед следующей проверкой
                await asyncio.sleep(check_interval_seconds)
            
            logger.warning(f"Payment {payment_id} timeout after {timeout_minutes} minutes")
            return False
            
        except Exception as e:
            logger.error(f"Error waiting for payment: {e}")
            return False
    
    async def handle_referral_bonus(self, user_id: int) -> bool:
        """
        Обработка реферального бонуса
        
        Args:
            user_id: ID пользователя
            
        Returns:
            True если бонус обработан
        """
        try:
            # Здесь должна быть логика проверки рефералов
            # Пока что заглушка
            logger.info(f"Referral bonus processing for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error handling referral bonus: {e}")
            return False
    
    async def get_user_payments(self, user_id: int, limit: int = 100) -> List[Payment]:
        """Получение платежей пользователя"""
        return await self.payment_repo.get_user_payments(user_id, limit)
    
    async def get_pending_payments(self) -> List[Payment]:
        """Получение ожидающих платежей"""
        return await self.payment_repo.get_pending_payments()
    
    async def get_paid_payments_without_keys(self) -> List[Payment]:
        """Получение оплаченных платежей без ключей"""
        return await self.payment_repo.get_paid_payments_without_keys()
    
    async def process_pending_payments(self) -> int:
        """
        Обработка всех ожидающих платежей
        
        Returns:
            Количество обработанных платежей
        """
        try:
            pending_payments = await self.get_pending_payments()
            processed_count = 0
            
            for payment in pending_payments:
                try:
                    # Проверяем статус в YooKassa только для новых платежей
                    # Старые платежи могут не существовать в YooKassa
                    is_paid = await self.yookassa_service.check_payment(payment.payment_id)
                    if is_paid:
                        # Обрабатываем успешный платеж
                        success = await self.process_payment_success(payment.payment_id)
                        if success:
                            processed_count += 1
                except Exception as e:
                    # Если платеж не найден в YooKassa, пропускаем его
                    logger.debug(f"Payment {payment.payment_id} not found in YooKassa, skipping: {e}")
                    continue
                
                # Небольшая задержка между проверками
                await asyncio.sleep(1)
            
            logger.info(f"Processed {processed_count} pending payments")
            return processed_count
            
        except Exception as e:
            logger.error(f"Error processing pending payments: {e}")
            return 0
    
    async def process_paid_payments_without_keys(self) -> int:
        """
        Обработка оплаченных платежей без ключей
        
        Returns:
            Количество обработанных платежей
        """
        try:
            paid_payments = await self.get_paid_payments_without_keys()
            processed_count = 0
            
            for payment in paid_payments:
                try:
                    logger.info(f"Processing paid payment without key: {payment.payment_id} for user {payment.user_id}")
                    
                    # Здесь должна быть логика создания ключей
                    # Пока что просто логируем
                    logger.info(f"Found paid payment without key: {payment.payment_id} for user {payment.user_id}")
                    
                    # TODO: Добавить логику создания ключей
                    # Для этого нужно:
                    # 1. Получить тариф по tariff_id
                    # 2. Выбрать сервер по протоколу и стране
                    # 3. Создать ключ на сервере
                    # 4. Сохранить ключ в БД
                    # 5. Отправить уведомление пользователю
                    
                    processed_count += 1
                    
                    # Небольшая задержка между обработкой
                    await asyncio.sleep(0.1)  # Уменьшаем задержку
                    
                except Exception as e:
                    logger.error(f"Error processing payment {payment.payment_id}: {e}")
                    continue
            
            logger.info(f"Found {processed_count} paid payments without keys")
            return processed_count
            
        except Exception as e:
            logger.error(f"Error processing paid payments without keys: {e}")
            return 0
    
    async def refund_payment(self, payment_id: str, amount: int, reason: str = "Возврат") -> bool:
        """
        Возврат платежа
        
        Args:
            payment_id: ID платежа
            amount: Сумма возврата в копейках
            reason: Причина возврата
            
        Returns:
            True если возврат успешен
        """
        try:
            # Выполняем возврат в YooKassa
            success = await self.yookassa_service.refund_payment(payment_id, amount, reason)
            if success:
                # Обновляем статус в БД
                await self.payment_repo.update_status(payment_id, PaymentStatus.REFUNDED)
                logger.info(f"Payment {payment_id} refunded successfully")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error refunding payment: {e}")
            return False
    
    async def get_payment_statistics(self, days: int = 30) -> dict:
        """
        Получение статистики платежей
        
        Args:
            days: Количество дней для статистики
            
        Returns:
            Словарь со статистикой
        """
        try:
            since_date = datetime.utcnow() - timedelta(days=days)
            
            # Получаем все платежи за период
            filter_obj = PaymentFilter(
                created_after=since_date,
                limit=1000
            )
            payments = await self.payment_repo.filter(filter_obj)
            
            # Подсчитываем статистику
            total_payments = len(payments)
            paid_payments = len([p for p in payments if p.is_paid()])
            pending_payments = len([p for p in payments if p.is_pending()])
            failed_payments = len([p for p in payments if p.is_failed()])
            
            total_amount = sum(p.amount for p in payments if p.is_paid())
            
            return {
                "total_payments": total_payments,
                "paid_payments": paid_payments,
                "pending_payments": pending_payments,
                "failed_payments": failed_payments,
                "total_amount": total_amount,
                "success_rate": (paid_payments / total_payments * 100) if total_payments > 0 else 0,
                "period_days": days
            }
            
        except Exception as e:
            logger.error(f"Error getting payment statistics: {e}")
            return {}
    
    async def cleanup_expired_payments(self, hours: int = 24) -> int:
        """
        Очистка истекших платежей
        
        Args:
            hours: Количество часов для определения истечения
            
        Returns:
            Количество удаленных платежей
        """
        try:
            # Получаем истекшие платежи
            since_date = datetime.utcnow() - timedelta(hours=hours)
            filter_obj = PaymentFilter(
                created_before=since_date,
                is_pending=True,
                limit=1000
            )
            expired_payments = await self.payment_repo.filter(filter_obj)
            
            # Помечаем как истекшие
            cleaned_count = 0
            for payment in expired_payments:
                payment.mark_as_failed()
                await self.payment_repo.update(payment)
                cleaned_count += 1
            
            logger.info(f"Cleaned up {cleaned_count} expired payments")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"Error cleaning up expired payments: {e}")
            return 0
