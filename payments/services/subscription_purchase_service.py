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
from bot.utils import safe_send_message
from bot.keyboards import get_main_menu

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
            # Проверяем наличие активной подписки с учетом grace_period
            # ВАЖНО: Если подписка была создана недавно (в течение последних 30 минут) и 
            # уведомление о покупке еще не отправлено, это покупка (возможно retry), а не продление
            from ..utils.renewal_detector import DEFAULT_GRACE_PERIOD
            
            now = int(time.time())
            grace_threshold = now - DEFAULT_GRACE_PERIOD  # 24 часа назад
            RECENT_SUBSCRIPTION_THRESHOLD = 1800  # 30 минут - если подписка создана недавно, это может быть покупка
            
            # Проверяем наличие активной подписки (с учетом grace_period)
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
                subscription_id = existing_subscription_row[0]
                subscription_token = existing_subscription_row[2]
                existing_expires_at = existing_subscription_row[4]
                created_at = existing_subscription_row[3]
                purchase_notification_sent = existing_subscription_row[9] if len(existing_subscription_row) > 9 else 0
                
                # Проверяем, была ли подписка создана недавно и уведомление о покупке не отправлено
                # Если да, то это покупка (возможно retry обработки того же платежа), а не продление
                subscription_age = now - created_at if created_at else 0
                is_recent_subscription = subscription_age < RECENT_SUBSCRIPTION_THRESHOLD
                purchase_notification_not_sent = not purchase_notification_sent
                
                if is_recent_subscription and purchase_notification_not_sent:
                    # Подписка создана недавно и уведомление о покупке не отправлено - это покупка
                    logger.info(
                        f"[SUBSCRIPTION] User {payment.user_id} has recent subscription {subscription_id} "
                        f"(created {subscription_age}s ago, purchase_notification_sent={purchase_notification_sent}). "
                        f"This is a PURCHASE (likely retry of same payment)."
                    )
                    
                    # Используем существующую подписку, но отправляем уведомление о покупке
                    existing_subscription = existing_subscription_row
                    return await self._send_purchase_notification_for_existing_subscription(
                        payment, tariff, existing_subscription, now
                    )
                
                # Есть активная подписка, которая не была создана недавно или уведомление уже отправлено - это ПРОДЛЕНИЕ
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
                new_expires_at = existing_expires_at + tariff['duration_sec']
                
                logger.info(
                    f"[SUBSCRIPTION] Extending subscription {subscription_id} for user {payment.user_id}: "
                    f"{existing_expires_at} -> {new_expires_at} (+{tariff['duration_sec']}s)"
                )
                
                # Шаг 1: Обновляем подписку
                await self.subscription_repo.extend_subscription_async(subscription_id, new_expires_at)
                
                # Шаг 2: Продлеваем все ключи подписки
                # ВАЖНО: Продлеваем ВСЕ ключи подписки, даже если они истекли
                # Это гарантирует, что при продлении подписки все ключи будут активны
                async with open_async_connection(self.db_path) as conn:
                    cursor = await conn.execute(
                        """
                        UPDATE v2ray_keys 
                        SET expiry_at = ? 
                        WHERE subscription_id = ?
                        """,
                        (new_expires_at, subscription_id)
                    )
                    keys_extended = cursor.rowcount
                    await conn.commit()
                
                logger.info(f"[SUBSCRIPTION] Extended {keys_extended} keys for subscription {subscription_id}")
            
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
                notification_type = "PURCHASE"
            else:
                # Уведомление о продлении
                msg = (
                    f"✅ *Подписка V2Ray успешно продлена!*\n\n"
                    f"🔗 *Ссылка подписки:*\n"
                    f"`{subscription_url}`\n\n"
                    f"⏳ *Добавлено времени:* {format_duration(tariff['duration_sec'])}\n"
                    f"📅 *Новый срок действия:* до <code>{datetime.fromtimestamp(new_expires_at).strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
                    f"💡 Подписка автоматически обновится в вашем приложении V2Ray"
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
            
            # Шаг 1: Проверяем, не было ли уже отправлено уведомление
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
            
            # Шаг 2: Отправляем уведомление о покупке
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
            
            # Шаг 3: Если уведомление не отправлено, делаем retry
            if not notification_sent:
                logger.warning(
                    f"[SUBSCRIPTION] Failed to send purchase notification for subscription {subscription_id}, "
                    f"user {payment.user_id}. Will retry."
                )
                # НЕ помечаем как completed, чтобы повторить попытку
                return False, f"Failed to send purchase notification to user {payment.user_id}"
            
            # Шаг 4: Уведомление успешно отправлено - помечаем подписку и платеж
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
                logger.warning(
                    f"[SUBSCRIPTION] Subscription {subscription_id} already exists for user {payment.user_id}. "
                    f"This might be a duplicate. Checking if notification was sent..."
                )
                
                # Проверяем, было ли отправлено уведомление
                async with open_async_connection(self.db_path) as conn:
                    async with conn.execute(
                        "SELECT purchase_notification_sent FROM subscriptions WHERE id = ?",
                        (subscription_id,)
                    ) as check_cursor:
                        notif_row = await check_cursor.fetchone()
                        if notif_row and notif_row[0]:
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
            
            expires_at = now + tariff['duration_sec']
            
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
            
            # Шаг 2: Создаем подписку в БД
            subscription_id = await self.subscription_repo.create_subscription_async(
                user_id=payment.user_id,
                subscription_token=subscription_token,
                expires_at=expires_at,
                tariff_id=tariff['id'],
            )
            
            logger.info(
                f"[SUBSCRIPTION] Created subscription {subscription_id} for user {payment.user_id}, "
                f"expires_at={expires_at}"
            )
            
            # Шаг 3: Создаем ключи на всех активных V2Ray серверах
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
                    # Генерация email для ключа
                    key_email = f"{payment.user_id}_subscription_{subscription_id}@veilbot.com"
                    
                    # Создание ключа через V2Ray API
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
                    
                    # Сохранение ключа в БД
                    async with open_async_connection(self.db_path) as conn:
                        await conn.execute("PRAGMA foreign_keys = OFF")
                        try:
                            cursor = await conn.execute(
                                """
                                INSERT INTO v2ray_keys 
                                (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id, client_config, subscription_id)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        f"[SUBSCRIPTION] Created key for subscription {subscription_id} on server {server_id} ({server_name})"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"[SUBSCRIPTION] Failed to create key for subscription {subscription_id} "
                        f"on server {server_id} ({server_name}): {e}",
                        exc_info=True,
                    )
                    # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить его с сервера
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
                # Удаляем подписку, если не удалось создать ни одного ключа
                await self.subscription_repo.deactivate_subscription_async(subscription_id)
                return False, error_msg
            
            logger.info(
                f"[SUBSCRIPTION] Created subscription {subscription_id} for user {payment.user_id}: "
                f"{created_keys} keys created, {len(failed_servers)} failed"
            )
            
            # Шаг 4: Проверяем, не было ли уже отправлено уведомление
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
            
            # Шаг 5: МОМЕНТАЛЬНО отправляем уведомление о покупке (как в ключах)
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
            
            # Шаг 7: Уведомление успешно отправлено - помечаем платеж как completed
            # ВАЖНО: Обновляем статус даже если mark_purchase_notification_sent_async выбросит исключение
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
