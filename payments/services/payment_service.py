import asyncio
import logging
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime, timedelta, timezone

from ..models.payment import Payment, PaymentStatus, PaymentCreate, PaymentFilter
from ..models.enums import PaymentProvider, PaymentCurrency, PaymentMethod
from ..repositories.payment_repository import PaymentRepository
from ..services.yookassa_service import YooKassaService
from ..services.cryptobot_service import CryptoBotService
from ..services.platega_service import PlategaService
from ..utils.validators import PaymentValidators
from app.repositories.server_repository import ServerRepository
from app.repositories.key_repository import KeyRepository
from vpn_protocols import ProtocolFactory
from app.settings import settings as app_settings
from app.infra.sqlite_utils import open_connection
# Импорты из bot.py (используем прямые импорты, чтобы избежать циклических зависимостей)
# bot будет получен через get_bot() или передан как параметр
from bot.utils.formatters import format_key_message, format_key_message_unified
from bot.keyboards import get_main_menu
from security_logger import log_key_creation

# Получаем bot из глобального контекста bot.py
def get_bot():
    """Получение экземпляра бота через централизованный модуль"""
    try:
        from bot.core import get_bot_instance
        return get_bot_instance()
    except Exception as e:
        logger.error(f"Error getting bot instance: {e}")
        return None

logger = logging.getLogger(__name__)


class PaymentService:
    """Основной сервис для работы с платежами"""
    
    def __init__(
        self, 
        payment_repo: PaymentRepository,
        yookassa_service: Optional[YooKassaService],
        cryptobot_service: Optional[CryptoBotService] = None,
        platega_service: Optional[PlategaService] = None,
    ):
        self.payment_repo = payment_repo
        self.yookassa_service = yookassa_service
        self.cryptobot_service = cryptobot_service
        self.platega_service = platega_service
    
    async def create_payment(
        self,
        user_id: int,
        tariff_id: int,
        amount: int,
        email: str,
        country: Optional[str] = None,
        protocol: str = "outline",
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        provider: PaymentProvider = PaymentProvider.YOOKASSA,
        method: Optional[PaymentMethod] = None,
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
            
            # Подготавливаем метаданные
            payment_metadata = {
                "user_id": user_id,
                "tariff_id": tariff_id,
                "country": country,
                "protocol": protocol
            }
            if metadata:
                payment_metadata.update(metadata)

            provider_payment_id: Optional[str] = None
            confirmation_url: Optional[str] = None

            logger.info(
                "PaymentService.create_payment: user_id=%s, tariff_id=%s, amount=%s, provider=%s, method=%s, "
                "country=%s, protocol=%s",
                user_id,
                tariff_id,
                amount,
                provider.value if isinstance(provider, PaymentProvider) else provider,
                method.value if isinstance(method, PaymentMethod) else method,
                country,
                protocol,
            )

            if provider == PaymentProvider.PLATEGA:
                if not self.platega_service:
                    logger.error("Platega service is not configured")
                    return None, None
                logger.info(f"Calling platega_service.create_payment for user {user_id}, payment_id={payment_id}")
                try:
                    provider_payment_id, confirmation_url = await self.platega_service.create_payment(
                        amount=amount,
                        description=description,
                        email=email,
                        payment_id=payment_id,
                        metadata=payment_metadata,
                    )
                    logger.info(
                        "platega_service.create_payment returned: payment_id=%s, url=%s",
                        provider_payment_id,
                        "present" if confirmation_url else "None",
                    )
                except Exception as e:
                    logger.error(f"Exception in platega_service.create_payment: {e}", exc_info=True)
                    return None, None
            else:
                if not self.yookassa_service:
                    logger.error("YooKassa service is not configured")
                    return None, None
                logger.info(f"Calling yookassa_service.create_payment for user {user_id}, payment_id={payment_id}")
                try:
                    provider_payment_id, confirmation_url = await self.yookassa_service.create_payment(
                        amount=amount,
                        description=description,
                        email=email,
                        payment_id=payment_id,
                        metadata=payment_metadata
                    )
                    logger.info(
                        "yookassa_service.create_payment returned: payment_id=%s, url=%s",
                        provider_payment_id,
                        "present" if confirmation_url else "None",
                    )
                except Exception as e:
                    logger.error(f"Exception in yookassa_service.create_payment: {e}", exc_info=True)
                    return None, None
            
            if not provider_payment_id:
                logger.error(f"Failed to create payment for user {user_id}: provider_payment_id is None")
                return None, None

            # Создаем запись в БД
            payment = Payment(
                payment_id=provider_payment_id,
                user_id=user_id,
                tariff_id=tariff_id,
                amount=amount,
                email=email,
                country=country,
                protocol=protocol,
                provider=provider,
                method=method,
                description=description,
                metadata=payment_metadata
            )
            
            await self.payment_repo.create(payment)
            
            logger.info(f"Payment created successfully: {provider_payment_id} for user {user_id}")
            return provider_payment_id, confirmation_url
            
        except Exception as e:
            logger.error(f"Error creating payment: {e}")
            return None, None
    
    async def create_crypto_payment(
        self,
        user_id: int,
        tariff_id: int,
        amount_usd: float,
        email: str,
        country: Optional[str] = None,
        protocol: str = "outline",
        description: Optional[str] = None,
        asset: str = "USDT",
        network: str = "TRC20",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Создание криптоплатежа через CryptoBot
        
        Args:
            user_id: ID пользователя
            tariff_id: ID тарифа
            amount_usd: Сумма в USD
            email: Email для чека
            country: Страна
            protocol: VPN протокол
            description: Описание платежа
            asset: Криптовалюта (USDT, BTC, ETH и т.д.)
            network: Сеть (TRC20, ERC20, BEP20 для USDT)
            
        Returns:
            Tuple[invoice_id, payment_url] или (None, None) при ошибке
        """
        try:
            if not self.cryptobot_service:
                logger.error("CryptoBot service is not available")
                return None, None
            
            # Валидация входных данных
            if not PaymentValidators.validate_email(email):
                logger.error(f"Invalid email for user {user_id}: {email}")
                return None, None
            
            if amount_usd <= 0:
                logger.error(f"Invalid amount for user {user_id}: {amount_usd}")
                return None, None
            
            # Создаем описание если не передано
            if not description:
                description = f"Покупка VPN тарифа (протокол: {protocol})"
            
            # Генерируем уникальный ID платежа
            import uuid
            payment_id = str(uuid.uuid4())
            
            # Создаем инвойс в CryptoBot
            # Используем "openBot" для открытия бота после оплаты
            # Допустимые значения: 'viewItem', 'openChannel', 'openBot', 'callback'
            bot_username = "veilbot_bot"  # Имя бота
            bot_url = f"https://t.me/{bot_username}"
            
            invoice_id, pay_url, invoice_hash = await self.cryptobot_service.create_invoice(
                amount=amount_usd,
                asset=asset,
                description=description,
                paid_btn_name="openBot",
                paid_btn_url=bot_url,
                expires_in=3600,  # 1 час
                metadata={
                    "user_id": user_id,
                    "tariff_id": tariff_id,
                    "country": country,
                    "protocol": protocol,
                    "payment_id": payment_id
                }
            )
            
            if not invoice_id:
                logger.error(f"Failed to create CryptoBot invoice for user {user_id}")
                return None, None
            
            # Подготавливаем метаданные
            payment_metadata = {
                "user_id": user_id,
                "tariff_id": tariff_id,
                "country": country,
                "protocol": protocol,
                "invoice_id": invoice_id,
                "invoice_hash": invoice_hash,
                "asset": asset,
                "network": network,
                "amount_usd": amount_usd
            }
            if metadata:
                payment_metadata.update(metadata)
            
            # Создаем запись в БД
            payment = Payment(
                payment_id=str(invoice_id),  # Используем invoice_id как payment_id
                user_id=user_id,
                tariff_id=tariff_id,
                amount=int(amount_usd * 100),  # Конвертируем в центы для единообразия
                currency=PaymentCurrency.USD,
                email=email,
                country=country,
                protocol=protocol,
                provider=PaymentProvider.CRYPTOBOT,
                description=description,
                metadata=payment_metadata
            )
            
            # Сохраняем крипто-данные в метаданные (можно добавить отдельные поля позже)
            await self.payment_repo.create(payment)
            
            logger.info(f"CryptoBot payment created successfully: {invoice_id} for user {user_id}")
            return str(invoice_id), pay_url
            
        except Exception as e:
            logger.error(f"Error creating crypto payment: {e}")
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
            
            # Проверяем статус в зависимости от провайдера.
            # Для Platega webhook уже является источником истины (CONFIRMED),
            # поэтому дополнительный запрос check_payment можно пропустить.
            try:
                is_paid = False
                if payment.provider == PaymentProvider.PLATEGA:
                    logger.info(f"Skipping remote status check for Platega payment {payment_id} (using webhook status)")
                    is_paid = True
                else:
                    if not self.yookassa_service:
                        logger.error("YooKassa service not configured for payment success check")
                        return False
                    is_paid = await self.yookassa_service.check_payment(payment_id)
                    if not is_paid:
                        logger.warning(f"Payment {payment_id} not paid in YooKassa")
                        return False
            except Exception as e:
                # Если провайдер вернул ошибку/не найден, для наших старых платежей
                # считаем их оплаченными и полагаемся на БД/вебхуки.
                logger.debug(f"Payment {payment_id} not found in provider, assuming paid: {e}")
                is_paid = True
            
            # Обновляем статус в БД атомарно
            # Используем try_update_status для предотвращения race conditions
            from ..models.payment import PaymentStatus
            if payment.status == PaymentStatus.PENDING:
                atomic_success = await self.payment_repo.try_update_status(
                    payment_id,
                    PaymentStatus.PAID,
                    PaymentStatus.PENDING
                )
                if not atomic_success:
                    # Статус уже изменился, проверяем текущее состояние
                    updated_payment = await self.payment_repo.get_by_payment_id(payment_id)
                    if updated_payment and updated_payment.status == PaymentStatus.PAID:
                        logger.info(f"Payment {payment_id} already marked as paid")
                        payment = updated_payment
                    else:
                        logger.warning(f"Payment {payment_id} status changed unexpectedly")
                        return False
            else:
                # Если статус уже не pending, просто обновляем (fallback)
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
            payment = await self.payment_repo.get_by_payment_id(payment_id)
            provider = payment.provider if payment else PaymentProvider.YOOKASSA
            max_checks = (timeout_minutes * 60) // check_interval_seconds
            
            for _ in range(max_checks):
                try:
                    # Проверяем статус в зависимости от провайдера
                    if provider == PaymentProvider.PLATEGA:
                        if not self.platega_service:
                            logger.error("Platega service not configured for wait_for_payment")
                            return False
                        is_paid = await self.platega_service.check_payment(payment_id)
                    else:
                        if not self.yookassa_service:
                            logger.error("YooKassa service not configured for wait_for_payment")
                            return False
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
                    # Проверяем статус в зависимости от провайдера
                    if payment.provider == PaymentProvider.PLATEGA:
                        if not self.platega_service:
                            logger.error("Platega service not configured for pending check")
                            continue
                        is_paid = await self.platega_service.check_payment(payment.payment_id)
                    else:
                        if not self.yookassa_service:
                            logger.error("YooKassa service not configured for pending check")
                            continue
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
                    
                    # Добавляем задержку между обработкой платежей для избежания rate limit
                    # Увеличиваем задержку до 15 секунд для V2Ray (rate limit: 5 запросов в минуту)
                    if (payment.protocol or 'outline') == 'v2ray':
                        await asyncio.sleep(15)  # 15 секунд = 4 запроса в минуту (безопасно)
                    else:
                        await asyncio.sleep(2)
                    
                    # Используем ту же логику, что и при покупке/продлении через wait_for_payment_with_protocol
                    # Это обеспечивает правильную обработку продления (если ключ существует, он будет продлен)
                    try:
                        from app.infra.sqlite_utils import get_db_cursor
                        from bot.services.key_creation import create_new_key_flow_with_protocol
                        
                        # Получаем тариф
                        tariff = None
                        with open_connection(app_settings.DATABASE_PATH) as conn:
                            c = conn.cursor()
                            c.execute("SELECT id, name, duration_sec, price_rub, traffic_limit_mb FROM tariffs WHERE id = ?", (payment.tariff_id,))
                            tariff_row = c.fetchone()
                            if tariff_row:
                                tariff = {
                                    'id': tariff_row[0],
                                    'name': tariff_row[1],
                                    'duration_sec': tariff_row[2],
                                    'price_rub': tariff_row[3],
                                    'traffic_limit_mb': tariff_row[4],
                                }
                        
                        if not tariff:
                            logger.error(f"Tariff {payment.tariff_id} not found for payment {payment.payment_id}")
                            continue
                        
                        # Используем create_new_key_flow_with_protocol, которая автоматически:
                        # 1. Проверит наличие существующего ключа
                        # 2. Если ключ существует - продлит его (обновит expiry_at)
                        # 3. Если ключа нет - создаст новый
                        # Это та же логика, что и при покупке/продлении через wait_for_payment_with_protocol
                        with get_db_cursor(commit=True) as cursor:
                            cursor.execute(
                                "SELECT status FROM payments WHERE payment_id = ?",
                                (payment.payment_id,),
                            )
                            status_row = cursor.fetchone()
                            if status_row:
                                payment_status = (status_row[0] or "").lower()
                                if payment_status == "completed":
                                    logger.info(
                                        "Payment %s already completed, skipping key issuance",
                                        payment.payment_id,
                                    )
                                    continue
                                if payment_status != "paid":
                                    cursor.execute(
                                        "UPDATE payments SET status = 'paid' WHERE payment_id = ?",
                                        (payment.payment_id,),
                                    )
                            else:
                                logger.warning(
                                    "Payment %s disappeared before key issuance, skipping",
                                    payment.payment_id,
                                )
                                continue
                            
                            # Проверяем, нужно ли создавать подписку вместо отдельного ключа
                            key_type = payment.metadata.get('key_type') if payment.metadata else None
                            
                            if key_type == 'subscription' and payment.protocol == 'v2ray':
                                # Для подписок НЕ определяем for_renewal на основе ключей,
                                # так как это может привести к неправильному определению продления
                                # (ключи могут быть созданы этим же платежом)
                                # Логика продления/создания определяется в process_subscription_purchase
                                
                                # Используем новый сервис для обработки подписки
                                from ..services.subscription_purchase_service import SubscriptionPurchaseService
                                
                                subscription_service = SubscriptionPurchaseService()
                                success, error_msg = await subscription_service.process_subscription_purchase(payment.payment_id)
                                
                                if success:
                                    logger.info(
                                        f"Subscription purchase processed successfully for payment {payment.payment_id}"
                                    )
                                    processed_count += 1
                                else:
                                    logger.error(
                                        f"Failed to process subscription purchase for payment {payment.payment_id}: {error_msg}"
                                    )
                                    # НЕ помечаем платеж как completed при ошибке, чтобы повторить попытку
                                
                                continue  # Пропускаем создание обычного ключа для подписок
                            else:
                                # Для обычных ключей определяем, это продление или покупка
                                # ИСПРАВЛЕНО: Используем унифицированную функцию для определения продления
                                from ..utils.renewal_detector import is_renewal_payment, DEFAULT_GRACE_PERIOD
                                
                                for_renewal = is_renewal_payment(
                                    cursor,
                                    payment.user_id,
                                    payment.protocol or 'outline',
                                    DEFAULT_GRACE_PERIOD
                                )
                                
                                logger.info(
                                    f"Processing payment {payment.payment_id} for user {payment.user_id}: for_renewal={for_renewal}"
                                )
                                
                                # Вызываем create_new_key_flow_with_protocol, которая обработает и продление, и создание нового ключа
                                await create_new_key_flow_with_protocol(
                                    cursor=cursor,
                                    message=None,  # Отправка сообщения будет внутри функции
                                    user_id=payment.user_id,
                                    tariff=tariff,
                                    email=payment.email,
                                    country=payment.country,
                                    protocol=payment.protocol or 'outline',
                                    for_renewal=for_renewal,  # Если есть активный ключ, это продление
                                )

                        # После успешной выдачи/продления ключа фиксируем статус платежа отдельно,
                        # чтобы обновление выполнялось после коммита в блоке get_db_cursor
                        payment.mark_as_completed()
                        await self.payment_repo.update(payment)
                        logger.info(f"Payment {payment.payment_id} marked as completed after key creation/renewal")
                        
                        processed_count += 1
                        # Небольшая задержка между обработкой
                        await asyncio.sleep(0.05)
                        
                    except Exception as e:
                        logger.error(f"Error processing payment {payment.payment_id} with create_new_key_flow_with_protocol: {e}", exc_info=True)
                        continue
                    
                except Exception as e:
                    logger.error(f"Error processing payment {payment.payment_id}: {e}", exc_info=True)
                    continue
            
            logger.info(f"Found {processed_count} paid payments without keys")
            return processed_count
            
        except Exception as e:
            logger.error(f"Error processing paid payments without keys: {e}")
            return 0

    async def issue_key_for_payment(self, payment_id: str) -> bool:
        """Идемпотентная выдача ключа для конкретного платежа."""
        try:
            payment = await self.payment_repo.get_by_payment_id(payment_id)
            if not payment or not payment.is_paid():
                return False

            # Проверяем, нет ли у пользователя активных ключей уже (как в get_paid_payments_without_keys)
            # Если есть — считаем, что ключ уже выдан
            from app.settings import settings as app_settings
            now_ts = int(datetime.now(timezone.utc).timestamp())
            with open_connection(app_settings.DATABASE_PATH) as conn:
                c = conn.cursor()
                c.execute("SELECT 1 FROM keys WHERE user_id = ? AND expiry_at > ? LIMIT 1", (payment.user_id, now_ts))
                if c.fetchone():
                    return True
                c.execute("SELECT 1 FROM v2ray_keys WHERE user_id = ? AND expiry_at > ? LIMIT 1", (payment.user_id, now_ts))
                if c.fetchone():
                    return True

            # Повторно используем логику из пакетной выдачи: создадим фиктивный список и обработаем
            # Чтобы не дублировать код, временно выполним ту же ветку, что и в основном методе
            # Упрощенно — создадим один элемент и прогоним через ту же логику
            # (дублирование кода можно вынести в приватный helper в будущем)
            class _Obj:
                pass
            # Создаем "одиночный" список
            original_get = self.get_paid_payments_without_keys
            async def _single_list():
                return [payment]
            self.get_paid_payments_without_keys = _single_list  # type: ignore
            try:
                processed = await self.process_paid_payments_without_keys()
                return processed > 0
            finally:
                self.get_paid_payments_without_keys = original_get  # type: ignore
        except Exception as e:
            logger.error(f"Error issuing key for payment {payment_id}: {e}")
            return False
    
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
            since_date = datetime.now(timezone.utc) - timedelta(days=days)
            
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
        Очистка истекших платежей (перевод старых pending в статус expired)
        
        Args:
            hours: Количество часов для определения истечения
            
        Returns:
            Количество помеченных как expired платежей
        """
        try:
            # Получаем истекшие платежи
            since_date = datetime.now(timezone.utc) - timedelta(hours=hours)
            filter_obj = PaymentFilter(
                created_before=since_date,
                is_pending=True,
                limit=1000
            )
            expired_payments = await self.payment_repo.filter(filter_obj)
            
            # Помечаем как истекшие
            cleaned_count = 0
            for payment in expired_payments:
                payment.mark_as_expired()
                await self.payment_repo.update(payment)
                cleaned_count += 1
            
            logger.info(f"Cleaned up {cleaned_count} expired payments")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"Error cleaning up expired payments: {e}")
            return 0
