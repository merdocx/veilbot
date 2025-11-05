import asyncio
import logging
from typing import Optional, Tuple, List
from datetime import datetime, timedelta

from ..models.payment import Payment, PaymentStatus, PaymentCreate, PaymentFilter
from ..models.enums import PaymentProvider, PaymentCurrency
from ..repositories.payment_repository import PaymentRepository
from ..services.yookassa_service import YooKassaService
from ..services.cryptobot_service import CryptoBotService
from ..utils.validators import PaymentValidators
from app.repositories.server_repository import ServerRepository
from app.repositories.key_repository import KeyRepository
from vpn_protocols import ProtocolFactory
from app.settings import settings as app_settings
# Импорты из bot.py (используем прямые импорты, чтобы избежать циклических зависимостей)
# bot будет получен через get_bot() или передан как параметр
from bot.utils.formatters import format_key_message, format_key_message_unified
from bot.keyboards import get_main_menu
from security_logger import log_key_creation

# Получаем bot из глобального контекста bot.py
def get_bot():
    """Получение экземпляра бота из bot.py"""
    try:
        import sys
        if 'bot' in sys.modules:
            bot_module = sys.modules['bot']
            if hasattr(bot_module, 'bot'):
                return bot_module.bot
        # Если не нашли, пытаемся импортировать напрямую
        import bot as bot_module
        return bot_module.bot
    except Exception as e:
        logger.error(f"Error getting bot instance: {e}")
        return None

logger = logging.getLogger(__name__)


class PaymentService:
    """Основной сервис для работы с платежами"""
    
    def __init__(
        self, 
        payment_repo: PaymentRepository,
        yookassa_service: YooKassaService,
        cryptobot_service: Optional[CryptoBotService] = None
    ):
        self.payment_repo = payment_repo
        self.yookassa_service = yookassa_service
        self.cryptobot_service = cryptobot_service
    
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
        network: str = "TRC20"
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
                metadata={
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
                    
                    # Идемпотентность: повторная проверка статуса в БД
                    # Если в ходе гонки ключ уже выдан, пропускаем
                    # (доп. проверка на наличие активных ключей у пользователя)
                    key_repo = KeyRepository()
                    sr = ServerRepository()

                    # Выбор серверов по протоколу и стране
                    servers = sr.list_servers()
                    target_servers = [
                        s for s in servers
                        if (len(s) > 7 and (s[7] or 'outline') == (payment.protocol or 'outline'))
                        and (not payment.country or (len(s) > 6 and (s[6] or '') == payment.country))
                        and (len(s) > 5 and int(s[5]) == 1)
                    ]
                    if not target_servers and servers:
                        # Фолбэк: любой активный сервер для данного протокола
                        target_servers = [
                            s for s in servers
                            if (len(s) > 7 and (s[7] or 'outline') == (payment.protocol or 'outline'))
                            and (len(s) > 5 and int(s[5]) == 1)
                        ]
                    if not target_servers:
                        logger.error(f"No active servers for protocol={payment.protocol} country={payment.country}")
                        continue

                    server = target_servers[0]
                    server_id, name, api_url, cert_sha256, max_keys, active, country, protocol, domain, api_key, v2ray_path = (
                        server + (None,) * (11 - len(server))
                    )

                    # Создание ключа в соответствии с протоколом
                    protocol_config = {
                        'api_url': api_url,
                        'cert_sha256': cert_sha256,
                        'api_key': api_key,
                        'domain': domain,
                        'v2ray_path': v2ray_path,
                    }
                    client = ProtocolFactory.create_protocol(payment.protocol or 'outline', protocol_config)

                    email = payment.email or f"user_{payment.user_id}@veilbot.com"
                    user_data = await client.create_user(email)

                    # Определение срока по тарифу (если возможно)
                    expiry_ts = int((payment.created_at or datetime.utcnow()).timestamp()) + 30*24*3600
                    try:
                        import sqlite3
                        from app.settings import settings as app_settings
                        with sqlite3.connect(app_settings.DATABASE_PATH) as conn:
                            c = conn.cursor()
                            c.execute("SELECT duration_sec FROM tariffs WHERE id = ?", (payment.tariff_id,))
                            row = c.fetchone()
                            if row and row[0]:
                                expiry_ts = int((payment.created_at or datetime.utcnow()).timestamp()) + int(row[0])
                    except Exception:
                        pass

                    # Сохранение ключа в БД (идемпотентно)
                    if (payment.protocol or 'outline') == 'outline':
                        # В таблицу keys
                        access_url = user_data.get('accessUrl') or ''
                        outline_key_id = user_data.get('id') or ''
                        new_key_id = key_repo.insert_outline_key(
                            server_id=server_id,
                            user_id=payment.user_id,
                            access_url=access_url,
                            expiry_at=expiry_ts,
                            key_id=outline_key_id,
                            email=payment.email,
                            tariff_id=payment.tariff_id,
                        )
                        try:
                            log_key_creation(
                                user_id=payment.user_id,
                                key_id=outline_key_id,
                                protocol='outline',
                                server_id=server_id or 0,
                                tariff_id=payment.tariff_id or 0,
                            )
                        except Exception:
                            pass
                        try:
                            # Отправка ключа пользователю
                            bot_instance = get_bot()
                            if bot_instance:
                                await bot_instance.send_message(
                                    payment.user_id,
                                    format_key_message(access_url),
                                    reply_markup=get_main_menu(),
                                    disable_web_page_preview=True,
                                    parse_mode="Markdown",
                                )
                        except Exception as e:
                            logger.error(f"Failed to notify user {payment.user_id} about outline key: {e}")
                    else:
                        # V2Ray → v2ray_keys
                        v2ray_uuid = user_data.get('uuid') or user_data.get('id') or ''
                        key_repo.insert_v2ray_key(
                            server_id=server_id,
                            user_id=payment.user_id,
                            v2ray_uuid=v2ray_uuid,
                            email=payment.email,
                            created_at=int((payment.created_at or datetime.utcnow()).timestamp()),
                            expiry_at=expiry_ts,
                            tariff_id=payment.tariff_id,
                        )
                        try:
                            log_key_creation(
                                user_id=payment.user_id,
                                key_id=v2ray_uuid,
                                protocol='v2ray',
                                server_id=server_id or 0,
                                tariff_id=payment.tariff_id or 0,
                            )
                        except Exception:
                            pass
                        try:
                            domain_eff = domain or 'veil-bird.ru'
                            v2ray_path_eff = v2ray_path or '/v2ray'
                            access_url = f"vless://{v2ray_uuid}@{domain_eff}:443?path={v2ray_path_eff}&security=tls&type=ws#VeilBot-V2Ray"
                            # Используем унифицированный формат
                            bot_instance = get_bot()
                            if bot_instance:
                                await bot_instance.send_message(
                                    payment.user_id,
                                    format_key_message_unified(access_url, 'v2ray', None, None),
                                    reply_markup=get_main_menu(),
                                    disable_web_page_preview=True,
                                    parse_mode="Markdown",
                                )
                        except Exception as e:
                            logger.error(f"Failed to notify user {payment.user_id} about v2ray key: {e}")
                    
                    processed_count += 1
                    # Небольшая задержка между обработкой
                    await asyncio.sleep(0.05)
                    
                except Exception as e:
                    logger.error(f"Error processing payment {payment.payment_id}: {e}")
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
            import sqlite3
            now_ts = int(datetime.utcnow().timestamp())
            with sqlite3.connect(app_settings.DATABASE_PATH) as conn:
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
