"""
Единый сервис для обработки покупки подписки V2Ray
Переписано с нуля по аналогии с ключами - максимально просто и надежно
"""
import uuid
import time
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

from ..models.payment import Payment, PaymentStatus
from ..repositories.payment_repository import PaymentRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.tariff_repository import TariffRepository
from app.infra.sqlite_utils import open_async_connection
from app.settings import settings as app_settings
from vpn_protocols import ProtocolFactory, format_duration
from bot.core import get_bot_instance
from bot.utils import safe_send_message, format_key_message_unified
from bot.keyboards import get_main_menu
from bot.services.subscription_traffic_reset import reset_subscription_traffic

logger = logging.getLogger(__name__)


class SubscriptionPurchaseService:
    """Сервис для обработки покупки подписки - переписан с нуля по аналогии с ключами"""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or app_settings.DATABASE_PATH
        self.payment_repo = PaymentRepository(db_path)
        self.subscription_repo = SubscriptionRepository(db_path)
        self.tariff_repo = TariffRepository(db_path)
    
    async def process_subscription_purchase(self, payment_id: str) -> Tuple[bool, Optional[str]]:
        """
        Обработать покупку подписки для оплаченного платежа
        
        Логика:
        1. Пользователь оплатил, мы получили статус paid
        2. Проверяем есть ли созданная подписка или нет
        3. Если подписка уже создана - это продление, если нет - это покупка
        
        Если покупка:
        - создаем подписку
        - отправляем уведомление о покупке
        - переводим платеж в статус completed
        
        Если продление:
        - продлеваем подписку
        - отправляем уведомление о продлении
        - переводим платеж в статус completed
        
        Args:
            payment_id: ID платежа в YooKassa
            
        Returns:
            Tuple[success, error_message]
        """
        try:
            logger.info(f"[SUBSCRIPTION] Processing subscription purchase for payment {payment_id}")
            
            # Шаг 1: Получаем платеж
            payment = await self.payment_repo.get_by_payment_id(payment_id)
            if not payment:
                error_msg = f"Payment {payment_id} not found"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            # Шаг 2: Проверяем, что это платеж за подписку
            if not (payment.metadata and payment.metadata.get('key_type') == 'subscription'):
                error_msg = f"Payment {payment_id} is not a subscription payment"
                logger.warning(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            if payment.protocol != 'v2ray':
                error_msg = f"Payment {payment_id} protocol is not v2ray"
                logger.warning(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            # Шаг 3: Проверяем статус платежа - должен быть paid
            # Если платеж уже completed, значит он уже обработан (возможно другим процессом)
            if payment.status == PaymentStatus.COMPLETED:
                logger.info(f"[SUBSCRIPTION] Payment {payment_id} already completed, skipping")
                return True, None
            
            if payment.status != PaymentStatus.PAID:
                error_msg = f"Payment {payment_id} is not paid (status: {payment.status.value}), cannot process subscription"
                logger.warning(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            # Шаг 4: Получаем тариф
            tariff_row = self.tariff_repo.get_tariff(payment.tariff_id)
            if not tariff_row:
                error_msg = f"Tariff {payment.tariff_id} not found"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            tariff = {
                'id': tariff_row[0],
                'name': tariff_row[1],
                'duration_sec': tariff_row[2],
                'price_rub': tariff_row[3],
                'traffic_limit_mb': tariff_row[4] if len(tariff_row) > 4 else 0,
            }
            
            # Дополнительная проверка статуса после получения тарифа (защита от race condition)
            payment_check = await self.payment_repo.get_by_payment_id(payment_id)
            if payment_check and payment_check.status == PaymentStatus.COMPLETED:
                logger.info(f"[SUBSCRIPTION] Payment {payment_id} was completed by another process, skipping")
                return True, None
            
            logger.info(
                f"[SUBSCRIPTION] Processing: payment={payment_id}, user={payment.user_id}, "
                f"tariff={tariff['name']}, duration={tariff['duration_sec']}s"
            )
            
            # Шаг 5: Определяем, это покупка или продление
            # ВАЖНО: Если у пользователя есть активный бесплатный ключ (включая grace period),
            # то любая оплата - это продление, а не покупка
            # Проверяем наличие активной подписки с учетом grace_period
            # ВАЖНО: Если подписка существует и это новый платеж (не retry), это продление
            # Если подписка была создана недавно и уведомление о покупке не отправлено,
            # проверяем, не является ли это retry того же платежа
            from ..utils.renewal_detector import DEFAULT_GRACE_PERIOD
            
            now = int(time.time())
            grace_threshold = now - DEFAULT_GRACE_PERIOD  # 24 часа назад
            RECENT_SUBSCRIPTION_THRESHOLD = 1800  # 30 минут - если подписка создана недавно, это может быть покупка
            
            # Проверяем наличие активной подписки (с учетом grace_period)
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT s.id, s.user_id, s.subscription_token, s.created_at, s.expires_at, s.tariff_id, s.is_active, s.last_updated_at, s.notified, s.purchase_notification_sent,
                           t.price_rub
                    FROM subscriptions s
                    LEFT JOIN tariffs t ON s.tariff_id = t.id
                    WHERE s.user_id = ? AND s.is_active = 1 AND s.expires_at > ?
                    ORDER BY s.created_at DESC
                    LIMIT 1
                    """,
                    (payment.user_id, grace_threshold)
                ) as cursor:
                    existing_subscription_row = await cursor.fetchone()
            
            # Проверяем наличие активной бесплатной подписки (включая grace period)
            # Бесплатная подписка определяется по price_rub = 0 или tariff_id = FREE_V2RAY_TARIFF_ID
            from app.settings import settings as app_settings
            FREE_V2RAY_TARIFF_ID = app_settings.FREE_V2RAY_TARIFF_ID
            
            has_active_free_subscription = False
            if existing_subscription_row:
                subscription_tariff_id = existing_subscription_row[5] if len(existing_subscription_row) > 5 else None
                subscription_price_rub = existing_subscription_row[10] if len(existing_subscription_row) > 10 else None
                # Проверяем, является ли подписка бесплатной
                has_active_free_subscription = (
                    subscription_tariff_id == FREE_V2RAY_TARIFF_ID or
                    (subscription_price_rub is not None and subscription_price_rub == 0)
                )
            
            # Если есть активная бесплатная подписка, любая оплата - это продление
            if has_active_free_subscription:
                if existing_subscription_row:
                    # Есть активная бесплатная подписка - продлеваем её
                    logger.info(
                        f"[SUBSCRIPTION] User {payment.user_id} has active free subscription "
                        f"(tariff_id={subscription_tariff_id}, price_rub={subscription_price_rub}, grace_threshold={grace_threshold}). "
                        f"This is a RENEWAL."
                    )
                    # Преобразуем в tuple для совместимости с _extend_subscription (без price_rub)
                    existing_subscription = existing_subscription_row[:10]
                    return await self._extend_subscription(payment, tariff, existing_subscription, now, is_purchase=False)
                else:
                    # Это не должно произойти, но на всякий случай
                    logger.warning(
                        f"[SUBSCRIPTION] User {payment.user_id} has active free subscription but row is None. "
                        f"This should not happen."
                    )
            
            if existing_subscription_row:
                subscription_id = existing_subscription_row[0]
                subscription_token = existing_subscription_row[2]
                existing_expires_at = existing_subscription_row[4]
                created_at = existing_subscription_row[3]
                purchase_notification_sent = existing_subscription_row[9] if len(existing_subscription_row) > 9 else 0
                
                # Проверяем, была ли подписка создана недавно и уведомление о покупке не отправлено
                subscription_age = now - created_at if created_at else 0
                is_recent_subscription = subscription_age < RECENT_SUBSCRIPTION_THRESHOLD
                purchase_notification_not_sent = not purchase_notification_sent
                
                # ВАЖНО: Проверяем, не была ли подписка создана очень недавно (менее 5 минут)
                # и соответствует ли срок действия подписки ожидаемому (created_at + duration)
                # Если срок действия = created_at + duration, значит это только что созданная подписка
                # и её не нужно продлевать, а нужно просто отправить уведомление о покупке
                VERY_RECENT_THRESHOLD = 300  # 5 минут
                expected_expires_at = created_at + tariff['duration_sec']
                is_very_recent = subscription_age < VERY_RECENT_THRESHOLD
                expires_at_matches_expected = abs(existing_expires_at - expected_expires_at) < 3600  # Разница менее часа
                
                # Если подписка создана очень недавно и срок соответствует ожидаемому (только что создана),
                # проверяем, есть ли уже другие completed платежи для этой подписки
                if is_very_recent and expires_at_matches_expected:
                    # Проверяем, есть ли другие completed платежи для этого пользователя с тем же тарифом
                    # созданные после создания подписки
                    async with open_async_connection(self.db_path) as conn:
                        async with conn.execute(
                            """
                            SELECT COUNT(*) FROM payments
                            WHERE user_id = ? 
                            AND tariff_id = ?
                            AND status = 'completed'
                            AND protocol = 'v2ray'
                            AND metadata LIKE '%subscription%'
                            AND created_at >= ?
                            AND payment_id != ?
                            """,
                            (payment.user_id, payment.tariff_id, created_at, payment.payment_id)
                        ) as check_cursor:
                            other_completed_count = (await check_cursor.fetchone())[0]
                            
                            # Если других completed платежей нет, это покупка (даже если purchase_notification_sent=1)
                            # Это может быть retry обработки того же платежа или подписка была создана автоматически
                            if other_completed_count == 0:
                                logger.info(
                                    f"[SUBSCRIPTION] User {payment.user_id} has very recent subscription {subscription_id} "
                                    f"(created {subscription_age}s ago, expires_at={existing_expires_at}, "
                                    f"expected={expected_expires_at}, purchase_notification_sent={purchase_notification_sent}). "
                                    f"No other completed payments found. This is a PURCHASE (subscription just created, not a renewal)."
                                )
                                
                                # Используем существующую подписку, но отправляем уведомление о покупке
                                # НЕ продлеваем подписку, так как она уже создана с правильным сроком
                                existing_subscription = existing_subscription_row
                                return await self._send_purchase_notification_for_existing_subscription(
                                    payment, tariff, existing_subscription, now
                                )
                            elif purchase_notification_not_sent:
                                # Есть другие completed платежи, но уведомление не отправлено - это может быть retry
                                logger.info(
                                    f"[SUBSCRIPTION] User {payment.user_id} has very recent subscription {subscription_id} "
                                    f"(created {subscription_age}s ago, expires_at={existing_expires_at}, "
                                    f"expected={expected_expires_at}, purchase_notification_sent={purchase_notification_sent}). "
                                    f"This is a PURCHASE (subscription just created, not a renewal)."
                                )
                                
                                # Используем существующую подписку, но отправляем уведомление о покупке
                                # НЕ продлеваем подписку, так как она уже создана с правильным сроком
                                existing_subscription = existing_subscription_row
                                return await self._send_purchase_notification_for_existing_subscription(
                                    payment, tariff, existing_subscription, now
                                )
                
                # Проверяем, не является ли это retry того же платежа
                # Если подписка была создана недавно и уведомление не отправлено, 
                # но это может быть retry обработки того же платежа
                # Проверяем, есть ли уже другие платежи для этой подписки, которые были обработаны
                is_likely_retry = False
                if is_recent_subscription and purchase_notification_not_sent:
                    # Проверяем, есть ли другие completed платежи для этого пользователя с тем же тарифом
                    # созданные примерно в то же время (в пределах 1 часа)
                    async with open_async_connection(self.db_path) as conn:
                        async with conn.execute(
                            """
                            SELECT COUNT(*) FROM payments
                            WHERE user_id = ? 
                            AND tariff_id = ?
                            AND status = 'completed'
                            AND protocol = 'v2ray'
                            AND metadata LIKE '%subscription%'
                            AND created_at > ?
                            AND payment_id != ?
                            """,
                            (payment.user_id, payment.tariff_id, created_at - 3600, payment.payment_id)
                        ) as check_cursor:
                            other_completed_count = (await check_cursor.fetchone())[0]
                            # Если есть другие completed платежи, это скорее всего продление, а не retry
                            # Если других completed платежей нет, это может быть retry
                            is_likely_retry = other_completed_count == 0
                
                if is_recent_subscription and purchase_notification_not_sent and is_likely_retry:
                    # Подписка создана недавно, уведомление не отправлено, и это похоже на retry того же платежа
                    logger.info(
                        f"[SUBSCRIPTION] User {payment.user_id} has recent subscription {subscription_id} "
                        f"(created {subscription_age}s ago, purchase_notification_sent={purchase_notification_sent}). "
                        f"This is likely a PURCHASE (retry of same payment)."
                    )
                    
                    # Используем существующую подписку, но отправляем уведомление о покупке
                    existing_subscription = existing_subscription_row
                    return await self._send_purchase_notification_for_existing_subscription(
                        payment, tariff, existing_subscription, now
                    )
                
                # Есть активная подписка - это ПРОДЛЕНИЕ
                # (независимо от того, когда была создана подписка, если это новый платеж)
                logger.info(
                    f"[SUBSCRIPTION] User {payment.user_id} has active subscription {subscription_id} "
                    f"(expires_at={existing_expires_at}, created_at={created_at}, age={subscription_age}s, "
                    f"purchase_notification_sent={purchase_notification_sent}, grace_threshold={grace_threshold}). "
                    f"This is a RENEWAL."
                )
                
                # Преобразуем в tuple для совместимости с _extend_subscription
                existing_subscription = existing_subscription_row
                
                # ПРОДЛЕНИЕ: Продлеваем существующую подписку
                return await self._extend_subscription(payment, tariff, existing_subscription, now, is_purchase=False)
            else:
                # Нет активной подписки - это ПОКУПКА
                logger.info(
                    f"[SUBSCRIPTION] User {payment.user_id} has no active subscription "
                    f"(grace_threshold={grace_threshold}). This is a PURCHASE."
                )
                
                # СОЗДАНИЕ: Создаем новую подписку
                return await self._create_subscription(payment, tariff, now)
            
        except Exception as e:
            error_msg = f"Error processing subscription purchase for payment {payment_id}: {e}"
            logger.error(f"[SUBSCRIPTION] {error_msg}", exc_info=True)
            return False, error_msg
    
    async def _create_subscription_as_renewal(
        self, 
        payment: Payment, 
        tariff: Dict[str, Any], 
        now: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Создать подписку как продление бесплатного ключа
        
        Используется когда у пользователя есть активный бесплатный ключ,
        но нет подписки. Создаем подписку и отправляем уведомление о продлении.
        """
        try:
            # Сначала проверяем, не была ли подписка уже создана другим процессом
            from ..utils.renewal_detector import DEFAULT_GRACE_PERIOD
            grace_threshold = now - DEFAULT_GRACE_PERIOD
            
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified, purchase_notification_sent
                    FROM subscriptions
                    WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (payment.user_id, grace_threshold)
                ) as cursor:
                    existing_subscription_row = await cursor.fetchone()
            
            if existing_subscription_row:
                # Подписка уже существует
                subscription_id = existing_subscription_row[0]
                existing_created_at = existing_subscription_row[3]
                existing_expires_at = existing_subscription_row[4]
                
                # ВАЖНО: Проверяем, не была ли подписка создана очень недавно (менее 5 минут)
                # и соответствует ли срок действия подписки ожидаемому (created_at + duration)
                VERY_RECENT_THRESHOLD = 300  # 5 минут
                subscription_age = now - existing_created_at if existing_created_at else 0
                expected_expires_at = existing_created_at + tariff['duration_sec']
                is_very_recent = subscription_age < VERY_RECENT_THRESHOLD
                expires_at_matches_expected = abs(existing_expires_at - expected_expires_at) < 3600  # Разница менее часа
                
                # Если подписка создана очень недавно и срок соответствует ожидаемому - это покупка, не продление
                if is_very_recent and expires_at_matches_expected:
                    logger.info(
                        f"[SUBSCRIPTION] Subscription {subscription_id} was just created ({subscription_age}s ago) "
                        f"for user {payment.user_id}, expires_at matches expected. "
                        f"This is a PURCHASE, not a renewal. Sending purchase notification."
                    )
                    existing_subscription = existing_subscription_row
                    return await self._send_purchase_notification_for_existing_subscription(
                        payment, tariff, existing_subscription, now
                    )
                
                # Продлеваем существующую подписку
                logger.info(
                    f"[SUBSCRIPTION] Subscription {subscription_id} already exists for user {payment.user_id}. "
                    f"Extending as renewal."
                )
                existing_subscription = existing_subscription_row
                return await self._extend_subscription(payment, tariff, existing_subscription, now, is_purchase=False)
            
            # Создаем новую подписку
            # ВАЛИДАЦИЯ: Проверяем, что duration_sec не None и не 0
            duration_sec = tariff.get('duration_sec', 0) or 0
            if duration_sec is None or duration_sec <= 0:
                error_msg = f"Invalid tariff duration_sec for user {payment.user_id}, tariff_id={tariff.get('id')}: duration_sec={duration_sec}"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            # Проверяем VIP статус пользователя
            from app.repositories.user_repository import UserRepository
            user_repo = UserRepository(self.db_path)
            is_vip = user_repo.is_user_vip(payment.user_id)
            
            # Константы для VIP подписок
            VIP_EXPIRES_AT = 4102434000  # 01.01.2100 00:00 UTC
            
            if is_vip:
                expires_at = VIP_EXPIRES_AT
                logger.info(f"[SUBSCRIPTION] Creating VIP subscription renewal for user {payment.user_id}, expires_at={expires_at}")
            else:
                expires_at = now + duration_sec
            
            # Валидация даты истечения (только для не-VIP)
            if not is_vip:
                MAX_REASONABLE_EXPIRY = now + (10 * 365 * 24 * 3600)  # 10 лет
                if expires_at > MAX_REASONABLE_EXPIRY:
                    error_msg = f"Calculated expiry date is too far in future: {expires_at}"
                    logger.error(f"[SUBSCRIPTION] {error_msg}")
                    return False, error_msg
            
            # Генерируем уникальный токен
            subscription_token = None
            for _ in range(10):
                token = str(uuid.uuid4())
                if not await self.subscription_repo.get_subscription_by_token_async(token):
                    subscription_token = token
                    break
            
            if not subscription_token:
                error_msg = "Failed to generate unique subscription token after 10 attempts"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            # Создаем подписку в БД
            traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
            subscription_id = await self.subscription_repo.create_subscription_async(
                user_id=payment.user_id,
                subscription_token=subscription_token,
                expires_at=expires_at,
                tariff_id=tariff['id'],
                traffic_limit_mb=traffic_limit_mb,
            )
            
            logger.info(
                f"[SUBSCRIPTION] Created subscription {subscription_id} for user {payment.user_id} as renewal of free key, "
                f"expires_at={expires_at}"
            )
            
            # Создаем ключи на всех активных V2Ray серверах
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, name, api_url, api_key, domain, v2ray_path
                    FROM servers
                    WHERE protocol = 'v2ray' AND active = 1
                    ORDER BY id
                    """
                ) as cursor:
                    servers = await cursor.fetchall()
            
            created_keys = 0
            failed_servers = []
            
            for server_id, server_name, api_url, api_key, domain, v2ray_path in servers:
                v2ray_uuid = None
                protocol_client = None
                try:
                    key_email = f"{payment.user_id}_subscription_{subscription_id}@veilbot.com"
                    server_config = {
                        'api_url': api_url,
                        'api_key': api_key,
                        'domain': domain,
                    }
                    protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                    user_data = await protocol_client.create_user(key_email, name=server_name)
                    
                    if not user_data or not user_data.get('uuid'):
                        raise Exception("Failed to create user on V2Ray server")
                    
                    v2ray_uuid = user_data['uuid']
                    client_config = await protocol_client.get_user_config(
                        v2ray_uuid,
                        {
                            'domain': domain,
                            'port': 443,
                            'email': key_email,
                        },
                    )
                    
                    if 'vless://' in client_config:
                        lines = client_config.split('\n')
                        for line in lines:
                            if line.strip().startswith('vless://'):
                                client_config = line.strip()
                                break
                    
                    traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
                    async with open_async_connection(self.db_path) as conn:
                        await conn.execute("PRAGMA foreign_keys = OFF")
                        try:
                            cursor = await conn.execute(
                                """
                                INSERT INTO v2ray_keys 
                                (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config, subscription_id, traffic_limit_mb)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    server_id,
                                    payment.user_id,
                                    v2ray_uuid,
                                    key_email,
                                    now,
                                    expires_at,
                                    tariff['id'],
                                    client_config,
                                    subscription_id,
                                    traffic_limit_mb,
                                ),
                            )
                            await conn.commit()
                            
                            async with conn.execute(
                                "SELECT id FROM v2ray_keys WHERE server_id = ? AND user_id = ? AND subscription_id = ? AND v2ray_uuid = ?",
                                (server_id, payment.user_id, subscription_id, v2ray_uuid)
                            ) as check_cursor:
                                if not await check_cursor.fetchone():
                                    raise Exception(f"Key was not saved to database for server {server_id}")
                            
                        finally:
                            await conn.execute("PRAGMA foreign_keys = ON")
                    
                    created_keys += 1
                    logger.info(
                        f"[SUBSCRIPTION] Created key for subscription {subscription_id} on server {server_id} ({server_name})"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"[SUBSCRIPTION] Failed to create key for subscription {subscription_id} "
                        f"on server {server_id} ({server_name}): {e}",
                        exc_info=True,
                    )
                    if v2ray_uuid and protocol_client:
                        try:
                            await protocol_client.delete_user(v2ray_uuid)
                            logger.info(f"[SUBSCRIPTION] Cleaned up orphaned key on server {server_id}")
                        except Exception as cleanup_error:
                            logger.error(f"[SUBSCRIPTION] Failed to cleanup orphaned key: {cleanup_error}")
                    failed_servers.append(server_id)
            
            if created_keys == 0:
                error_msg = f"Failed to create any keys for subscription {subscription_id}"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                await self.subscription_repo.deactivate_subscription_async(subscription_id)
                return False, error_msg
            
            logger.info(
                f"[SUBSCRIPTION] Created subscription {subscription_id} for user {payment.user_id}: "
                f"{created_keys} keys created, {len(failed_servers)} failed"
            )
            
            # Отправляем уведомление о продлении (не о покупке)
            subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_token}"
            msg = (
                f"✅ *Подписка V2Ray успешно продлена!*\n\n"
                f"🔗 *Ссылка подписки:*\n"
                f"`{subscription_url}`\n\n"
                f"⏳ *Добавлено времени:* {format_duration(tariff['duration_sec'])}\n"
                f"📅 *Новый срок действия:* до {datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"💡 Подписка автоматически обновится в вашем приложении"
            )
            
            logger.info(
                f"[SUBSCRIPTION] Sending RENEWAL notification to user {payment.user_id} for subscription {subscription_id}"
            )
            notification_sent = await self._send_notification_simple(payment.user_id, msg)
            
            if not notification_sent:
                logger.warning(
                    f"[SUBSCRIPTION] Failed to send renewal notification for subscription {subscription_id}, "
                    f"user {payment.user_id}. Will retry."
                )
                return False, f"Failed to send renewal notification to user {payment.user_id}"
            
            # Обновляем статус платежа
            try:
                update_success = await self.payment_repo.try_update_status(
                    payment.payment_id,
                    PaymentStatus.COMPLETED,
                    PaymentStatus.PAID
                )
                
                if not update_success:
                    updated_payment = await self.payment_repo.get_by_payment_id(payment.payment_id)
                    if updated_payment and updated_payment.status == PaymentStatus.COMPLETED:
                        logger.info(
                            f"[SUBSCRIPTION] Payment {payment.payment_id} already completed by another process"
                        )
                    else:
                        payment.mark_as_completed()
                        await self.payment_repo.update(payment)
                        logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed via update()")
                else:
                    logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed atomically")
                    
            except Exception as update_error:
                logger.error(
                    f"[SUBSCRIPTION] Failed to update payment {payment.payment_id} status to completed: {update_error}",
                    exc_info=True
                )
            
            logger.info(
                f"[SUBSCRIPTION] Subscription {subscription_id} created as renewal successfully for payment {payment.payment_id}, "
                f"notification_sent={notification_sent}"
            )
            return True, None
            
        except Exception as e:
            error_msg = f"Error creating subscription as renewal: {e}"
            logger.error(f"[SUBSCRIPTION] {error_msg}", exc_info=True)
            try:
                if 'subscription_id' in locals():
                    await self.subscription_repo.deactivate_subscription_async(subscription_id)
            except:
                pass
            return False, error_msg
    
    async def _extend_subscription(
        self, 
        payment: Payment, 
        tariff: Dict[str, Any], 
        existing_subscription: tuple,
        now: int,
        is_purchase: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Продлить существующую подписку или отправить уведомление о покупке
        
        Args:
            payment: Платеж
            tariff: Тариф
            existing_subscription: Существующая подписка (tuple)
            now: Текущее время (timestamp)
            is_purchase: Если True, не продлевать подписку, только отправить уведомление о покупке
        """
        try:
            subscription_id = existing_subscription[0]
            subscription_token = existing_subscription[2]
            existing_expires_at = existing_subscription[4]
            
            if is_purchase:
                # Если это покупка, не продлеваем подписку, только отправляем уведомление
                logger.info(
                    f"[SUBSCRIPTION] Subscription {subscription_id} already exists for purchase, "
                    f"not extending, only sending purchase notification"
                )
                new_expires_at = existing_expires_at  # Используем существующий срок
            else:
                # Продление: увеличиваем срок действия
                # ВАЖНО: Не изменяем срок подписки, если он был установлен вручную через админку
                # Признак ручной установки: expires_at очень далеко в будущем (например, 01.01.2100)
                MANUAL_EXPIRY_THRESHOLD = 4102434000  # 01.01.2100 - признак ручной установки
                ONE_YEAR_IN_SECONDS = 365 * 24 * 3600
                is_manually_set = (
                    existing_expires_at >= MANUAL_EXPIRY_THRESHOLD or
                    (existing_expires_at > now and (existing_expires_at - now) > (5 * ONE_YEAR_IN_SECONDS))
                )
                
                if is_manually_set:
                    # Срок был установлен вручную - не изменяем его при продлении
                    logger.info(
                        f"[SUBSCRIPTION] Subscription {subscription_id} has manually set expiry date: "
                        f"{existing_expires_at}. Not extending, keeping original expiry."
                    )
                    new_expires_at = existing_expires_at
                else:
                    # Обычное продление: увеличиваем срок действия
                    # ВАЖНО: Валидация существующей даты истечения
                    MAX_REASONABLE_EXPIRY = now + (10 * 365 * 24 * 3600)  # 10 лет
                    if existing_expires_at > MAX_REASONABLE_EXPIRY:
                        logger.warning(
                            f"[SUBSCRIPTION] Subscription {subscription_id} has unreasonable expiry date: "
                            f"{existing_expires_at} (more than 10 years in future). "
                            f"Resetting to now + duration."
                        )
                        # Исправляем на разумную дату: используем текущее время + длительность тарифа
                        existing_expires_at = now + tariff['duration_sec']
                    
                    new_expires_at = existing_expires_at + tariff['duration_sec']
                    
                    # Дополнительная валидация новой даты
                    if new_expires_at > MAX_REASONABLE_EXPIRY:
                        logger.error(
                            f"[SUBSCRIPTION] Calculated expiry date is too far in future for subscription {subscription_id}: "
                            f"{new_expires_at}. Using now + duration instead."
                        )
                        new_expires_at = now + tariff['duration_sec']
                
                logger.info(
                    f"[SUBSCRIPTION] Extending subscription {subscription_id} for user {payment.user_id}: "
                    f"{existing_expires_at} -> {new_expires_at} (+{tariff['duration_sec']}s)"
                )
                
                # Шаг 1: Обновляем подписку (срок, tariff_id и лимит трафика)
                # ВАЖНО: Обновляем tariff_id подписки на тариф из платежа
                await self.subscription_repo.extend_subscription_async(subscription_id, new_expires_at, tariff['id'])
                
                # Обновляем лимит трафика подписки из тарифа
                # ВАЖНО: Обновляем всегда, даже если лимит = 0 (безлимит), чтобы корректно применить новый тариф
                traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
                await self.subscription_repo.update_subscription_traffic_limit_async(subscription_id, traffic_limit_mb)
                logger.info(
                    f"[SUBSCRIPTION] Updated subscription {subscription_id} traffic_limit_mb to {traffic_limit_mb} MB"
                )
                
                # Шаг 2: Продлеваем все ключи подписки
                # ВАЖНО: Продлеваем ВСЕ ключи подписки, даже если они истекли
                # Это гарантирует, что при продлении подписки все ключи будут активны
                async with open_async_connection(self.db_path) as conn:
                    # ВАЖНО: expiry_at удалено из таблиц keys и v2ray_keys - срок действия берется из subscriptions
                    # ВАЖНО: traffic_limit_mb не обновляется в ключах - лимит управляется через подписку (subscriptions.traffic_limit_mb)
                    # Подписка уже обновлена выше в коде (new_expires_at и traffic_limit_mb), ключи автоматически используют данные из подписки
                    # Не нужно обновлять ключи - они используют данные из подписки
                    await conn.commit()
                
                logger.info(
                    f"[SUBSCRIPTION] Extended subscription {subscription_id} - keys automatically use updated subscription data"
                )
                
                # Шаг 2.5: Сбрасываем трафик всех ключей подписки при продлении
                try:
                    reset_success = await reset_subscription_traffic(subscription_id)
                    if reset_success:
                        logger.info(f"[SUBSCRIPTION] Successfully reset traffic for subscription {subscription_id}")
                    else:
                        logger.warning(f"[SUBSCRIPTION] Failed to reset traffic for subscription {subscription_id}")
                except Exception as e:
                    logger.error(f"[SUBSCRIPTION] Error resetting traffic for subscription {subscription_id}: {e}", exc_info=True)
            
            # Шаг 3: Проверяем, не было ли уже отправлено уведомление
            # ВАЖНО: purchase_notification_sent проверяем ТОЛЬКО для покупки (is_purchase=True)
            # Для продления (is_purchase=False) уведомление отправляем всегда
            if is_purchase:
                # Для покупки проверяем флаг purchase_notification_sent
                async with open_async_connection(self.db_path) as conn:
                    async with conn.execute(
                        "SELECT purchase_notification_sent FROM subscriptions WHERE id = ?",
                        (subscription_id,)
                    ) as check_cursor:
                        notif_row = await check_cursor.fetchone()
                        if notif_row and notif_row[0]:
                            logger.info(f"[SUBSCRIPTION] Purchase notification already sent for subscription {subscription_id}, skipping")
                            # Уведомление уже отправлено, помечаем платеж как completed
                            try:
                                update_success = await self.payment_repo.try_update_status(
                                    payment.payment_id,
                                    PaymentStatus.COMPLETED,
                                    PaymentStatus.PAID
                                )
                                if not update_success:
                                    payment.mark_as_completed()
                                    await self.payment_repo.update(payment)
                            except Exception as e:
                                logger.error(f"[SUBSCRIPTION] Failed to mark payment {payment.payment_id} as completed: {e}", exc_info=True)
                            return True, None
            
            # Шаг 4: МОМЕНТАЛЬНО отправляем уведомление (о покупке или продлении)
            subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_token}"
            
            if is_purchase:
                # Уведомление о покупке (для случая когда подписка уже существует, но это первая покупка)
                msg = (
                    f"✅ *Подписка успешно создана!*\n\n"
                    f"🔗 *Ссылка (коснитесь, чтобы скопировать):*\n"
                    f"`{subscription_url}`\n\n"
                    f"⏳ *Срок действия:* {format_duration(tariff['duration_sec'])}\n\n"
                    f"💡 *Как использовать:*\n"
                    f"1. Откройте приложение V2Ray\n"
                    f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
                    f"3. Вставьте ссылку выше\n"
                    f"4. Все серверы будут добавлены автоматически"
                )
                notification_type = "PURCHASE"
            else:
                # Уведомление о продлении
                msg = (
                    f"✅ *Подписка успешно продлена!*\n\n"
                    f"🔗 *Ссылка (коснитесь, чтобы скопировать):*\n"
                    f"`{subscription_url}`\n\n"
                    f"⏳ *Добавлено времени:* {format_duration(tariff['duration_sec'])}\n"
                    f"📅 *Новый срок действия:* до {datetime.fromtimestamp(new_expires_at).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"💡 Подписка автоматически обновится в вашем приложении"
                )
                notification_type = "RENEWAL"
            
            logger.info(
                f"[SUBSCRIPTION] Sending {notification_type} notification to user {payment.user_id} for subscription {subscription_id}"
            )
            notification_sent = await self._send_notification_simple(payment.user_id, msg)
            logger.info(
                f"[SUBSCRIPTION] {notification_type} notification send result: {notification_sent} for user {payment.user_id}, subscription {subscription_id}"
            )
            
            # Шаг 5: Если уведомление не отправлено, делаем retry
            if not notification_sent:
                notification_type_name = "purchase" if is_purchase else "renewal"
                logger.warning(
                    f"[SUBSCRIPTION] Failed to send {notification_type_name} notification for subscription {subscription_id}, "
                    f"user {payment.user_id}. Will retry."
                )
                # НЕ помечаем как completed, чтобы повторить попытку
                return False, f"Failed to send {notification_type_name} notification to user {payment.user_id}"
            
            # Шаг 5.5: Если это покупка (is_purchase=True), отправляем информацию о запасных Outline ключах
            if is_purchase:
                try:
                    # Получаем все Outline ключи для этой подписки
                    async with open_async_connection(self.db_path) as conn:
                        async with conn.execute(
                            """
                            SELECT k.access_url, COALESCE(sub.expires_at, 0) as expiry_at, s.country
                            FROM keys k
                            JOIN servers s ON k.server_id = s.id
                            JOIN subscriptions sub ON k.subscription_id = sub.id
                            WHERE k.subscription_id = ? AND k.protocol = 'outline' AND sub.expires_at > ?
                            ORDER BY s.country, k.created_at
                            """,
                            (subscription_id, now)
                        ) as cursor:
                            outline_keys = await cursor.fetchall()
                    
                    if outline_keys:
                        # Формируем сообщение с запасными Outline ключами
                        outline_msg = "🎁 *Также мы подготовили для вас запасные Outline ключи (потребуется скачать другое приложение):*\n\n"
                        
                        for access_url, key_expiry_at, country in outline_keys:
                            remaining_time = key_expiry_at - now
                            country_text = f"🌍 *Страна:* {country}\n" if country else ""
                            outline_msg += (
                                f"{country_text}"
                                f"{format_key_message_unified(access_url, 'outline', tariff, remaining_time)}\n\n"
                            )
                        
                        # Отправляем сообщение с запасными ключами
                        outline_notification_sent = await self._send_notification_simple(payment.user_id, outline_msg)
                        if outline_notification_sent:
                            logger.info(
                                f"[SUBSCRIPTION] Outline backup keys notification sent to user {payment.user_id} for subscription {subscription_id}"
                            )
                        else:
                            logger.warning(
                                f"[SUBSCRIPTION] Failed to send Outline backup keys notification to user {payment.user_id} for subscription {subscription_id}"
                            )
                    else:
                        logger.info(
                            f"[SUBSCRIPTION] No Outline keys found for subscription {subscription_id}, skipping backup keys notification"
                        )
                except Exception as outline_error:
                    logger.error(
                        f"[SUBSCRIPTION] Error sending Outline backup keys notification for subscription {subscription_id}: {outline_error}",
                        exc_info=True
                    )
                    # Не прерываем процесс, так как основное уведомление уже отправлено
            
            # Шаг 6: Уведомление успешно отправлено - помечаем платеж как completed
            # ВАЖНО: mark_purchase_notification_sent вызываем ТОЛЬКО для покупки (is_purchase=True)
            # Для продления этот флаг не используется
            if is_purchase:
                try:
                    await self.subscription_repo.mark_purchase_notification_sent_async(subscription_id)
                except Exception as mark_error:
                    logger.warning(
                        f"[SUBSCRIPTION] Failed to mark purchase notification sent for subscription {subscription_id}: {mark_error}. "
                        f"Continuing with payment status update."
                    )
            
            # Обновляем статус платежа - используем атомарное обновление для надежности
            try:
                # Сначала пытаемся обновить через try_update_status (атомарно)
                update_success = await self.payment_repo.try_update_status(
                    payment.payment_id,
                    PaymentStatus.COMPLETED,
                    PaymentStatus.PAID
                )
                
                if not update_success:
                    # Если атомарное обновление не сработало (статус уже изменился), 
                    # проверяем текущий статус
                    updated_payment = await self.payment_repo.get_by_payment_id(payment.payment_id)
                    if updated_payment and updated_payment.status == PaymentStatus.COMPLETED:
                        logger.info(
                            f"[SUBSCRIPTION] Payment {payment.payment_id} already completed by another process"
                        )
                    else:
                        # Пробуем обновить через обычный update
                        payment.mark_as_completed()
                        await self.payment_repo.update(payment)
                        logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed via update()")
                else:
                    logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed atomically")
                    
            except Exception as update_error:
                logger.error(
                    f"[SUBSCRIPTION] Failed to update payment {payment.payment_id} status to completed: {update_error}",
                    exc_info=True
                )
                # Пытаемся обновить напрямую через SQL как последнюю попытку
                try:
                    async with open_async_connection(self.db_path) as conn:
                        await conn.execute(
                            "UPDATE payments SET status = ?, updated_at = ? WHERE payment_id = ?",
                            (
                                PaymentStatus.COMPLETED.value,
                                int(time.time()),
                                payment.payment_id
                            )
                        )
                        await conn.commit()
                        logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed via direct SQL")
                except Exception as sql_error:
                    logger.error(
                        f"[SUBSCRIPTION] Failed to update payment {payment.payment_id} via direct SQL: {sql_error}",
                        exc_info=True
                    )
                    # Не возвращаем ошибку, так как уведомление уже отправлено
                    # Статус будет обновлен при следующей попытке через retry механизм
            
            logger.info(
                f"[SUBSCRIPTION] Subscription {subscription_id} extended successfully for payment {payment.payment_id}, "
                f"notification_sent={notification_sent}"
            )
            return True, None
            
        except Exception as e:
            error_msg = f"Error extending subscription: {e}"
            logger.error(f"[SUBSCRIPTION] {error_msg}", exc_info=True)
            return False, error_msg
    
    async def _send_purchase_notification_for_existing_subscription(
        self,
        payment: Payment,
        tariff: Dict[str, Any],
        existing_subscription: tuple,
        now: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Отправить уведомление о покупке для существующей подписки
        
        Используется когда подписка уже создана, но уведомление о покупке не было отправлено.
        Это происходит когда подписка была создана другим процессом после оплаты платежа.
        """
        try:
            subscription_id = existing_subscription[0]
            subscription_token = existing_subscription[2]
            
            logger.info(
                f"[SUBSCRIPTION] Sending purchase notification for existing subscription {subscription_id}, "
                f"user {payment.user_id}, payment {payment.payment_id}"
            )
            
            # Шаг 1: Атомарно проверяем и помечаем уведомление как отправляемое
            # Это предотвращает дублирование уведомлений при параллельной обработке
            async with open_async_connection(self.db_path) as conn:
                # Атомарно проверяем и обновляем флаг purchase_notification_sent
                async with conn.execute(
                    """
                    UPDATE subscriptions 
                    SET purchase_notification_sent = 1 
                    WHERE id = ? AND purchase_notification_sent = 0
                    """,
                    (subscription_id,)
                ) as update_cursor:
                    await conn.commit()
                    notification_already_sent = update_cursor.rowcount == 0
                
                if notification_already_sent:
                    # Уведомление уже отправлено другим процессом
                    logger.info(f"[SUBSCRIPTION] Purchase notification already sent for subscription {subscription_id} by another process, skipping")
                    # Помечаем платеж как completed
                    try:
                        update_success = await self.payment_repo.try_update_status(
                            payment.payment_id,
                            PaymentStatus.COMPLETED,
                            PaymentStatus.PAID
                        )
                        if not update_success:
                            payment.mark_as_completed()
                            await self.payment_repo.update(payment)
                    except Exception as e:
                        logger.error(f"[SUBSCRIPTION] Failed to mark payment {payment.payment_id} as completed: {e}", exc_info=True)
                    return True, None
            
            # Шаг 2: Отправляем уведомление о покупке
            # Флаг purchase_notification_sent уже установлен атомарно выше
            subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_token}"
            msg = (
                f"✅ *Подписка успешно создана!*\n\n"
                f"🔗 *Ссылка (коснитесь, чтобы скопировать):*\n"
                f"`{subscription_url}`\n\n"
                f"⏳ *Срок действия:* {format_duration(tariff['duration_sec'])}\n\n"
                f"💡 *Как использовать:*\n"
                f"1. Откройте приложение V2Ray\n"
                f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
                f"3. Вставьте ссылку выше\n"
                f"4. Все серверы будут добавлены автоматически"
            )
            
            logger.info(
                f"[SUBSCRIPTION] Sending PURCHASE notification to user {payment.user_id} for subscription {subscription_id}"
            )
            notification_sent = await self._send_notification_simple(payment.user_id, msg)
            logger.info(
                f"[SUBSCRIPTION] Purchase notification send result: {notification_sent} for user {payment.user_id}, subscription {subscription_id}"
            )
            
            # Шаг 3: Если уведомление не отправлено, делаем retry
            if not notification_sent:
                logger.warning(
                    f"[SUBSCRIPTION] Failed to send purchase notification for subscription {subscription_id}, "
                    f"user {payment.user_id}. Will retry."
                )
                # НЕ помечаем как completed, чтобы повторить попытку
                return False, f"Failed to send purchase notification to user {payment.user_id}"
            
            # Шаг 3.5: Отправляем информацию о запасных Outline ключах
            try:
                # Получаем все Outline ключи для этой подписки
                async with open_async_connection(self.db_path) as conn:
                    async with conn.execute(
                        """
                        SELECT k.access_url, COALESCE(sub.expires_at, 0) as expiry_at, s.country
                        FROM keys k
                        JOIN servers s ON k.server_id = s.id
                        JOIN subscriptions sub ON k.subscription_id = sub.id
                        WHERE k.subscription_id = ? AND k.protocol = 'outline' AND sub.expires_at > ?
                        ORDER BY s.country, k.created_at
                        """,
                        (subscription_id, now)
                    ) as cursor:
                        outline_keys = await cursor.fetchall()
                
                if outline_keys:
                    # Формируем сообщение с запасными Outline ключами
                    outline_msg = "🎁 *Также мы подготовили для вас запасные Outline ключи (потребуется скачать другое приложение):*\n\n"
                    
                    for access_url, key_expiry_at, country in outline_keys:
                        remaining_time = key_expiry_at - now
                        country_text = f"🌍 *Страна:* {country}\n" if country else ""
                        outline_msg += (
                            f"{country_text}"
                            f"{format_key_message_unified(access_url, 'outline', tariff, remaining_time)}\n\n"
                        )
                    
                    # Отправляем сообщение с запасными ключами
                    outline_notification_sent = await self._send_notification_simple(payment.user_id, outline_msg)
                    if outline_notification_sent:
                        logger.info(
                            f"[SUBSCRIPTION] Outline backup keys notification sent to user {payment.user_id} for subscription {subscription_id}"
                        )
                    else:
                        logger.warning(
                            f"[SUBSCRIPTION] Failed to send Outline backup keys notification to user {payment.user_id} for subscription {subscription_id}"
                        )
                else:
                    logger.info(
                        f"[SUBSCRIPTION] No Outline keys found for subscription {subscription_id}, skipping backup keys notification"
                    )
            except Exception as outline_error:
                logger.error(
                    f"[SUBSCRIPTION] Error sending Outline backup keys notification for subscription {subscription_id}: {outline_error}",
                    exc_info=True
                )
                # Не прерываем процесс, так как основное уведомление уже отправлено
            
            # Шаг 4: Уведомление успешно отправлено - помечаем платеж
            # ВАЖНО: Флаг purchase_notification_sent уже установлен атомарно в Шаге 1
            # Здесь только обновляем статус платежа
            
            # Обновляем статус платежа - используем атомарное обновление для надежности
            try:
                # Сначала пытаемся обновить через try_update_status (атомарно)
                update_success = await self.payment_repo.try_update_status(
                    payment.payment_id,
                    PaymentStatus.COMPLETED,
                    PaymentStatus.PAID
                )
                
                if not update_success:
                    # Если атомарное обновление не сработало (статус уже изменился), 
                    # проверяем текущий статус
                    updated_payment = await self.payment_repo.get_by_payment_id(payment.payment_id)
                    if updated_payment and updated_payment.status == PaymentStatus.COMPLETED:
                        logger.info(
                            f"[SUBSCRIPTION] Payment {payment.payment_id} already completed by another process"
                        )
                    else:
                        # Пробуем обновить через обычный update
                        payment.mark_as_completed()
                        await self.payment_repo.update(payment)
                        logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed via update()")
                else:
                    logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed atomically")
                    
            except Exception as update_error:
                logger.error(
                    f"[SUBSCRIPTION] Failed to update payment {payment.payment_id} status to completed: {update_error}",
                    exc_info=True
                )
                # Пытаемся обновить напрямую через SQL как последнюю попытку
                try:
                    async with open_async_connection(self.db_path) as conn:
                        await conn.execute(
                            "UPDATE payments SET status = ?, updated_at = ? WHERE payment_id = ?",
                            (
                                PaymentStatus.COMPLETED.value,
                                int(time.time()),
                                payment.payment_id
                            )
                        )
                        await conn.commit()
                        logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed via direct SQL")
                except Exception as sql_error:
                    logger.error(
                        f"[SUBSCRIPTION] Failed to update payment {payment.payment_id} via direct SQL: {sql_error}",
                        exc_info=True
                    )
                    # Не возвращаем ошибку, так как уведомление уже отправлено
                    # Статус будет обновлен при следующей попытке через retry механизм
            
            logger.info(
                f"[SUBSCRIPTION] Purchase notification sent successfully for subscription {subscription_id}, "
                f"payment {payment.payment_id}, user {payment.user_id}"
            )
            return True, None
            
        except Exception as e:
            error_msg = f"Error sending purchase notification for existing subscription: {e}"
            logger.error(f"[SUBSCRIPTION] {error_msg}", exc_info=True)
            return False, error_msg
    
    async def _create_subscription(
        self, 
        payment: Payment, 
        tariff: Dict[str, Any], 
        now: int
    ) -> Tuple[bool, Optional[str]]:
        """Создать новую подписку"""
        try:
            # Проверяем, не была ли подписка уже создана другим процессом
            # Используем grace_period для определения активной подписки
            from ..utils.renewal_detector import DEFAULT_GRACE_PERIOD
            
            grace_threshold = now - DEFAULT_GRACE_PERIOD
            
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                    FROM subscriptions
                    WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (payment.user_id, grace_threshold)
                ) as cursor:
                    existing_subscription_row = await cursor.fetchone()
            
            if existing_subscription_row:
                # Подписка уже существует - возможно, создана другим процессом
                subscription_id = existing_subscription_row[0]
                existing_created_at = existing_subscription_row[3]
                existing_expires_at = existing_subscription_row[4]
                
                # ВАЖНО: Проверяем, не была ли подписка создана очень недавно (менее 5 минут)
                # и соответствует ли срок действия подписки ожидаемому (created_at + duration)
                # Если срок действия = created_at + duration, значит это только что созданная подписка
                # и её не нужно продлевать, а нужно просто отправить уведомление о покупке
                VERY_RECENT_THRESHOLD = 300  # 5 минут
                subscription_age = now - existing_created_at if existing_created_at else 0
                expected_expires_at = existing_created_at + tariff['duration_sec']
                is_very_recent = subscription_age < VERY_RECENT_THRESHOLD
                expires_at_matches_expected = abs(existing_expires_at - expected_expires_at) < 3600  # Разница менее часа
                
                logger.warning(
                    f"[SUBSCRIPTION] Subscription {subscription_id} already exists for user {payment.user_id}. "
                    f"Age: {subscription_age}s, expires_at={existing_expires_at}, expected={expected_expires_at}. "
                    f"This might be a duplicate. Checking if notification was sent..."
                )
                
                # Проверяем, было ли отправлено уведомление
                async with open_async_connection(self.db_path) as conn:
                    async with conn.execute(
                        "SELECT purchase_notification_sent FROM subscriptions WHERE id = ?",
                        (subscription_id,)
                    ) as check_cursor:
                        notif_row = await check_cursor.fetchone()
                        purchase_notification_sent = notif_row[0] if notif_row else 0
                
                # Если подписка создана очень недавно и срок соответствует ожидаемому - это покупка, не продление
                if is_very_recent and expires_at_matches_expected and not purchase_notification_sent:
                    logger.info(
                        f"[SUBSCRIPTION] Subscription {subscription_id} was just created ({subscription_age}s ago), "
                        f"expires_at matches expected. This is a PURCHASE, not a renewal. Sending purchase notification."
                    )
                    existing_subscription = existing_subscription_row
                    return await self._send_purchase_notification_for_existing_subscription(
                        payment, tariff, existing_subscription, now
                    )
                
                if purchase_notification_sent:
                            # Уведомление уже отправлено - это дублирование, просто помечаем платеж как completed
                            logger.info(f"[SUBSCRIPTION] Notification already sent for subscription {subscription_id}, marking payment as completed")
                            try:
                                update_success = await self.payment_repo.try_update_status(
                                    payment.payment_id,
                                    PaymentStatus.COMPLETED,
                                    PaymentStatus.PAID
                                )
                                if not update_success:
                                    payment.mark_as_completed()
                                    await self.payment_repo.update(payment)
                            except Exception as e:
                                logger.error(f"[SUBSCRIPTION] Failed to mark payment {payment.payment_id} as completed: {e}", exc_info=True)
                            return True, None
                
                # Если уведомление не отправлено - отправляем уведомление о покупке для существующей подписки
                logger.info(
                    f"[SUBSCRIPTION] Subscription {subscription_id} exists but notification not sent. Sending purchase notification."
                )
                existing_subscription = existing_subscription_row
                return await self._send_purchase_notification_for_existing_subscription(
                    payment, tariff, existing_subscription, now
                )
            
            # ВАЛИДАЦИЯ: Проверяем, что duration_sec не None и не 0
            duration_sec = tariff.get('duration_sec', 0) or 0
            if duration_sec is None or duration_sec <= 0:
                error_msg = f"Invalid tariff duration_sec for user {payment.user_id}, tariff_id={tariff.get('id')}: duration_sec={duration_sec}"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            # Проверяем VIP статус пользователя
            from app.repositories.user_repository import UserRepository
            user_repo = UserRepository(self.db_path)
            is_vip = user_repo.is_user_vip(payment.user_id)
            
            # Константы для VIP подписок
            VIP_EXPIRES_AT = 4102434000  # 01.01.2100 00:00 UTC
            VIP_TRAFFIC_LIMIT_MB = 0  # 0 = безлимит
            
            if is_vip:
                expires_at = VIP_EXPIRES_AT
                traffic_limit_mb = VIP_TRAFFIC_LIMIT_MB
                logger.info(f"[SUBSCRIPTION] Creating VIP subscription for user {payment.user_id}, expires_at={expires_at}")
            else:
                expires_at = now + duration_sec
                traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
            
            # Шаг 1: Генерируем уникальный токен
            subscription_token = None
            for _ in range(10):
                token = str(uuid.uuid4())
                if not await self.subscription_repo.get_subscription_by_token_async(token):
                    subscription_token = token
                    break
            
            if not subscription_token:
                error_msg = "Failed to generate unique subscription token after 10 attempts"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            # Шаг 2: Атомарно создаем подписку в БД с защитой от race condition
            # ВАЖНО: Используем транзакцию и проверяем наличие подписки непосредственно перед вставкой
            subscription_id = None
            async with open_async_connection(self.db_path) as conn:
                # Начинаем транзакцию
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    # Финальная проверка перед созданием (защита от race condition)
                    async with conn.execute(
                        """
                        SELECT id FROM subscriptions
                        WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                        LIMIT 1
                        """,
                        (payment.user_id, grace_threshold)
                    ) as check_cursor:
                        existing = await check_cursor.fetchone()
                    
                    if existing:
                        # Подписка уже создана другим процессом
                        subscription_id = existing[0]
                        await conn.commit()
                        logger.info(
                            f"[SUBSCRIPTION] Subscription {subscription_id} already exists (race condition detected), "
                            f"using existing subscription for user {payment.user_id}"
                        )
                    else:
                        # Создаем новую подписку
                        # traffic_limit_mb уже установлен выше (из тарифа или VIP)
                        cursor = await conn.execute(
                            """
                            INSERT INTO subscriptions (user_id, subscription_token, created_at, expires_at, tariff_id, is_active, notified, traffic_limit_mb)
                            VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                            """,
                            (payment.user_id, subscription_token, now, expires_at, tariff['id'], traffic_limit_mb),
                        )
                        subscription_id = cursor.lastrowid
                        await conn.commit()
                        logger.info(
                            f"[SUBSCRIPTION] Created subscription {subscription_id} for user {payment.user_id}, "
                            f"expires_at={expires_at}"
                        )
                except Exception as e:
                    await conn.rollback()
                    raise e
            
            if not subscription_id:
                error_msg = "Failed to create or get subscription"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            # Шаг 3: Создаем ключи на всех активных серверах
            # ВАЖНО: Для V2Ray создаем ключи на всех активных серверах
            # Для Outline создаем только один ключ (приоритет серверу с id=8)
            
            # Сначала получаем все V2Ray серверы
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256
                    FROM servers
                    WHERE active = 1 AND protocol = 'v2ray'
                    ORDER BY id
                    """
                ) as cursor:
                    v2ray_servers = await cursor.fetchall()
            
            # Получаем Outline сервер (приоритет серверу с id=8)
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256
                    FROM servers
                    WHERE active = 1 AND protocol = 'outline'
                    ORDER BY CASE WHEN id = 8 THEN 0 ELSE 1 END, id
                    LIMIT 1
                    """
                ) as cursor:
                    outline_server_row = await cursor.fetchone()
            
            created_keys = 0
            failed_servers = []
            
            # Создаем ключи на всех V2Ray серверах
            for server_id, server_name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256 in v2ray_servers:
                v2ray_uuid = None
                outline_key_id = None
                protocol_client = None
                try:
                    # Генерация email для ключа
                    key_email = f"{payment.user_id}_subscription_{subscription_id}@veilbot.com"
                    
                    # Создание ключа в зависимости от протокола
                    if protocol == 'v2ray':
                        server_config = {
                            'api_url': api_url,
                            'api_key': api_key,
                            'domain': domain,
                        }
                        protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                        user_data = await protocol_client.create_user(key_email, name=server_name)
                        
                        if not user_data or not user_data.get('uuid'):
                            raise Exception("Failed to create user on V2Ray server")
                        
                        v2ray_uuid = user_data['uuid']
                        
                        # Получение client_config
                        client_config = await protocol_client.get_user_config(
                            v2ray_uuid,
                            {
                                'domain': domain,
                                'port': 443,
                                'email': key_email,
                            },
                        )
                        
                        # Извлекаем VLESS URL из конфигурации
                        if 'vless://' in client_config:
                            lines = client_config.split('\n')
                            for line in lines:
                                if line.strip().startswith('vless://'):
                                    client_config = line.strip()
                                    break
                        
                        # Сохранение V2Ray ключа в БД
                        traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
                        async with open_async_connection(self.db_path) as conn:
                            await conn.execute("PRAGMA foreign_keys = OFF")
                            try:
                                # ВАЖНО: expiry_at удалено из v2ray_keys - срок действия берется из subscriptions
                                # ВАЖНО: traffic_limit_mb не устанавливается - лимит берется из подписки
                                cursor = await conn.execute(
                                    """
                                    INSERT INTO v2ray_keys 
                                    (server_id, user_id, v2ray_uuid, email, created_at, tariff_id, client_config, subscription_id)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        server_id,
                                        payment.user_id,
                                        v2ray_uuid,
                                        key_email,
                                        now,
                                        tariff['id'],
                                        client_config,
                                        subscription_id,
                                    ),
                                )
                                await conn.commit()
                                
                                # Проверяем, что ключ действительно сохранен
                                async with conn.execute(
                                    "SELECT id FROM v2ray_keys WHERE server_id = ? AND user_id = ? AND subscription_id = ? AND v2ray_uuid = ?",
                                    (server_id, payment.user_id, subscription_id, v2ray_uuid)
                                ) as check_cursor:
                                    if not await check_cursor.fetchone():
                                        raise Exception(f"Key was not saved to database for server {server_id}")
                                
                            finally:
                                await conn.execute("PRAGMA foreign_keys = ON")
                    
                    created_keys += 1
                    logger.info(
                        f"[SUBSCRIPTION] Created v2ray key for subscription {subscription_id} on server {server_id} ({server_name})"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"[SUBSCRIPTION] Failed to create v2ray key for subscription {subscription_id} "
                        f"on server {server_id} ({server_name}): {e}",
                        exc_info=True,
                    )
                    # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить его с сервера
                    if protocol_client and v2ray_uuid:
                        try:
                            await protocol_client.delete_user(v2ray_uuid)
                            logger.info(f"[SUBSCRIPTION] Cleaned up orphaned v2ray key on server {server_id}")
                        except Exception as cleanup_error:
                            logger.error(f"[SUBSCRIPTION] Failed to cleanup orphaned v2ray key: {cleanup_error}")
                    failed_servers.append(server_id)
            
            # Создаем один Outline ключ (если есть доступный Outline сервер)
            if outline_server_row:
                server_id, server_name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256 = outline_server_row
                outline_key_id = None
                protocol_client = None
                try:
                    # Генерация email для ключа
                    key_email = f"{payment.user_id}_subscription_{subscription_id}@veilbot.com"
                    
                    server_config = {
                        'api_url': api_url,
                        'cert_sha256': cert_sha256,
                    }
                    protocol_client = ProtocolFactory.create_protocol('outline', server_config)
                    user_data = await protocol_client.create_user(key_email)
                    
                    if not user_data or not user_data.get('id'):
                        raise Exception("Failed to create user on Outline server")
                    
                    outline_key_id = user_data['id']
                    access_url = user_data['accessUrl']
                    
                    # Сохранение Outline ключа в БД
                    traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
                    async with open_async_connection(self.db_path) as conn:
                        await conn.execute("PRAGMA foreign_keys = OFF")
                        try:
                            cursor = await conn.execute(
                                # ВАЖНО: expiry_at удалено из keys - срок действия берется из subscriptions
                                # ВАЖНО: traffic_limit_mb не устанавливается для ключей с subscription_id - лимит берется из подписки
                                """
                                INSERT INTO keys 
                                (server_id, user_id, access_url, traffic_limit_mb, notified, key_id, created_at, email, tariff_id, protocol, subscription_id)
                                VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    server_id,
                                    payment.user_id,
                                    access_url,
                                    outline_key_id,
                                    now,
                                    key_email,
                                    tariff['id'],
                                    'outline',
                                    subscription_id,
                                ),
                            )
                            await conn.commit()
                            
                            # Проверяем, что ключ действительно сохранен
                            async with conn.execute(
                                "SELECT id FROM keys WHERE server_id = ? AND user_id = ? AND subscription_id = ? AND key_id = ?",
                                (server_id, payment.user_id, subscription_id, outline_key_id)
                            ) as check_cursor:
                                if not await check_cursor.fetchone():
                                    raise Exception(f"Key was not saved to database for server {server_id}")
                            
                        finally:
                            await conn.execute("PRAGMA foreign_keys = ON")
                    
                    created_keys += 1
                    logger.info(
                        f"[SUBSCRIPTION] Created outline key for subscription {subscription_id} on server {server_id} ({server_name})"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"[SUBSCRIPTION] Failed to create outline key for subscription {subscription_id} "
                        f"on server {server_id} ({server_name}): {e}",
                        exc_info=True,
                    )
                    # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить его с сервера
                    if protocol_client and outline_key_id:
                        try:
                            await protocol_client.delete_user(outline_key_id)
                            logger.info(f"[SUBSCRIPTION] Cleaned up orphaned outline key on server {server_id}")
                        except Exception as cleanup_error:
                            logger.error(f"[SUBSCRIPTION] Failed to cleanup orphaned outline key: {cleanup_error}")
                    failed_servers.append(server_id)
            else:
                logger.info(
                    f"[SUBSCRIPTION] No active outline server found for subscription {subscription_id}, skipping outline key creation"
                )
            
            if created_keys == 0:
                error_msg = f"Failed to create any keys for subscription {subscription_id}"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                # Удаляем подписку, если не удалось создать ни одного ключа
                await self.subscription_repo.deactivate_subscription_async(subscription_id)
                return False, error_msg
            
            logger.info(
                f"[SUBSCRIPTION] Created subscription {subscription_id} for user {payment.user_id}: "
                f"{created_keys} keys created, {len(failed_servers)} failed"
            )
            
            # Шаг 3.5: Сбрасываем трафик всех ключей подписки при создании
            try:
                reset_success = await reset_subscription_traffic(subscription_id)
                if reset_success:
                    logger.info(f"[SUBSCRIPTION] Successfully reset traffic for new subscription {subscription_id}")
                else:
                    logger.warning(f"[SUBSCRIPTION] Failed to reset traffic for new subscription {subscription_id}")
            except Exception as e:
                logger.error(f"[SUBSCRIPTION] Error resetting traffic for new subscription {subscription_id}: {e}", exc_info=True)
            
            # Шаг 4: Атомарно проверяем и помечаем уведомление как отправляемое
            # Это предотвращает дублирование уведомлений при параллельной обработке
            async with open_async_connection(self.db_path) as conn:
                # Атомарно проверяем и обновляем флаг purchase_notification_sent
                async with conn.execute(
                    """
                    UPDATE subscriptions 
                    SET purchase_notification_sent = 1 
                    WHERE id = ? AND purchase_notification_sent = 0
                    """,
                    (subscription_id,)
                ) as update_cursor:
                    await conn.commit()
                    notification_already_sent = update_cursor.rowcount == 0
                
                if notification_already_sent:
                    # Уведомление уже отправлено другим процессом
                    logger.info(f"[SUBSCRIPTION] Purchase notification already sent for subscription {subscription_id} by another process, skipping")
                    # Помечаем платеж как completed
                    try:
                        update_success = await self.payment_repo.try_update_status(
                            payment.payment_id,
                            PaymentStatus.COMPLETED,
                            PaymentStatus.PAID
                        )
                        if not update_success:
                            payment.mark_as_completed()
                            await self.payment_repo.update(payment)
                    except Exception as e:
                        logger.error(f"[SUBSCRIPTION] Failed to mark payment {payment.payment_id} as completed: {e}", exc_info=True)
                    return True, None
            
            # Шаг 5: МОМЕНТАЛЬНО отправляем уведомление о покупке (как в ключах)
            # Флаг purchase_notification_sent уже установлен атомарно выше
            subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_token}"
            msg = (
                f"✅ *Подписка V2Ray успешно создана!*\n\n"
                f"🔗 *Ссылка подписки:*\n"
                f"`{subscription_url}`\n\n"
                f"⏳ *Срок действия:* {format_duration(tariff['duration_sec'])}\n\n"
                f"💡 *Как использовать:*\n"
                f"1. Откройте приложение V2Ray\n"
                f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
                f"3. Вставьте ссылку выше\n"
                f"4. Все серверы будут добавлены автоматически"
            )
            
            logger.info(
                f"[SUBSCRIPTION] Sending PURCHASE notification to user {payment.user_id} for subscription {subscription_id}"
            )
            notification_sent = await self._send_notification_simple(payment.user_id, msg)
            logger.info(
                f"[SUBSCRIPTION] Purchase notification send result: {notification_sent} for user {payment.user_id}, subscription {subscription_id}"
            )
            
            # Шаг 6: Если уведомление не отправлено, делаем retry
            if not notification_sent:
                logger.warning(
                    f"[SUBSCRIPTION] Failed to send purchase notification for subscription {subscription_id}, "
                    f"user {payment.user_id}. Will retry."
                )
                # НЕ помечаем как completed, чтобы повторить попытку
                return False, f"Failed to send notification to user {payment.user_id}"
            
            # Шаг 6.5: Отправляем информацию о запасных Outline ключах
            try:
                # Получаем все Outline ключи для этой подписки
                async with open_async_connection(self.db_path) as conn:
                    async with conn.execute(
                        """
                        SELECT k.access_url, COALESCE(sub.expires_at, 0) as expiry_at, s.country
                        FROM keys k
                        JOIN servers s ON k.server_id = s.id
                        JOIN subscriptions sub ON k.subscription_id = sub.id
                        WHERE k.subscription_id = ? AND k.protocol = 'outline' AND sub.expires_at > ?
                        ORDER BY s.country, k.created_at
                        """,
                        (subscription_id, now)
                    ) as cursor:
                        outline_keys = await cursor.fetchall()
                
                if outline_keys:
                    # Формируем сообщение с запасными Outline ключами
                    outline_msg = "🎁 *Также мы подготовили для вас запасные Outline ключи (потребуется скачать другое приложение):*\n\n"
                    
                    for access_url, key_expiry_at, country in outline_keys:
                        remaining_time = key_expiry_at - now
                        country_text = f"🌍 *Страна:* {country}\n" if country else ""
                        outline_msg += (
                            f"{country_text}"
                            f"{format_key_message_unified(access_url, 'outline', tariff, remaining_time)}\n\n"
                        )
                    
                    # Отправляем сообщение с запасными ключами
                    outline_notification_sent = await self._send_notification_simple(payment.user_id, outline_msg)
                    if outline_notification_sent:
                        logger.info(
                            f"[SUBSCRIPTION] Outline backup keys notification sent to user {payment.user_id} for subscription {subscription_id}"
                        )
                    else:
                        logger.warning(
                            f"[SUBSCRIPTION] Failed to send Outline backup keys notification to user {payment.user_id} for subscription {subscription_id}"
                        )
                else:
                    logger.info(
                        f"[SUBSCRIPTION] No Outline keys found for subscription {subscription_id}, skipping backup keys notification"
                    )
            except Exception as outline_error:
                logger.error(
                    f"[SUBSCRIPTION] Error sending Outline backup keys notification for subscription {subscription_id}: {outline_error}",
                    exc_info=True
                )
                # Не прерываем процесс, так как основное уведомление уже отправлено
            
            # Шаг 7: Уведомление успешно отправлено - помечаем платеж как completed
            # ВАЖНО: Флаг purchase_notification_sent уже установлен атомарно в Шаге 4
            # Здесь только обновляем статус платежа
            
            # Обновляем статус платежа - используем атомарное обновление для надежности
            try:
                # Сначала пытаемся обновить через try_update_status (атомарно)
                update_success = await self.payment_repo.try_update_status(
                    payment.payment_id,
                    PaymentStatus.COMPLETED,
                    PaymentStatus.PAID
                )
                
                if not update_success:
                    # Если атомарное обновление не сработало (статус уже изменился), 
                    # проверяем текущий статус
                    updated_payment = await self.payment_repo.get_by_payment_id(payment.payment_id)
                    if updated_payment and updated_payment.status == PaymentStatus.COMPLETED:
                        logger.info(
                            f"[SUBSCRIPTION] Payment {payment.payment_id} already completed by another process"
                        )
                    else:
                        # Пробуем обновить через обычный update
                        payment.mark_as_completed()
                        await self.payment_repo.update(payment)
                        logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed via update()")
                else:
                    logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed atomically")
                    
            except Exception as update_error:
                logger.error(
                    f"[SUBSCRIPTION] Failed to update payment {payment.payment_id} status to completed: {update_error}",
                    exc_info=True
                )
                # Пытаемся обновить напрямую через SQL как последнюю попытку
                try:
                    async with open_async_connection(self.db_path) as conn:
                        await conn.execute(
                            "UPDATE payments SET status = ?, updated_at = ? WHERE payment_id = ?",
                            (
                                PaymentStatus.COMPLETED.value,
                                int(time.time()),
                                payment.payment_id
                            )
                        )
                        await conn.commit()
                        logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed via direct SQL")
                except Exception as sql_error:
                    logger.error(
                        f"[SUBSCRIPTION] Failed to update payment {payment.payment_id} via direct SQL: {sql_error}",
                        exc_info=True
                    )
                    # Не возвращаем ошибку, так как уведомление уже отправлено
                    # Статус будет обновлен при следующей попытке через retry механизм
            
            logger.info(
                f"[SUBSCRIPTION] Subscription purchase completed successfully: payment={payment.payment_id}, "
                f"subscription={subscription_id}, keys={created_keys}, notification_sent={notification_sent}"
            )
            
            return True, None
            
        except Exception as e:
            error_msg = f"Error creating subscription: {e}"
            logger.error(f"[SUBSCRIPTION] {error_msg}", exc_info=True)
            # Удаляем подписку при ошибке, если она была создана
            try:
                if 'subscription_id' in locals():
                    await self.subscription_repo.deactivate_subscription_async(subscription_id)
            except:
                pass
            return False, error_msg
    
    async def _send_notification_simple(self, user_id: int, message: str) -> bool:
        """
        Отправить уведомление пользователю - использует safe_send_message с встроенным retry
        
        УНИФИЦИРОВАНО: Теперь использует только safe_send_message, который имеет встроенный retry механизм (3 попытки)
        Это устраняет дублирование retry логики
        """
        try:
            bot = get_bot_instance()
            if not bot:
                logger.warning(f"[SUBSCRIPTION] Bot instance is None for user {user_id}")
                return False
            
            # safe_send_message имеет встроенный retry механизм (до 3 попыток)
            result = await safe_send_message(
                bot,
                user_id,
                message,
                reply_markup=get_main_menu(user_id),
                disable_web_page_preview=True,
                parse_mode="Markdown"
            )
            
            if result:
                logger.info(f"[SUBSCRIPTION] Notification sent to user {user_id}")
                return True
            else:
                logger.warning(f"[SUBSCRIPTION] Failed to send notification to user {user_id} after retries")
                return False
                
        except Exception as e:
            logger.error(f"[SUBSCRIPTION] Error sending notification to user {user_id}: {e}", exc_info=True)
            return False
