"""
Адаптер для совместимости со старым платежным кодом

Этот модуль обеспечивает плавный переход от старого кода к новому модулю.
"""

import asyncio
import logging
from typing import Optional, Tuple, Dict, Any

from ..services.payment_service import PaymentService
from ..services.yookassa_service import YooKassaService
from ..repositories.payment_repository import PaymentRepository
from ..models.enums import PaymentProvider, PaymentMethod

logger = logging.getLogger(__name__)


class LegacyPaymentAdapter:
    """Адаптер для совместимости со старым платежным кодом"""
    
    def __init__(self, payment_service: PaymentService):
        if payment_service is None:
            raise ValueError("PaymentService cannot be None")
        self.payment_service = payment_service
    
    async def create_payment_with_email_and_protocol(
        self,
        message,
        user_id: int,
        tariff: Dict[str, Any],
        email: str,
        country: str,
        protocol: str,
        provider: PaymentProvider = PaymentProvider.YOOKASSA,
        method: Optional[PaymentMethod] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Адаптер для старой функции create_payment_with_email_and_protocol
        
        Args:
            message: Telegram сообщение
            user_id: ID пользователя
            tariff: Словарь с данными тарифа
            email: Email пользователя
            country: Страна
            protocol: VPN протокол
            
        Returns:
            Tuple[payment_id, confirmation_url] или (None, None) при ошибке
        """
        try:
            logger.info(
                "LegacyAdapter.create_payment_with_email_and_protocol: user_id=%s, tariff_id=%s, country=%s, "
                "protocol=%s, provider=%s, method=%s",
                user_id,
                tariff.get("id") if isinstance(tariff, dict) else None,
                country,
                protocol,
                provider.value if isinstance(provider, PaymentProvider) else provider,
                method.value if isinstance(method, PaymentMethod) else method,
            )
            # Если email не передан, создаем временный email
            if not email:
                temp_email = f"user_{user_id}@veilbot.com"
                logger.info(f"Email not provided for user {user_id}, using temporary email: {temp_email}")
                email = temp_email
            
            # Конвертируем данные тарифа
            tariff_id = tariff.get('id', 1)
            amount = tariff.get('price_rub', 0) * 100  # Конвертируем в копейки
            
            # Создаем платеж через новый сервис
            payment_id, confirmation_url = await self.payment_service.create_payment(
                user_id=user_id,
                tariff_id=tariff_id,
                amount=amount,
                email=email,
                country=country,
                protocol=protocol,
                provider=provider,
                method=method,
            )
            
            if payment_id and confirmation_url:
                logger.info(f"Legacy payment created: {payment_id} for user {user_id}")
                return payment_id, confirmation_url
            else:
                logger.error(f"Failed to create legacy payment for user {user_id}")
                return None, None
                
        except Exception as e:
            logger.error(f"Error in legacy payment creation: {e}")
            return None, None
    
    async def wait_for_payment_with_protocol(
        self,
        message,
        payment_id: str,
        protocol: str,
        timeout_minutes: int = 5
    ) -> bool:
        """
        Адаптер для старой функции wait_for_payment_with_protocol
        
        Args:
            message: Telegram сообщение
            payment_id: ID платежа
            protocol: VPN протокол
            timeout_minutes: Таймаут в минутах
            
        Returns:
            True если платеж оплачен, False при таймауте
        """
        try:
            # Используем новый сервис для ожидания платежа
            success = await self.payment_service.wait_for_payment(
                payment_id=payment_id,
                timeout_minutes=timeout_minutes
            )
            
            if success:
                logger.info(f"Legacy payment completed: {payment_id}")
                # Здесь можно добавить логику создания ключа
                # await self.handle_payment_success(message, payment_id, protocol)
            else:
                logger.warning(f"Legacy payment timeout: {payment_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error in legacy payment waiting: {e}")
            return False
    
    async def handle_paid_tariff(
        self,
        message,
        payment_id: str,
        protocol: str
    ) -> bool:
        """
        Адаптер для старой функции handle_paid_tariff
        
        Args:
            message: Telegram сообщение
            payment_id: ID платежа
            protocol: VPN протокол
            
        Returns:
            True если обработка успешна
        """
        try:
            # Обрабатываем успешный платеж
            success = await self.payment_service.process_payment_success(payment_id)
            
            if success:
                logger.info(f"Legacy paid tariff handled: {payment_id}")
                # Здесь должна быть логика создания VPN ключа
                # await self.create_vpn_key(message, payment_id, protocol)
                return True
            else:
                logger.error(f"Failed to handle legacy paid tariff: {payment_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error handling legacy paid tariff: {e}")
            return False
    
    async def process_pending_paid_payments(self) -> int:
        """
        Адаптер для старой функции process_pending_paid_payments
        
        Returns:
            Количество обработанных платежей
        """
        try:
            # Используем новый сервис для обработки ожидающих платежей
            processed_count = await self.payment_service.process_pending_payments()
            
            # Также обрабатываем оплаченные платежи без ключей
            paid_without_keys_count = await self.payment_service.process_paid_payments_without_keys()
            
            total_processed = processed_count + paid_without_keys_count
            logger.info(f"Legacy payments processed: {processed_count} pending + {paid_without_keys_count} paid without keys = {total_processed}")
            return total_processed
            
        except Exception as e:
            logger.error(f"Error processing legacy pending payments: {e}")
            # Возвращаем 0 вместо падения, чтобы не прерывать работу бота
            return 0


# Функции-обертки для прямой замены старых функций
async def create_payment_with_email_and_protocol_legacy(
    message,
    user_id: int,
    tariff: Dict[str, Any],
    email: str,
    country: str,
    protocol: str,
    provider: PaymentProvider = PaymentProvider.YOOKASSA,
    method: Optional[PaymentMethod] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Обертка для прямой замены старой функции
    """
    try:
        # Получаем сервис из глобального контекста или создаем новый
        payment_service = get_payment_service()
        
        if not payment_service:
            logger.error(f"Payment service is None for user {user_id}")
            return None, None
        
        # Проверяем, что YooKassa сервис доступен
        if not hasattr(payment_service, 'yookassa_service') or not payment_service.yookassa_service:
            logger.error(f"YooKassa service is not available for user {user_id}")
            return None, None
        
        adapter = LegacyPaymentAdapter(payment_service)
        
        result = await adapter.create_payment_with_email_and_protocol(
            message, user_id, tariff, email, country, protocol, provider, method
        )
        
        # Если новый модуль вернул None (например, email не передан), 
        # возвращаем None для fallback на старую логику
        if result == (None, None):
            logger.warning(f"New payment module returned None for user {user_id}, tariff={tariff.get('name')}, amount={tariff.get('price_rub')}")
            return None, None
            
        return result
        
    except Exception as e:
        logger.error(f"Error in create_payment_with_email_and_protocol_legacy: {e}", exc_info=True)
        # В случае ошибки возвращаем None для fallback на старую логику
        return None, None


async def wait_for_payment_with_protocol_legacy(
    message,
    payment_id: str,
    protocol: str,
    timeout_minutes: int = 5
) -> bool:
    """
    Обертка для прямой замены старой функции
    """
    try:
        payment_service = get_payment_service()
        if not payment_service:
            logger.error("Payment service is not available in wait_for_payment_with_protocol_legacy")
            return False
        
        adapter = LegacyPaymentAdapter(payment_service)
        
        return await adapter.wait_for_payment_with_protocol(
            message, payment_id, protocol, timeout_minutes
        )
    except Exception as e:
        logger.error(f"Error in wait_for_payment_with_protocol_legacy: {e}", exc_info=True)
        return False


async def handle_paid_tariff_legacy(
    message,
    payment_id: str,
    protocol: str
) -> bool:
    """
    Обертка для прямой замены старой функции
    """
    payment_service = get_payment_service()
    adapter = LegacyPaymentAdapter(payment_service)
    
    return await adapter.handle_paid_tariff(message, payment_id, protocol)


async def process_pending_paid_payments_legacy() -> int:
    """
    Обертка для прямой замены старой функции
    """
    try:
        payment_service = get_payment_service()
        adapter = LegacyPaymentAdapter(payment_service)
        
        return await adapter.process_pending_paid_payments()
    except Exception as e:
        logger.error(f"Error in process_pending_paid_payments_legacy: {e}")
        # Возвращаем 0 в случае ошибки, чтобы не прерывать работу бота
        return 0


# Глобальная переменная для хранения сервиса (временное решение)
_payment_service = None


def get_payment_service() -> PaymentService:
    """Получение глобального экземпляра PaymentService"""
    global _payment_service
    if _payment_service is None:
        # Создаем сервис с параметрами из настроек
        from app.settings import settings as app_settings
        
        yookassa_service = YooKassaService(
            shop_id=app_settings.YOOKASSA_SHOP_ID or "default_shop_id",
            api_key=app_settings.YOOKASSA_API_KEY or "default_api_key",
            return_url=app_settings.YOOKASSA_RETURN_URL or "https://t.me/default_bot"
        )
        # Используем путь к БД из настроек для обеспечения консистентности
        payment_repo = PaymentRepository(db_path=app_settings.DATABASE_PATH)
        
        # Создаем CryptoBot сервис если токен есть
        cryptobot_service = None
        if app_settings.CRYPTOBOT_API_TOKEN:
            try:
                from payments.services.cryptobot_service import CryptoBotService
                cryptobot_service = CryptoBotService(
                    api_token=app_settings.CRYPTOBOT_API_TOKEN,
                    api_url=app_settings.CRYPTOBOT_API_URL
                )
            except Exception as e:
                import logging
                logging.warning(f"Не удалось создать CryptoBot сервис: {e}")
        
        _payment_service = PaymentService(payment_repo, yookassa_service, cryptobot_service)
    
    return _payment_service


def set_payment_service(service: PaymentService):
    """Установка глобального экземпляра PaymentService"""
    global _payment_service
    _payment_service = service
