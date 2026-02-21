"""
Единый сервис для обработки покупки подписки V2Ray
Переписано с нуля по аналогии с ключами - максимально просто и надежно
"""
import uuid
import time
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime

from ..models.payment import Payment, PaymentStatus
from ..repositories.payment_repository import PaymentRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.tariff_repository import TariffRepository
from app.repositories.user_repository import UserRepository
from app.infra.sqlite_utils import open_async_connection, open_connection
from app.settings import settings as app_settings
from vpn_protocols import ProtocolFactory, format_duration
from bot.core import get_bot_instance
from bot.utils import safe_send_message, format_key_message_unified
from bot.keyboards import get_main_menu
from bot.services.subscription_traffic_reset import reset_subscription_traffic

logger = logging.getLogger(__name__)


class ServerClientPool:
    """Упрощенный пул клиентов для переиспользования соединений к серверам"""
    
    def __init__(self):
        self._clients: Dict[int, Any] = {}
    
    async def get_client(self, server_id: int, protocol: str, api_url: str, api_key: Optional[str] = None, 
                        domain: Optional[str] = None, cert_sha256: Optional[str] = None) -> Optional[Any]:
        """Получить или создать клиент для сервера"""
        if server_id not in self._clients:
            try:
                if protocol == "v2ray":
                    if not api_url or not api_key:
                        return None
                    self._clients[server_id] = ProtocolFactory.create_protocol("v2ray", {
                        "api_url": api_url,
                        "api_key": api_key,
                        "domain": domain,
                    })
                elif protocol == "outline":
                    if not api_url:
                        return None
                    self._clients[server_id] = ProtocolFactory.create_protocol("outline", {
                        "api_url": api_url,
                        "cert_sha256": cert_sha256 or "",
                    })
                else:
                    return None
            except Exception as e:
                logger.warning(f"[SUBSCRIPTION] Не удалось создать клиент для сервера #{server_id}: {e}")
                return None
        return self._clients.get(server_id)
    
    async def close_all(self):
        """Закрыть все клиенты"""
        for server_id, client in self._clients.items():
            try:
                if hasattr(client, 'close'):
                    await client.close()
            except Exception as e:
                logger.warning(f"[SUBSCRIPTION] Ошибка закрытия клиента для сервера #{server_id}: {e}")
        self._clients.clear()


# Главное меню в формате Telegram Bot API (для reply_markup при отправке через API)
_MAIN_MENU_REPLY_MARKUP = {
    "keyboard": [
        [{"text": "Получить доступ"}],
        [{"text": "Мои ключи"}],
        [{"text": "Получить месяц бесплатно"}],
        [{"text": "Помощь"}],
    ],
    "resize_keyboard": True,
}


async def _send_via_telegram_api(
    token: str,
    chat_id: int,
    text: str,
    *,
    parse_mode: str = "Markdown",
    disable_web_page_preview: bool = False,
    max_retries: int = 3,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Отправить сообщение через Telegram Bot API с retry (по аналогии с safe_send_message).
    Используется когда get_bot_instance() is None (процесс админки при вебхуках).
    reply_markup: опционально клавиатура в формате API (dict), будет отправлена как JSON.
    """
    import aiohttp
    import json as _json
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = _json.dumps(reply_markup)
    last_error = None
    last_status = None
    last_body = None
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    last_status = resp.status
                    body = await resp.text()
                    last_body = body
                    if resp.status == 200:
                        logger.info(
                            "[SUBSCRIPTION] Telegram API send succeeded to chat_id=%s (attempt %d)",
                            chat_id, attempt + 1,
                        )
                        return True
                    if resp.status == 429:
                        try:
                            import json
                            data = json.loads(body) if body else {}
                            retry_after = data.get("parameters", {}).get("retry_after", 5)
                        except Exception:
                            retry_after = 5
                        logger.warning(
                            "[SUBSCRIPTION] Telegram API 429 for chat_id=%s, retry after %ss (attempt %d/%d)",
                            chat_id, retry_after, attempt + 1, max_retries,
                        )
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_after)
                        continue
                    if resp.status == 400 and parse_mode:
                        payload_no_md = {
                            "chat_id": chat_id,
                            "text": text,
                            "disable_web_page_preview": disable_web_page_preview,
                        }
                        if reply_markup is not None:
                            payload_no_md["reply_markup"] = _json.dumps(reply_markup)
                        try:
                            async with aiohttp.ClientSession() as session2:
                                async with session2.post(
                                    url, json=payload_no_md, timeout=aiohttp.ClientTimeout(total=15)
                                ) as retry_resp:
                                    if retry_resp.status == 200:
                                        logger.info(
                                            "[SUBSCRIPTION] Telegram API send succeeded (no parse_mode) to chat_id=%s",
                                            chat_id,
                                        )
                                        return True
                        except Exception as e2:
                            logger.warning("[SUBSCRIPTION] Telegram API retry without parse_mode failed: %s", e2)
                    logger.warning(
                        "[SUBSCRIPTION] Telegram API %s for chat_id=%s (attempt %d/%d): %s",
                        resp.status, chat_id, attempt + 1, max_retries, body[:300] if body else "",
                    )
                    last_error = body
        except Exception as e:
            last_error = str(e)
            logger.warning(
                "[SUBSCRIPTION] Telegram API exception for chat_id=%s (attempt %d/%d): %s",
                chat_id, attempt + 1, max_retries, e,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error(
                    "[SUBSCRIPTION] Telegram API failed after %d attempts for chat_id=%s: %s",
                    max_retries, chat_id, e, exc_info=True,
                )
                return False
    logger.error(
        "[SUBSCRIPTION] Telegram API failed after %d attempts for chat_id=%s, status=%s: %s",
        max_retries, chat_id, last_status, (last_body or last_error or "")[:300],
    )
    return False


class SubscriptionPurchaseService:
    """Сервис для обработки покупки подписки - переписан с нуля по аналогии с ключами"""
    
    # Константы для определения покупки/продления
    VERY_RECENT_THRESHOLD = 3600  # 1 час - защита от двойного продления
    RECENT_SUBSCRIPTION_THRESHOLD = 1800  # 30 минут - если подписка создана недавно, это может быть покупка
    EXPIRES_AT_MATCH_TOLERANCE = 3600  # 1 час - допустимая разница для expires_at
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or app_settings.DATABASE_PATH
        self.payment_repo = PaymentRepository(db_path)
        self.subscription_repo = SubscriptionRepository(db_path)
        self.tariff_repo = TariffRepository(db_path)
        self.user_repo = UserRepository(db_path)
        
    # Константы для новой логики
    VIP_EXPIRES_AT = 4102434000  # 01.01.2100 - признак VIP/ручной установки
    DEFAULT_GRACE_PERIOD = 86400  # 24 часа
    REFERRAL_BONUS_DURATION = 30 * 24 * 3600  # 30 дней в секундах
    
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
            # КРИТИЧНО: Атомарная проверка и обновление статуса для предотвращения race conditions
            # Если платеж уже completed, значит он уже обработан (возможно другим процессом)
            if payment.status == PaymentStatus.COMPLETED:
                logger.info(f"[SUBSCRIPTION] Payment {payment_id} already completed, skipping")
                # Всё равно отправляем уведомление админу (на случай если при первой обработке отправка не прошла)
                if payment.subscription_id is not None:
                    subscription_row = await self.subscription_repo.get_subscription_by_id_async(payment.subscription_id)
                    tariff_row_for_notify = self.tariff_repo.get_tariff(payment.tariff_id) if payment.tariff_id else None
                    if subscription_row and tariff_row_for_notify:
                        tariff_for_notify = {
                            'id': tariff_row_for_notify[0],
                            'name': tariff_row_for_notify[1],
                            'duration_sec': tariff_row_for_notify[2],
                            'price_rub': tariff_row_for_notify[3],
                            'traffic_limit_mb': tariff_row_for_notify[4] if len(tariff_row_for_notify) > 4 else 0,
                        }
                        try:
                            await self._send_admin_purchase_notification(
                                payment,
                                payment.subscription_id,
                                tariff_for_notify,
                                subscription_row[4],
                                is_new=False,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[SUBSCRIPTION] Failed to send admin notification for already-completed payment {payment_id}: {e}"
                            )
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
                if payment_check.subscription_id is not None:
                    sub_row = await self.subscription_repo.get_subscription_by_id_async(payment_check.subscription_id)
                    if sub_row:
                        try:
                            await self._send_admin_purchase_notification(
                                payment_check,
                                payment_check.subscription_id,
                                tariff,
                                sub_row[4],
                                is_new=False,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[SUBSCRIPTION] Failed to send admin notification for race-completed payment {payment_id}: {e}"
                            )
                return True, None
            
            logger.info(
                f"[SUBSCRIPTION] Processing: payment={payment_id}, user={payment.user_id}, "
                f"tariff={tariff['name']}, duration={tariff['duration_sec']}s"
            )
            
            # Шаг 5 (НОВАЯ ЛОГИКА): Проверка subscription_id (защита от retry)
            # КРИТИЧНО: Проверяем subscription_id раньше, чем проверку наличия подписки
            # subscription_id обновляется раньше, чем статус completed
            if payment.subscription_id is not None:
                subscription_id_retry = payment.subscription_id
                
                # Проверяем, есть ли ключи для этой подписки
                async with open_async_connection(self.db_path) as conn:
                    async with conn.execute(
                        """
                        SELECT COUNT(*) FROM (
                            SELECT id FROM v2ray_keys WHERE subscription_id = ?
                            UNION ALL
                            SELECT id FROM keys WHERE subscription_id = ?
                        )
                        """
                        ,
                        (subscription_id_retry, subscription_id_retry)
                    ) as cursor:
                        keys_count = (await cursor.fetchone())[0] or 0
                
                if keys_count > 0:
                    # Платеж уже связан с подпиской и ключи созданы → это retry webhook
                    logger.info(
                        f"[SUBSCRIPTION] Payment {payment_id} already linked to subscription {subscription_id_retry}, "
                        f"keys exist ({keys_count}). This is a retry webhook. Skipping processing."
                    )
                    # Платеж уже обработан, подписка уже создана/продлена, ключи созданы
                    # Не нужно повторять обработку, только отправить уведомление (если не отправлено)
                    # TODO: Реализовать send_notification_only_if_needed
                    
                    # КРИТИЧНО: Обновляем статус на completed, если он еще не обновлен
                    # Это важно для retry webhook'ов, которые могут прийти до обновления статуса
                    update_success = await self.payment_repo.try_update_status(
                        payment_id,
                        PaymentStatus.COMPLETED,
                        PaymentStatus.PAID
                    )
                    if update_success:
                        logger.info(
                            f"[SUBSCRIPTION] Payment {payment_id} status updated to completed during retry"
                        )
                    else:
                        # Статус уже может быть completed или изменен другим процессом
                        logger.debug(
                            f"[SUBSCRIPTION] Payment {payment_id} status update skipped (already completed or changed)"
                        )
                    
                    # Уведомление админу при переходе в completed (в т.ч. при повторном webhook)
                    subscription_row_retry = await self.subscription_repo.get_subscription_by_id_async(subscription_id_retry)
                    if subscription_row_retry:
                        expires_at_retry = subscription_row_retry[4]
                        await self._send_admin_purchase_notification(
                            payment,
                            subscription_id_retry,
                            tariff,
                            expires_at_retry,
                            is_new=False
                        )
                    
                    return True, None
                else:
                    # Платеж связан с подпиской, но ключи НЕ созданы → нужно создать ключи
                    logger.warning(
                        f"[SUBSCRIPTION] Payment {payment_id} linked to subscription {subscription_id_retry}, "
                        f"but NO KEYS found. Creating keys..."
                    )
                    # Получаем подписку для создания ключей
                    subscription_row = await self.subscription_repo.get_subscription_by_id_async(subscription_id_retry)
                    if not subscription_row:
                        error_msg = f"Subscription {subscription_id_retry} not found"
                        logger.error(f"[SUBSCRIPTION] {error_msg}")
                        return False, error_msg
                    
                    # Создаем ключи для существующей подписки, если их нет
                    logger.warning(
                        f"[SUBSCRIPTION] Payment {payment_id} linked to subscription {subscription_id_retry}, "
                        f"but NO KEYS found. Creating keys now..."
                    )
                    
                    # Используем единый метод для создания ключей
                    created_keys, failed_servers = await self._create_keys_for_subscription(
                        subscription_id_retry,
                        payment.user_id,
                        payment,
                        tariff,
                        int(time.time())
                    )
                    
                    if created_keys == 0:
                        error_msg = f"Failed to create keys for subscription {subscription_id_retry} during retry"
                        logger.error(f"[SUBSCRIPTION] {error_msg}")
                        return False, error_msg
                    
                    logger.info(
                        f"[SUBSCRIPTION] Created {created_keys} keys for subscription {subscription_id_retry} during retry"
                    )
                    
                    # Обновляем статус платежа на completed
                    payment = await self.payment_repo.get_by_payment_id(payment_id)
                    if payment:
                        await self._mark_payment_completed(payment)
                        # Уведомление админу при успешном завершении
                        expires_at_retry = subscription_row[4]
                        await self._send_admin_purchase_notification(
                            payment,
                            subscription_id_retry,
                            tariff,
                            expires_at_retry,
                            is_new=False
                        )
                    
                    return True, None
            
            # Шаг 6 (НОВАЯ ЛОГИКА): Определяем покупка/продление (упрощенная логика)
            # ВАЖНО: Атомарность обеспечивается через:
            # 1. Атомарное обновление expires_at в SQL (extend_subscription_by_duration_async)
            # 2. Проверку статуса COMPLETED в начале функции
            # 3. Уникальные ограничения на подписки (если нужны)
            
            now = int(time.time())
            grace_threshold = now - self.DEFAULT_GRACE_PERIOD  # 24 часа назад
            
            # НОВАЯ УПРОЩЕННАЯ ЛОГИКА: Определение покупки/продления (1 проверка)
            # Получаем или создаем подписку
            subscription_row, was_created = await self._get_or_create_subscription(
                payment.user_id,
                payment,
                tariff,
                now
            )
            
            if not subscription_row:
                error_msg = "Failed to get or create subscription"
                logger.error(f"[SUBSCRIPTION] {error_msg}")
                return False, error_msg
            
            subscription_id = subscription_row[0]
            subscription_created_at = subscription_row[3]
            
            # Получаем все completed платежи для подписки (включая текущий)
            # Используем PaymentRepository для получения платежей
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT * FROM payments
                    WHERE subscription_id = ? AND status = 'completed'
                    ORDER BY created_at ASC
                    """,
                    (subscription_id,)
                ) as cursor:
                    payment_rows = await cursor.fetchall()
            all_payments = []
            for row in payment_rows:
                # Используем приватный метод PaymentRepository для создания Payment из строки БД
                try:
                    # Импортируем PaymentRepository для доступа к _payment_from_row
                    from payments.repositories.payment_repository import PaymentRepository
                    payment_repo_temp = PaymentRepository(self.db_path)
                    p = payment_repo_temp._payment_from_row(row)
                    if p:
                        all_payments.append(p)
                except Exception as e:
                    logger.warning(f"[SUBSCRIPTION] Error creating Payment from row: {e}")
                    continue
            
            # Добавляем текущий платеж в список (если его еще нет)
            current_payment_in_list = any(p.payment_id == payment_id for p in all_payments)
            if not current_payment_in_list:
                # Используем текущий платеж (будет completed после обработки)
                # Создаем копию с обновленным subscription_id и статусом
                temp_payment = Payment(
                    payment_id=payment.payment_id,
                    user_id=payment.user_id,
                    tariff_id=payment.tariff_id,
                    amount=payment.amount or 0,
                    currency=payment.currency or 'RUB',
                    email=payment.email,
                    status=PaymentStatus.COMPLETED,  # Считаем как completed для расчета
                    country=payment.country,
                    protocol=payment.protocol,
                    provider=payment.provider,
                    method=payment.method,
                    description=payment.description,
                    created_at=payment.created_at,
                    updated_at=payment.updated_at,
                    paid_at=payment.paid_at,
                    metadata=payment.metadata,
                    subscription_id=subscription_id
                )
                all_payments.append(temp_payment)
            
            # Обновляем subscription_id в платеже ПЕРЕД обновлением expires_at
            await self.payment_repo.update_subscription_id(payment.payment_id, subscription_id)
            # Сразу помечаем платёж completed, чтобы при любом исключении ниже (expires_at, ключи) статус не терялся
            payment_updated = await self.payment_repo.get_by_payment_id(payment_id)
            if payment_updated:
                await self._mark_payment_completed(payment_updated)
            
            # ВАЖНО: Проверяем VIP статус перед обновлением expires_at
            # VIP подписки не должны изменяться при продлении
            is_vip = self.user_repo.is_user_vip(payment.user_id)
            current_expires_at = subscription_row[4]
            is_vip_subscription = current_expires_at >= self.VIP_EXPIRES_AT - 86400  # Допускаем разницу до 1 дня
            
            # Расчет нового expires_at:
            # - Если подписка создана сейчас (was_created = True) - пересчитываем на основе всех платежей
            # - Если подписка уже существовала (was_created = False) - просто продлеваем от текущего expires_at
            # - Если это VIP подписка - не изменяем expires_at
            
            if is_vip_subscription or is_vip:
                # VIP подписка - не изменяем expires_at
                new_expires_at = current_expires_at
                logger.info(
                    f"[SUBSCRIPTION] Subscription {subscription_id} is VIP (expires_at={current_expires_at}), "
                    f"keeping VIP_EXPIRES_AT unchanged"
                )
            elif was_created:
                # Новая подписка - пересчитываем на основе всех платежей (передаём tariff на случай 0 из репо)
                new_expires_at = self._calculate_subscription_expires_at(
                    all_payments,
                    subscription_created_at,
                    payment.user_id,
                    current_tariff=tariff,
                )
            else:
                # Продление существующей подписки - просто прибавляем к текущему expires_at
                tariff_duration = tariff.get('duration_sec', 0) or 0
                new_expires_at = current_expires_at + tariff_duration
                
                logger.info(
                    f"[SUBSCRIPTION] Extending subscription {subscription_id}: "
                    f"current_expires_at={current_expires_at} ({current_expires_at - int(time.time())}s from now), "
                    f"tariff_duration={tariff_duration}s, new_expires_at={new_expires_at}"
                )
            
            # Атомарно обновляем expires_at и лимит трафика (только если изменился)
            # ВАЖНО: Всегда получаем traffic_limit_mb из тарифа (не используем or 0, чтобы не потерять значение)
            traffic_limit_mb = tariff.get('traffic_limit_mb')
            if traffic_limit_mb is None:
                # Если в тарифе нет лимита, используем 0
                traffic_limit_mb = 0
            else:
                # Используем значение из тарифа (может быть 0 для безлимита)
                traffic_limit_mb = int(traffic_limit_mb)
            
            if abs(current_expires_at - new_expires_at) > 60:  # Допускаем разницу до 1 минуты
                await self._update_subscription_expires_at(
                    subscription_id,
                    new_expires_at,
                    tariff['id'],
                    traffic_limit_mb
                )
            else:
                # Даже если expires_at не изменился, обновляем tariff_id и лимит трафика из тарифа (безопасно)
                # ВАЖНО: Обновляем tariff_id, даже если expires_at не изменился
                async with open_async_connection(self.db_path) as conn:
                    await conn.execute(
                        """
                        UPDATE subscriptions
                        SET tariff_id = ?, last_updated_at = ?
                        WHERE id = ?
                        """,
                        (tariff['id'], int(time.time()), subscription_id)
                    )
                    await conn.commit()
                    logger.info(
                        f"[SUBSCRIPTION] Updated subscription {subscription_id} tariff_id to {tariff['id']} "
                        f"(expires_at unchanged in process_subscription_purchase)"
                    )
                # ВАЖНО: Всегда обновляем лимит трафика из тарифа (даже если expires_at не изменился)
                await self._update_subscription_traffic_limit_safe(subscription_id, traffic_limit_mb)

            # При продлении существующей подписки (не новой) сбрасываем трафик — лимит обновляется на новый период
            # ВАЖНО: Сбрасываем трафик при любом продлении (даже минимальном), так как это новый платежный период
            if not was_created and new_expires_at > current_expires_at:
                try:
                    reset_success = await reset_subscription_traffic(subscription_id)
                    if reset_success:
                        logger.info(
                            f"[SUBSCRIPTION] Successfully reset traffic for subscription {subscription_id} after renewal"
                        )
                    else:
                        logger.warning(
                            f"[SUBSCRIPTION] Failed to reset traffic for subscription {subscription_id} after renewal"
                        )
                except Exception as reset_err:
                    logger.error(
                        f"[SUBSCRIPTION] Error resetting traffic for subscription {subscription_id}: {reset_err}",
                        exc_info=True,
                    )

            # Ключи уже должны быть созданы в _get_or_create_subscription при was_created = True
            # Но на всякий случай проверяем, есть ли ключи, и создаем их, если их нет
            # (защита от race condition или если была ошибка при создании)
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT id FROM v2ray_keys WHERE subscription_id = ?
                        UNION ALL
                        SELECT id FROM keys WHERE subscription_id = ?
                    )
                    """
                    ,
                    (subscription_id, subscription_id)
                ) as cursor:
                    keys_count = (await cursor.fetchone())[0] or 0
            
            if keys_count == 0:
                # Ключи не были созданы (возможно, из-за ошибки или race condition)
                logger.warning(
                    f"[SUBSCRIPTION] Subscription {subscription_id} has no keys. "
                    f"was_created={was_created}. Creating keys now as fallback..."
                )
                created_keys, failed_servers = await self._create_keys_for_subscription(
                    subscription_id,
                    payment.user_id,
                    payment,
                    tariff,
                    now
                )
                
                if created_keys == 0:
                    error_msg = f"Failed to create any keys for subscription {subscription_id}"
                    logger.error(f"[SUBSCRIPTION] {error_msg}")
                    # НЕ возвращаем ошибку, так как подписка уже создана
                    # Но логируем для мониторинга
            
            # Платёж уже помечен completed; перезагружаем для уведомлений
            payment_for_notify = await self.payment_repo.get_by_payment_id(payment_id)
            if not payment_for_notify:
                payment_for_notify = payment
            try:
                await self._send_admin_purchase_notification(
                    payment_for_notify,
                    subscription_id,
                    tariff,
                    new_expires_at,
                    was_created
                )
            except Exception as notif_err:
                logger.warning(
                    f"[SUBSCRIPTION] Failed to send admin purchase notification for payment {payment_id}: {notif_err}",
                    exc_info=True,
                )
            
            try:
                await self._send_universal_notification(payment_for_notify, subscription_row, tariff, new_expires_at, was_created, subscription_id)
            except Exception as notif_err:
                logger.warning(
                    f"[SUBSCRIPTION] Failed to send user notification for payment {payment_id}: {notif_err}",
                    exc_info=True,
                )
            
            # Финальная проверка консистентности
            await self._verify_subscription_consistency(subscription_id, payment_id)
            
            logger.info(
                f"[SUBSCRIPTION] Subscription {subscription_id} processed successfully: "
                f"was_created={was_created}, new_expires_at={new_expires_at}"
            )
                
            return True, None
            
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
                
                # ВАЖНО: Проверяем, не была ли подписка создана очень недавно (менее 1 часа)
                # и соответствует ли срок действия подписки ожидаемому (created_at + duration)
                subscription_age = now - existing_created_at if existing_created_at else 0
                expected_expires_at = existing_created_at + tariff['duration_sec']
                is_very_recent = subscription_age < self.VERY_RECENT_THRESHOLD
                expires_at_matches_expected = abs(existing_expires_at - expected_expires_at) < self.EXPIRES_AT_MATCH_TOLERANCE
                
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
            
            # Обновляем subscription_id в платеже
            await self.payment_repo.update_subscription_id(payment.payment_id, subscription_id)
            
            logger.info(
                f"[SUBSCRIPTION] Created subscription {subscription_id} for user {payment.user_id} as renewal of free key, "
                f"expires_at={expires_at}"
            )
            
            # Создаем ключи на всех активных V2Ray серверах с учетом access_level
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, name, api_url, api_key, domain, v2ray_path, COALESCE(access_level, 'all') as access_level
                    FROM servers
                    WHERE protocol = 'v2ray' AND active = 1
                    ORDER BY id
                    """
                ) as cursor:
                    servers_raw = await cursor.fetchall()
            
            # Фильтруем серверы по доступности для пользователя
            servers = []
            user_repo = UserRepository(self.db_path)
            is_vip = user_repo.is_user_vip(payment.user_id)
            now_ts = int(time.time())
            
            # Проверяем активную подписку для определения платного статуса
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT COUNT(*) FROM subscriptions 
                    WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                    """,
                    (payment.user_id, now_ts)
                ) as cursor:
                    has_active_subscription = (await cursor.fetchone())[0] > 0
            
            for server in servers_raw:
                server_access_level = server[6] if len(server) > 6 else 'all'
                if server_access_level == 'all':
                    servers.append(server[:6])  # Без access_level
                elif server_access_level == 'vip' and is_vip:
                    servers.append(server[:6])
                elif server_access_level == 'paid' and (is_vip or has_active_subscription):
                    servers.append(server[:6])
            
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
                                (server_id, user_id, v2ray_uuid, email, created_at, tariff_id, client_config, subscription_id, traffic_limit_mb)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                # ВАЖНО: Не деактивируем подписку, если платеж уже COMPLETED
                # Это предотвращает ситуацию, когда пользователь оплатил, но подписка деактивирована
                payment_status = await self.payment_repo.get_by_payment_id(payment.payment_id)
                if payment_status and payment_status.status == PaymentStatus.COMPLETED:
                    logger.error(
                        f"[SUBSCRIPTION] CRITICAL: Payment {payment.payment_id} is COMPLETED but no keys created for subscription {subscription_id}. "
                        f"Subscription will NOT be deactivated. Manual intervention required."
                    )
                    # Отправляем алерт администратору о критической ошибке
                    # TODO: Добавить отправку алерта администратору
                else:
                    # Платеж еще не COMPLETED, можно безопасно деактивировать подписку
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
            await self._mark_payment_completed(payment)
            
            logger.info(
                f"[SUBSCRIPTION] Subscription {subscription_id} created as renewal successfully for payment {payment.payment_id}, "
                f"notification_sent={notification_sent}"
            )
            
            # Отправляем уведомление администратору
            await self._send_admin_purchase_notification(
                payment,
                subscription_id,
                tariff,
                expires_at,
                is_new=True  # Это новая подписка (создана как продление бесплатного ключа)
            )
            
            return True, None
            
        except Exception as e:
            error_msg = f"Error creating subscription as renewal: {e}"
            logger.error(f"[SUBSCRIPTION] {error_msg}", exc_info=True)
            try:
                if 'subscription_id' in locals():
                    await self.subscription_repo.deactivate_subscription_async(subscription_id)
            except Exception as deact_err:
                logger.warning("[SUBSCRIPTION] Failed to deactivate subscription on error cleanup: %s", deact_err)
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
            
            # ВАЖНО: Проверяем VIP статус перед продлением
            is_vip = self.user_repo.is_user_vip(payment.user_id)
            is_vip_subscription = existing_expires_at >= self.VIP_EXPIRES_AT - 86400  # Допускаем разницу до 1 дня
            
            if is_purchase:
                # Если это покупка, не продлеваем подписку, только отправляем уведомление
                logger.info(
                    f"[SUBSCRIPTION] Subscription {subscription_id} already exists for purchase, "
                    f"not extending, only sending purchase notification"
                )
                new_expires_at = existing_expires_at  # Используем существующий срок
            elif is_vip_subscription or is_vip:
                # VIP подписка - не изменяем expires_at
                logger.info(
                    f"[SUBSCRIPTION] Subscription {subscription_id} is VIP (expires_at={existing_expires_at}), "
                    f"not extending, keeping VIP_EXPIRES_AT"
                )
                new_expires_at = existing_expires_at
            else:
                # Продление: увеличиваем срок действия
                # ВАЖНО: Используем атомарное обновление для предотвращения race conditions
                # Вместо чтения expires_at в Python и вычисления нового значения,
                # используем SQL-обновление с вычислением прямо в БД
                
                # ВАЖНО: Не изменяем срок подписки, если он был установлен вручную через админку
                # Признак ручной установки: expires_at очень далеко в будущем (например, 01.01.2100)
                MANUAL_EXPIRY_THRESHOLD = 4102434000  # 01.01.2100 - признак ручной установки
                ONE_YEAR_IN_SECONDS = 365 * 24 * 3600
                MAX_REASONABLE_EXPIRY = now + (10 * 365 * 24 * 3600)  # 10 лет
                
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
                    # Обновляем только tariff_id и лимит трафика, но не expires_at
                    await self.subscription_repo.extend_subscription_async(subscription_id, new_expires_at, tariff['id'])
                else:
                    # Обычное продление: используем атомарное SQL-обновление
                    # Это предотвращает race conditions при одновременном продлении несколькими процессами
                    logger.info(
                        f"[SUBSCRIPTION] Atomically extending subscription {subscription_id} for user {payment.user_id} "
                        f"by {tariff['duration_sec']}s (current expires_at={existing_expires_at})"
                    )
                    
                    # Атомарное обновление: вычисление нового expires_at происходит в SQL
                    # Используем ограничение MAX_REASONABLE_EXPIRY для защиты от неразумных дат,
                    # но только если текущий expires_at еще разумен
                    # Если existing_expires_at уже превышает MAX_REASONABLE_EXPIRY, значит это была ручная установка,
                    # и мы не должны продлевать (но это уже проверено выше в is_manually_set)
                    use_max_limit = existing_expires_at <= MAX_REASONABLE_EXPIRY
                    new_expires_at = await self.subscription_repo.extend_subscription_by_duration_async(
                        subscription_id, 
                        tariff['duration_sec'], 
                        tariff['id'],
                        max_expires_at=MAX_REASONABLE_EXPIRY if use_max_limit else None
                    )
                
                logger.info(
                        f"[SUBSCRIPTION] Subscription {subscription_id} extended atomically: "
                        f"new expires_at={new_expires_at} (added {tariff['duration_sec']}s, "
                        f"previous={existing_expires_at})"
                )
            
            # ВАЖНО: Обновляем лимит трафика подписки из тарифа, но сохраняем реферальный бонус
            # Это нужно делать и при покупке (is_purchase=True), и при продлении (is_purchase=False),
            # и при ручной установке срока (is_manually_set=True)
            # ВАЖНО: Правильно получаем traffic_limit_mb из тарифа (не используем or 0, чтобы не потерять значение)
            tariff_limit_mb = tariff.get('traffic_limit_mb')
            if tariff_limit_mb is None:
                tariff_limit_mb = 0
            else:
                tariff_limit_mb = int(tariff_limit_mb)
            await self._update_subscription_traffic_limit_safe(subscription_id, tariff_limit_mb)

            # Шаг 2: "Продлеваем" ключи подписки
            # ВАЖНО: expiry_at удалено из таблиц keys и v2ray_keys - срок действия берется из subscriptions
            # ВАЖНО: traffic_limit_mb не обновляется в ключах - лимит управляется через подписку (subscriptions.traffic_limit_mb)
            # Подписка уже обновлена выше (new_expires_at и traffic_limit_mb), ключи автоматически используют данные из подписки.
            logger.info(
                f"[SUBSCRIPTION] Extended subscription {subscription_id} - keys automatically use updated subscription data"
            )

            # Шаг 2.5: Сбрасываем трафик подписки ТОЛЬКО при реальном продлении.
            # Для is_purchase=True мы не продлеваем подписку и не должны вмешиваться в учёт трафика.
            if not is_purchase:
                try:
                    reset_success = await reset_subscription_traffic(subscription_id)
                    if reset_success:
                        logger.info(f"[SUBSCRIPTION] Successfully reset traffic for subscription {subscription_id}")
                    else:
                        logger.warning(f"[SUBSCRIPTION] Failed to reset traffic for subscription {subscription_id}")
                except Exception as e:
                    logger.error(
                        f"[SUBSCRIPTION] Error resetting traffic for subscription {subscription_id}: {e}",
                        exc_info=True,
                    )
            
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
                            await self._mark_payment_completed(payment)
                            await self._send_admin_purchase_notification(
                                payment,
                                subscription_id,
                                tariff,
                                new_expires_at,
                                is_new=False
                            )
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
            
            # Шаг 6: Обновляем subscription_id в платеже (если еще не обновлен)
            # ВАЖНО: Это нужно делать и для покупки, и для продления
            # УЛУЧШЕНИЕ: update_subscription_id теперь использует retry механизм
            subscription_id_updated = await self.payment_repo.update_subscription_id(payment.payment_id, subscription_id)
            if subscription_id_updated:
                logger.info(f"[SUBSCRIPTION] Updated payment {payment.payment_id} subscription_id to {subscription_id}")
            else:
                logger.error(
                    f"[SUBSCRIPTION] CRITICAL: Failed to update subscription_id for payment {payment.payment_id} "
                    f"after retries. Payment will remain without subscription_id. "
                    f"This should be fixed by monitoring task."
                )
                # Не прерываем выполнение, так как основная обработка завершена
                # Мониторинговая задача исправит это позже
            
            # Шаг 6.5: Уведомление успешно отправлено - помечаем платеж как completed
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
            
            # Отправляем уведомление администратору
            await self._send_admin_purchase_notification(
                payment,
                subscription_id,
                tariff,
                new_expires_at,
                is_new=False  # Это продление
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
                    await self._mark_payment_completed(payment)
                    await self._send_admin_purchase_notification(
                        payment,
                        subscription_id,
                        tariff,
                        existing_subscription[4],
                        is_new=True
                    )
                    return True, None
            
            # Шаг 1.5: Обновляем лимит трафика подписки из тарифа
            # ВАЖНО: Это нужно делать всегда, даже если уведомление уже отправлено
            traffic_limit_mb = tariff.get('traffic_limit_mb', 0) or 0
            await self.subscription_repo.update_subscription_traffic_limit_async(subscription_id, traffic_limit_mb)
            logger.info(
                f"[SUBSCRIPTION] Updated subscription {subscription_id} traffic_limit_mb to {traffic_limit_mb} MB "
                f"(in _send_purchase_notification_for_existing_subscription)"
            )
            
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
            
            # Отправляем уведомление администратору
            expires_at = existing_subscription[4]  # expires_at из кортежа подписки
            await self._send_admin_purchase_notification(
                payment,
                subscription_id,
                tariff,
                expires_at,
                is_new=True  # Это новая подписка
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
            
            # Обновляем статус платежа - используем единый метод для надежности
            await self._mark_payment_completed(payment)
            
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
                
                # ВАЖНО: Проверяем, не была ли подписка создана очень недавно (менее 1 часа)
                # и соответствует ли срок действия подписки ожидаемому (created_at + duration)
                # Если срок действия = created_at + duration, значит это только что созданная подписка
                # и её не нужно продлевать, а нужно просто отправить уведомление о покупке
                subscription_age = now - existing_created_at if existing_created_at else 0
                expected_expires_at = existing_created_at + tariff['duration_sec']
                is_very_recent = subscription_age < self.VERY_RECENT_THRESHOLD
                expires_at_matches_expected = abs(existing_expires_at - expected_expires_at) < self.EXPIRES_AT_MATCH_TOLERANCE
                
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
                            await self._send_admin_purchase_notification(
                                payment,
                                subscription_id,
                                tariff,
                                existing_expires_at,
                                is_new=True
                            )
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
                        
                        # Обновляем subscription_id в платеже
                        await self.payment_repo.update_subscription_id(payment.payment_id, subscription_id)
                        
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
                        
                        # Обновляем subscription_id в платеже
                        await self.payment_repo.update_subscription_id(payment.payment_id, subscription_id)
                        
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
            
            # Сначала получаем все V2Ray серверы с access_level
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256, COALESCE(access_level, 'all') as access_level
                    FROM servers
                    WHERE active = 1 AND protocol = 'v2ray'
                    ORDER BY id
                    """
                ) as cursor:
                    v2ray_servers_raw = await cursor.fetchall()
            
            # Фильтруем серверы по доступности для пользователя
            v2ray_servers = []
            user_repo = UserRepository(self.db_path)
            is_vip = user_repo.is_user_vip(payment.user_id)
            now_ts = int(time.time())
            
            # Проверяем активную подписку для определения платного статуса
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT COUNT(*) FROM subscriptions 
                    WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                    """,
                    (payment.user_id, now_ts)
                ) as cursor:
                    has_active_subscription = (await cursor.fetchone())[0] > 0
            
            for server in v2ray_servers_raw:
                server_access_level = server[8] if len(server) > 8 else 'all'
                if server_access_level == 'all':
                    v2ray_servers.append(server[:8])  # Без access_level
                elif server_access_level == 'vip' and is_vip:
                    v2ray_servers.append(server[:8])
                elif server_access_level == 'paid' and (is_vip or has_active_subscription):
                    v2ray_servers.append(server[:8])
            
            # Получаем Outline сервер (приоритет серверу с id=8) с проверкой доступности
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256, COALESCE(access_level, 'all') as access_level
                    FROM servers
                    WHERE active = 1 AND protocol = 'outline'
                    ORDER BY CASE WHEN id = 8 THEN 0 ELSE 1 END, id
                    """
                ) as cursor:
                    outline_servers_raw = await cursor.fetchall()
            
            outline_server_row = None
            for server in outline_servers_raw:
                server_access_level = server[8] if len(server) > 8 else 'all'
                if server_access_level == 'all':
                    outline_server_row = server[:8]
                    break
                elif server_access_level == 'vip' and is_vip:
                    outline_server_row = server[:8]
                    break
                elif server_access_level == 'paid' and (is_vip or has_active_subscription):
                    outline_server_row = server[:8]
                    break
            
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
                # Подписку не деактивируем — ключи можно доставить позже (повтор, скрипт или админ).
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
                    await self._mark_payment_completed(payment)
                    await self._send_admin_purchase_notification(
                        payment,
                        subscription_id,
                        tariff,
                        expires_at,
                        is_new=True
                    )
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
            except Exception as deact_err:
                logger.warning("[SUBSCRIPTION] Failed to deactivate subscription on error cleanup: %s", deact_err)
            return False, error_msg
    
    async def _mark_payment_completed(self, payment: Payment) -> bool:
        """
        Единая точка обновления статуса платежа на COMPLETED.
        Использует атомарное обновление с fallback механизмами.
        
        Returns:
            bool: True если платеж успешно помечен как COMPLETED, False в противном случае
        """
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
                    return True
                else:
                    # Пробуем обновить через обычный update
                    payment.mark_as_completed()
                    await self.payment_repo.update(payment)
                    logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed via update()")
                    return True
            else:
                logger.info(f"[SUBSCRIPTION] Payment {payment.payment_id} marked as completed atomically")
                return True
                
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
                    return True
            except Exception as sql_error:
                logger.error(
                    f"[SUBSCRIPTION] Failed to mark payment {payment.payment_id} as completed via direct SQL: {sql_error}",
                    exc_info=True
                )
                return False
    
    async def _update_subscription_and_payment_atomic(
        self,
        subscription_id: int,
        new_expires_at: int,
        tariff_id: int,
        traffic_limit_mb: int,
        payment_id: str
    ) -> bool:
        """
        Атомарно обновить подписку (expires_at, tariff_id, traffic_limit_mb) и платеж (status = COMPLETED).
        Использует транзакцию для обеспечения консистентности данных.
        
        Returns:
            bool: True если обновление успешно, False в противном случае
        """
        try:
            async with open_async_connection(self.db_path) as conn:
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    # Обновляем подписку
                    await conn.execute(
                        """
                        UPDATE subscriptions
                        SET expires_at = ?, last_updated_at = ?, tariff_id = ?, traffic_limit_mb = ?
                        WHERE id = ?
                        """,
                        (new_expires_at, int(time.time()), tariff_id, traffic_limit_mb, subscription_id)
                    )
                    
                    # Обновляем платеж (только если он в статусе PAID)
                    await conn.execute(
                        """
                        UPDATE payments
                        SET status = ?, updated_at = ?
                        WHERE payment_id = ? AND status = ?
                        """,
                        (PaymentStatus.COMPLETED.value, int(time.time()), payment_id, PaymentStatus.PAID.value)
                    )
                    
                    await conn.commit()
                    logger.info(
                        f"[SUBSCRIPTION] Atomically updated subscription {subscription_id} and payment {payment_id}"
                    )
                    return True
                except Exception as e:
                    await conn.rollback()
                    raise e
        except Exception as e:
            logger.error(
                f"[SUBSCRIPTION] Failed to atomically update subscription {subscription_id} and payment {payment_id}: {e}",
                exc_info=True
            )
            return False
    
    async def _is_purchase_or_renewal(self, subscription_id: int) -> bool:
        """
        Определить покупка это или продление на основе количества completed платежей.
        
        Returns:
            bool: True если это покупка (1 completed платеж), False если продление (>1 completed платежей)
        """
        try:
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT COUNT(*) FROM payments
                    WHERE subscription_id = ? AND status = 'completed'
                    """,
                    (subscription_id,)
                ) as cursor:
                    completed_count = (await cursor.fetchone())[0] or 0
            
            is_purchase = completed_count <= 1
            logger.debug(
                f"[SUBSCRIPTION] Subscription {subscription_id}: {completed_count} completed payments, "
                f"is_purchase={is_purchase}"
            )
            return is_purchase
        except Exception as e:
            logger.error(
                f"[SUBSCRIPTION] Error determining purchase/renewal for subscription {subscription_id}: {e}",
                exc_info=True
            )
            # В случае ошибки считаем продлением (более безопасно)
            return False
    
    async def _verify_subscription_consistency(self, subscription_id: int, payment_id: str) -> None:
        """
        Финальная проверка консистентности после обработки платежа.
        Проверяет:
        - Есть ли ключи у подписки?
        - Обновлен ли статус платежа?
        - Отправлено ли уведомление?
        
        Логирует предупреждения при несоответствиях.
        """
        try:
            # Проверка 1: Есть ли ключи у подписки?
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT id FROM v2ray_keys WHERE subscription_id = ?
                        UNION ALL
                        SELECT id FROM keys WHERE subscription_id = ?
                    )
                    """,
                    (subscription_id, subscription_id)
                ) as cursor:
                    keys_count = (await cursor.fetchone())[0] or 0
            
            if keys_count == 0:
                logger.warning(
                    f"[SUBSCRIPTION] CONSISTENCY CHECK: Subscription {subscription_id} has no keys after payment {payment_id} processing"
                )
            
            # Проверка 2: Обновлен ли статус платежа?
            payment = await self.payment_repo.get_by_payment_id(payment_id)
            if payment and payment.status != PaymentStatus.COMPLETED:
                logger.warning(
                    f"[SUBSCRIPTION] CONSISTENCY CHECK: Payment {payment_id} status is {payment.status.value}, "
                    f"expected COMPLETED after processing subscription {subscription_id}"
                )
            
            # Проверка 3: Отправлено ли уведомление?
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT purchase_notification_sent FROM subscriptions WHERE id = ?
                    """,
                    (subscription_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    notification_sent = row[0] if row else None
            
            if notification_sent != 1:
                logger.warning(
                    f"[SUBSCRIPTION] CONSISTENCY CHECK: Purchase notification not sent for subscription {subscription_id} "
                    f"(purchase_notification_sent={notification_sent})"
                )
            
            logger.debug(
                f"[SUBSCRIPTION] Consistency check completed for subscription {subscription_id}, payment {payment_id}: "
                f"keys={keys_count}, payment_status={payment.status.value if payment else 'N/A'}, "
                f"notification_sent={notification_sent}"
            )
        except Exception as e:
            logger.error(
                f"[SUBSCRIPTION] Error during consistency check for subscription {subscription_id}, payment {payment_id}: {e}",
                exc_info=True
            )
    
    def _calculate_referral_bonuses(self, user_id: int, expires_at: int) -> int:
        """
        Рассчитать реферальные бонусы для пользователя.
        
        Условия для начисления бонуса:
        1. Реферал должен иметь bonus_issued = 1 в таблице referrals
        2. Реферал должен иметь completed платежи с amount > 0
        3. Платежи должны быть до текущего expires_at
        
        Формула:
        - Количество рефералов = COUNT(*) WHERE referrer_id = user_id AND bonus_issued = 1
        - Реферальные бонусы = bonuses_count * REFERRAL_BONUS_DURATION
        - REFERRAL_BONUS_DURATION = 30 дней = 2592000 секунд
        """
        try:
            with open_connection(self.db_path) as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT COUNT(*)
                    FROM referrals r
                    WHERE r.referrer_id = ?
                      AND r.bonus_issued = 1
                      AND EXISTS (
                          SELECT 1 FROM payments p
                          WHERE p.user_id = r.referred_id
                            AND p.status = 'completed'
                            AND p.amount > 0
                            AND p.created_at <= ?
                      )
                """, (user_id, expires_at))
                
                bonuses_count = c.fetchone()[0] or 0
                referral_bonuses_sec = bonuses_count * self.REFERRAL_BONUS_DURATION
                
                logger.debug(
                    f"[SUBSCRIPTION] Referral bonuses for user {user_id}: "
                    f"{bonuses_count} referrals × {self.REFERRAL_BONUS_DURATION} sec = {referral_bonuses_sec} sec"
                )
                
                return referral_bonuses_sec
        except Exception as e:
            logger.error(f"[SUBSCRIPTION] Error calculating referral bonuses for user {user_id}: {e}")
            return 0  # В случае ошибки возвращаем 0 (без бонусов)
    
    def _calculate_subscription_expires_at(
        self,
        payments: List[Payment],
        subscription_created_at: int,
        user_id: int,
        *,
        current_tariff: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Пересчет expires_at на основе ВСЕХ платежей с учетом разных тарифов и реферальных бонусов.
        
        Формула:
        - Базовая дата = max(первый_платеж, created_at)
        - Суммарная длительность = sum(duration_sec всех платежей) - учитываются разные тарифы
        - Реферальные бонусы = 30 дней × количество рефералов (bonus_issued=1, с completed платежами)
        - expires_at = базовая_дата + суммарная_длительность + реферальные_бонусы
        
        current_tariff: при новой подписке передать тариф текущего платежа; если из репозитория
        не удалось взять duration и total_duration_sec=0, используется duration_sec из current_tariff,
        чтобы срок не оставался равным base_date (просрочен).
        VIP подписки не пересчитываются (возвращается VIP_EXPIRES_AT).
        """
        # VIP подписки не пересчитываются
        if self.user_repo.is_user_vip(user_id):
            logger.debug(f"[SUBSCRIPTION] User {user_id} is VIP, returning VIP_EXPIRES_AT")
            return self.VIP_EXPIRES_AT
        
        # Шаг 1: Собираем все completed платежи
        completed_payments = [p for p in payments if p.status == PaymentStatus.COMPLETED]
        
        if not completed_payments:
            # Если нет завершенных платежей, возвращаем created_at
            # Это может произойти, если подписка только что создана и платеж еще не обработан
            logger.debug(f"[SUBSCRIPTION] No completed payments for subscription, returning created_at")
            return subscription_created_at
        
        # Шаг 2: Базовая дата = первый платеж или created_at (что больше)
        # Преобразуем created_at из datetime в timestamp (int)
        first_payment_timestamps = []
        for p in completed_payments:
            if p.created_at:
                if isinstance(p.created_at, datetime):
                    first_payment_timestamps.append(int(p.created_at.timestamp()))
                elif isinstance(p.created_at, (int, float)):
                    first_payment_timestamps.append(int(p.created_at))
                else:
                    logger.warning(f"[SUBSCRIPTION] Unexpected created_at type for payment {p.payment_id}: {type(p.created_at)}")
        
        if not first_payment_timestamps:
            # Если не удалось получить даты платежей, возвращаем created_at
            # Это может произойти при некорректных данных платежей
            return subscription_created_at
        
        first_payment_date = min(first_payment_timestamps)
        # Базовая дата = max(первый платеж, created_at подписки)
        # Если подписка создана с expires_at = now, то subscription_created_at = now
        # Это гарантирует, что expires_at будет рассчитан от правильной базовой даты
        base_date = max(first_payment_date, subscription_created_at)
        
        # Шаг 3: Суммарная длительность всех платежей (учитываются РАЗНЫЕ тарифы)
        total_duration_sec = 0
        for payment in completed_payments:
            if payment.tariff_id:
                tariff_row = self.tariff_repo.get_tariff(payment.tariff_id)
                if tariff_row and tariff_row[2]:  # duration_sec
                    tariff_duration = tariff_row[2]
                    total_duration_sec += tariff_duration
        # Fallback: если длительность 0 (репо не вернул тариф), используем current_tariff для новой подписки
        if total_duration_sec == 0 and current_tariff:
            fallback = current_tariff.get("duration_sec") or 0
            if fallback:
                total_duration_sec = int(fallback)
                logger.info(
                    f"[SUBSCRIPTION] Using current_tariff duration_sec={total_duration_sec} for expires_at (repo had no duration)"
                )
        
        # Шаг 4: Реферальные бонусы (вычисляем предварительный expires_at для расчета бонусов)
        # Сначала вычисляем без бонусов, затем используем для расчета реферальных бонусов
        preliminary_expires_at = base_date + total_duration_sec
        referral_bonuses_sec = self._calculate_referral_bonuses(user_id, preliminary_expires_at)
        
        # Шаг 5: Итоговый expires_at
        expires_at = base_date + total_duration_sec + referral_bonuses_sec
        
        logger.info(
            f"[SUBSCRIPTION] Calculated expires_at for user {user_id}: "
            f"base_date={base_date}, total_duration={total_duration_sec} sec, "
            f"referral_bonuses={referral_bonuses_sec} sec, expires_at={expires_at}"
        )
        
        return expires_at
    
    async def _get_or_create_subscription(
        self,
        user_id: int,
        payment: Payment,
        tariff: Dict[str, Any],
        now: int
    ) -> Tuple[Optional[Tuple], bool]:
        """
        Получить существующую подписку или создать новую.
        
        Returns:
            (subscription_tuple, was_created: bool)
            subscription_tuple: (id, user_id, token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified)
        """
        from ..utils.renewal_detector import DEFAULT_GRACE_PERIOD
        
        grace_threshold = now - DEFAULT_GRACE_PERIOD  # 24 часа назад
        
        # Проверяем наличие активной подписки
        async with open_async_connection(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, grace_threshold)
            ) as cursor:
                existing_subscription_row = await cursor.fetchone()
        
        if existing_subscription_row:
            # ВАЖНО: Обновляем лимит трафика для существующей подписки из тарифа (безопасно, сохраняем реферальный бонус)
            subscription_id = existing_subscription_row[0]
            # ВАЖНО: Правильно получаем traffic_limit_mb из тарифа (не используем or 0, чтобы не потерять значение)
            traffic_limit_mb = tariff.get('traffic_limit_mb')
            if traffic_limit_mb is None:
                traffic_limit_mb = 0
            else:
                traffic_limit_mb = int(traffic_limit_mb)
            await self._update_subscription_traffic_limit_safe(subscription_id, traffic_limit_mb)
            return existing_subscription_row, False  # Подписка существовала
        
        # ВАЖНО: Проверяем VIP статус перед созданием подписки
        is_vip = self.user_repo.is_user_vip(user_id)
        
        # Создаем новую подписку
        subscription_token = str(uuid.uuid4())
        
        # ВАЖНО: При создании новой подписки устанавливаем expires_at = now
        # Реальная дата окончания будет пересчитана в process_subscription_purchase()
        # через _calculate_subscription_expires_at() на основе всех платежей
        # Это предотвращает двойное добавление длительности тарифа
        if is_vip:
            expires_at = self.VIP_EXPIRES_AT
            traffic_limit_mb = 0  # VIP = безлимит
            logger.info(f"[SUBSCRIPTION] Creating VIP subscription for user {user_id}, expires_at={expires_at}")
        else:
            # Устанавливаем expires_at = now, будет пересчитан в process_subscription_purchase()
            expires_at = now
            # ВАЖНО: Правильно получаем traffic_limit_mb из тарифа (не используем or 0, чтобы не потерять значение)
            traffic_limit_mb = tariff.get('traffic_limit_mb')
            if traffic_limit_mb is None:
                traffic_limit_mb = 0
            else:
                traffic_limit_mb = int(traffic_limit_mb)
            logger.info(
                f"[SUBSCRIPTION] Creating new subscription for user {user_id}, "
                f"expires_at={expires_at} (will be recalculated in process_subscription_purchase)"
            )
        
        subscription_id = None
        async with open_async_connection(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                # Финальная проверка перед созданием (защита от race condition)
                async with conn.execute(
                    """
                    SELECT id FROM subscriptions
                    WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                    LIMIT 1
                    """,
                    (user_id, grace_threshold)
                ) as check_cursor:
                    existing = await check_cursor.fetchone()
                
                if existing:
                    # Подписка уже создана другим процессом
                    subscription_id = existing[0]
                    await conn.commit()
                else:
                    # Создаем новую подписку
                    cursor = await conn.execute(
                        """
                        INSERT INTO subscriptions (user_id, subscription_token, created_at, expires_at, tariff_id, is_active, notified, traffic_limit_mb)
                        VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                        """,
                        (user_id, subscription_token, now, expires_at, tariff['id'], traffic_limit_mb),
                    )
                    subscription_id = cursor.lastrowid
                    await conn.commit()
            except Exception as e:
                await conn.rollback()
                raise e
        
        # Получаем созданную подписку
        async with open_async_connection(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE id = ?
                """,
                (subscription_id,)
            ) as cursor:
                new_subscription_row = await cursor.fetchone()
        
        # Если подписка была создана, создаем ключи для неё
        if new_subscription_row:
            created_keys, failed_servers = await self._create_keys_for_subscription(
                subscription_id,
                user_id,
                payment,
                tariff,
                now
            )
            
            if created_keys == 0:
                logger.error(
                    f"[SUBSCRIPTION] Failed to create any keys for newly created subscription {subscription_id}, "
                    f"but subscription was created. This may require manual intervention."
                )
                # НЕ удаляем подписку здесь, так как она может быть создана другим процессом
                # Просто логируем ошибку
        
        return new_subscription_row, True  # Подписка создана
    
    async def _create_single_v2ray_key(
        self,
        server_info: Tuple,
        subscription_id: int,
        user_id: int,
        tariff: Dict[str, Any],
        now: int,
        client_pool: Optional[ServerClientPool] = None
    ) -> Tuple[bool, Optional[int]]:
        """
        Создать V2Ray ключ на одном сервере.
        
        Returns:
            Tuple[success, server_id] - success=True если ключ создан, server_id для failed_servers
        """
        server_id, server_name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256 = server_info
        v2ray_uuid = None
        protocol_client = None
        key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"
        
        # Retry механизм: до 3 попыток для создания ключа (защита от таймаутов)
        max_retries = 3
        retry_delay = 2  # секунды между попытками
        
        for attempt in range(1, max_retries + 1):
            try:
                # ОПТИМИЗАЦИЯ: Используем пул клиентов для переиспользования соединений
                if client_pool:
                    protocol_client = await client_pool.get_client(server_id, 'v2ray', api_url, api_key, domain)
                else:
                    server_config = {
                        'api_url': api_url,
                        'api_key': api_key,
                        'domain': domain,
                    }
                    protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                
                if not protocol_client:
                    raise Exception(f"Failed to create protocol client for server {server_id}")
                
                # Создание пользователя с retry
                if attempt > 1:
                    logger.info(
                        f"[SUBSCRIPTION] Retry {attempt}/{max_retries} creating v2ray key for subscription {subscription_id} "
                        f"on server {server_id} ({server_name})"
                    )
                    await asyncio.sleep(retry_delay)
                
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
                
                # Сохранение V2Ray ключа в БД с защитой от race condition
                async with open_async_connection(self.db_path) as conn:
                    await conn.execute("BEGIN IMMEDIATE")
                    try:
                        await conn.execute("PRAGMA foreign_keys = OFF")
                        
                        # Двойная проверка: проверяем существование ключа прямо перед вставкой
                        async with conn.execute(
                            """
                            SELECT id FROM v2ray_keys
                            WHERE server_id = ? AND subscription_id = ?
                            LIMIT 1
                            """
                        , (server_id, subscription_id)) as check_cursor:
                            if await check_cursor.fetchone():
                                logger.warning(
                                    f"[SUBSCRIPTION] Key for subscription {subscription_id} on server {server_id} "
                                    f"already exists (race condition), deleting duplicate from server"
                                )
                                # Удаляем дубликат с сервера
                                if protocol_client:
                                    try:
                                        await protocol_client.delete_user(v2ray_uuid)
                                    except Exception as e:
                                        logger.warning(f"[SUBSCRIPTION] Failed to delete duplicate key from server: {e}")
                                await conn.commit()
                                return True, None  # Ключ уже существует, считаем успехом
                        
                        # Вставляем ключ только если его еще нет
                        cursor = await conn.execute(
                            """
                            INSERT INTO v2ray_keys 
                            (server_id, user_id, v2ray_uuid, email, created_at, tariff_id, client_config, subscription_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                server_id,
                                user_id,
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
                            (server_id, user_id, subscription_id, v2ray_uuid)
                        ) as verify_cursor:
                            if not await verify_cursor.fetchone():
                                raise Exception(f"Key was not saved to database for server {server_id}")
                    except Exception as db_error:
                        await conn.rollback()
                        raise db_error
                    finally:
                        await conn.execute("PRAGMA foreign_keys = ON")
                
                if attempt > 1:
                    logger.info(
                        f"[SUBSCRIPTION] Successfully created v2ray key for subscription {subscription_id} "
                        f"on server {server_id} ({server_name}) on retry {attempt}"
                    )
                else:
                    logger.info(
                        f"[SUBSCRIPTION] Created v2ray key for subscription {subscription_id} on server {server_id} ({server_name})"
                    )
                
                    # НЕ закрываем клиент при успехе, если он из пула - пул закроет его сам
                    # Закрываем только если клиент создан напрямую
                    if not client_pool and protocol_client:
                        try:
                            await protocol_client.close()
                        except Exception as close_err:
                            logger.debug("[SUBSCRIPTION] Protocol client close: %s", close_err)
                
                return True, None
                
            except Exception as e:
                error_msg = str(e)
                is_timeout = "timeout" in error_msg.lower() or "Timeout" in error_msg
                
                if attempt < max_retries and is_timeout:
                    # Таймаут - повторяем попытку
                    logger.warning(
                        f"[SUBSCRIPTION] Timeout creating v2ray key for subscription {subscription_id} "
                        f"on server {server_id} ({server_name}), attempt {attempt}/{max_retries}. "
                        f"Retrying in {retry_delay}s..."
                    )
                    # При retry закрываем клиент (даже из пула), чтобы создать новый
                    if protocol_client:
                        try:
                            await protocol_client.close()
                        except Exception as close_err:
                            logger.debug("[SUBSCRIPTION] Protocol client close on retry: %s", close_err)
                    # Удаляем клиент из пула для следующей попытки
                    if client_pool and server_id in client_pool._clients:
                        del client_pool._clients[server_id]
                    protocol_client = None
                    v2ray_uuid = None
                    continue
                else:
                    # Другая ошибка или последняя попытка - логируем
                    logger.error(
                        f"[SUBSCRIPTION] Failed to create v2ray key for subscription {subscription_id} "
                        f"on server {server_id} ({server_name}), attempt {attempt}/{max_retries}: {e}",
                        exc_info=True,
                    )
                    # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить его с сервера
                    if protocol_client and v2ray_uuid:
                        try:
                            await protocol_client.delete_user(v2ray_uuid)
                            logger.info(f"[SUBSCRIPTION] Cleaned up orphaned v2ray key on server {server_id}")
                        except Exception as cleanup_error:
                            logger.error(f"[SUBSCRIPTION] Failed to cleanup orphaned v2ray key: {cleanup_error}")
                    
                    # НЕ закрываем клиент при ошибке, если он из пула - пул закроет его сам
                    # Но при retry закрываем клиент, чтобы создать новый
                    if not client_pool and protocol_client:
                        try:
                            await protocol_client.close()
                        except Exception as close_err:
                            logger.debug("[SUBSCRIPTION] Protocol client close: %s", close_err)
                    
                    return False, server_id  # Возвращаем server_id для failed_servers
        
        return False, server_id
    
    async def _create_single_outline_key(
        self,
        server_info: Tuple,
        subscription_id: int,
        user_id: int,
        tariff: Dict[str, Any],
        now: int,
        client_pool: Optional[ServerClientPool] = None
    ) -> Tuple[bool, Optional[int]]:
        """
        Создать Outline ключ на одном сервере.
        
        Returns:
            Tuple[success, server_id] - success=True если ключ создан, server_id для failed_servers
        """
        server_id, server_name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256 = server_info
        outline_key_id = None
        protocol_client = None
        key_email = f"{user_id}_subscription_{subscription_id}@veilbot.com"
        
        try:
            # ОПТИМИЗАЦИЯ: Используем пул клиентов для переиспользования соединений
            if client_pool:
                protocol_client = await client_pool.get_client(server_id, 'outline', api_url, None, None, cert_sha256)
            else:
                server_config = {
                    'api_url': api_url,
                    'cert_sha256': cert_sha256,
                }
                protocol_client = ProtocolFactory.create_protocol('outline', server_config)
            
            if not protocol_client:
                raise Exception(f"Failed to create protocol client for server {server_id}")
            user_data = await protocol_client.create_user(key_email)
            
            if not user_data or not user_data.get('id'):
                raise Exception("Failed to create user on Outline server")
            
            outline_key_id = user_data['id']
            access_url = user_data['accessUrl']
            
            # Сохранение Outline ключа в БД с защитой от race condition
            async with open_async_connection(self.db_path) as conn:
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    await conn.execute("PRAGMA foreign_keys = OFF")
                    
                    # Двойная проверка: проверяем существование ключа прямо перед вставкой
                    async with conn.execute(
                        """
                        SELECT id FROM keys
                        WHERE server_id = ? AND subscription_id = ? AND protocol = 'outline'
                        LIMIT 1
                        """
                    , (server_id, subscription_id)) as check_cursor:
                        if await check_cursor.fetchone():
                            logger.warning(
                                f"[SUBSCRIPTION] Outline key for subscription {subscription_id} on server {server_id} "
                                f"already exists (race condition), deleting duplicate from server"
                            )
                            # Удаляем дубликат с сервера
                            if protocol_client:
                                try:
                                    await protocol_client.delete_user(outline_key_id)
                                except Exception as e:
                                    logger.warning(f"[SUBSCRIPTION] Failed to delete duplicate outline key from server: {e}")
                            await conn.commit()
                            return True, None  # Ключ уже существует, считаем успехом
                    
                    # Вставляем ключ только если его еще нет
                    cursor = await conn.execute(
                        """
                        INSERT INTO keys 
                        (server_id, user_id, access_url, traffic_limit_mb, notified, key_id, created_at, email, tariff_id, protocol, subscription_id)
                        VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            server_id,
                            user_id,
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
                        (server_id, user_id, subscription_id, outline_key_id)
                    ) as verify_cursor:
                        if not await verify_cursor.fetchone():
                            raise Exception(f"Key was not saved to database for server {server_id}")
                except Exception as db_error:
                    await conn.rollback()
                    raise db_error
                finally:
                    await conn.execute("PRAGMA foreign_keys = ON")
            
            logger.info(
                f"[SUBSCRIPTION] Created outline key for subscription {subscription_id} on server {server_id} ({server_name})"
            )
            
            # НЕ закрываем клиент, если он из пула - пул закроет его сам
            if not client_pool and protocol_client:
                try:
                    await protocol_client.close()
                except Exception as close_err:
                    logger.debug("[SUBSCRIPTION] Protocol client close: %s", close_err)
            
            return True, None
            
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
            
            # НЕ закрываем клиент, если он из пула - пул закроет его сам
            if not client_pool and protocol_client:
                try:
                    await protocol_client.close()
                except Exception as close_err:
                    logger.debug("[SUBSCRIPTION] Protocol client close: %s", close_err)
            
            return False, server_id
    
    async def _create_keys_for_subscription(
        self,
        subscription_id: int,
        user_id: int,
        payment: Payment,
        tariff: Dict[str, Any],
        now: int
    ) -> Tuple[int, List[int]]:
        """
        Создать ключи для подписки на всех активных серверах.
        
        Args:
            subscription_id: ID подписки
            user_id: ID пользователя
            payment: Объект платежа (для получения информации)
            tariff: Словарь с информацией о тарифе
            now: Текущее время (timestamp)
            
        Returns:
            Tuple[created_keys_count, failed_servers_list]
            created_keys_count: Количество успешно созданных ключей
            failed_servers_list: Список ID серверов, на которых не удалось создать ключи
        """
        try:
            # Получаем все V2Ray серверы с access_level
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256, COALESCE(access_level, 'all') as access_level
                    FROM servers
                    WHERE active = 1 AND protocol = 'v2ray'
                    ORDER BY id
                    """
                ) as cursor:
                    v2ray_servers_raw = await cursor.fetchall()
            
            # Фильтруем серверы по доступности для пользователя
            v2ray_servers = []
            user_repo = UserRepository(self.db_path)
            is_vip = user_repo.is_user_vip(user_id)
            now_ts = now
            
            # Проверяем активную подписку для определения платного статуса.
            # Учитываем и подписку, для которой создаём ключи (subscription_id): у новой подписки
            # expires_at = now, поэтому условие expires_at > now не выполняется — без этого
            # для paid-серверов список серверов был бы пуст и ключи не создавались бы.
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT COUNT(*) FROM subscriptions
                    WHERE (user_id = ? AND is_active = 1 AND expires_at > ?)
                       OR (id = ? AND user_id = ? AND is_active = 1)
                    """,
                    (user_id, now_ts, subscription_id, user_id)
                ) as cursor:
                    has_active_subscription = (await cursor.fetchone())[0] > 0
            
            for server in v2ray_servers_raw:
                server_access_level = server[8] if len(server) > 8 else 'all'
                if server_access_level == 'all':
                    v2ray_servers.append(server[:8])  # Без access_level
                elif server_access_level == 'vip' and is_vip:
                    v2ray_servers.append(server[:8])
                elif server_access_level == 'paid' and (is_vip or has_active_subscription):
                    v2ray_servers.append(server[:8])
            
            # Получаем Outline сервер (приоритет серверу с id=8) с проверкой доступности
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    """
                    SELECT id, name, api_url, api_key, domain, v2ray_path, protocol, cert_sha256, COALESCE(access_level, 'all') as access_level
                    FROM servers
                    WHERE active = 1 AND protocol = 'outline'
                    ORDER BY CASE WHEN id = 8 THEN 0 ELSE 1 END, id
                    """
                ) as cursor:
                    outline_servers_raw = await cursor.fetchall()
            
            outline_server_row = None
            for server in outline_servers_raw:
                server_access_level = server[8] if len(server) > 8 else 'all'
                if server_access_level == 'all':
                    outline_server_row = server[:8]
                    break
                elif server_access_level == 'vip' and is_vip:
                    outline_server_row = server[:8]
                    break
                elif server_access_level == 'paid' and (is_vip or has_active_subscription):
                    outline_server_row = server[:8]
                    break
            
            created_keys = 0
            failed_servers = []
            
            # ОПТИМИЗАЦИЯ: Создаем пул клиентов для переиспользования соединений
            client_pool = ServerClientPool()
            
            try:
                # ОПТИМИЗАЦИЯ: Создаем ключи на всех V2Ray серверах параллельно
                if v2ray_servers:
                    v2ray_tasks = [
                        self._create_single_v2ray_key(server_info, subscription_id, user_id, tariff, now, client_pool)
                        for server_info in v2ray_servers
                    ]
                    v2ray_results = await asyncio.gather(*v2ray_tasks, return_exceptions=True)
                else:
                    v2ray_results = []
                
                for result in v2ray_results:
                    if isinstance(result, Exception):
                        logger.error(
                            f"[SUBSCRIPTION] Exception in parallel v2ray key creation: {result}",
                            exc_info=True
                        )
                        # Не можем определить server_id из исключения, пропускаем
                    elif isinstance(result, tuple):
                        success, failed_server_id = result
                        if success:
                            created_keys += 1
                        elif failed_server_id:
                            failed_servers.append(failed_server_id)
            
                # Создаем один Outline ключ (если есть доступный Outline сервер)
                if outline_server_row:
                    success, failed_server_id = await self._create_single_outline_key(
                        outline_server_row, subscription_id, user_id, tariff, now, client_pool
                    )
                    if success:
                        created_keys += 1
                    elif failed_server_id:
                        failed_servers.append(failed_server_id)
                else:
                    logger.info(
                        f"[SUBSCRIPTION] No active outline server found for subscription {subscription_id}, skipping outline key creation"
                    )
            finally:
                # Закрываем все клиенты из пула
                await client_pool.close_all()
            
            logger.info(
                f"[SUBSCRIPTION] Created keys for subscription {subscription_id}: "
                f"{created_keys} keys created, {len(failed_servers)} failed"
            )
            
            return created_keys, failed_servers
            
        except Exception as e:
            logger.error(
                f"[SUBSCRIPTION] Error creating keys for subscription {subscription_id}: {e}",
                exc_info=True
            )
            return 0, failed_servers
    
    async def _recalculate_and_update_subscription_expires_at(
        self,
        subscription_id: int,
        payments: List[Payment],
        subscription_created_at: int,
        user_id: int
    ) -> int:
        """
        Атомарно пересчитать и обновить expires_at подписки.
        
        Использует SQL-транзакцию для предотвращения race conditions.
        Возвращает новое значение expires_at.
        
        ВАЖНО: Также обновляет tariff_id из последнего платежа и traffic_limit_mb из тарифа.
        """
        new_expires_at = self._calculate_subscription_expires_at(
            payments,
            subscription_created_at,
            user_id
        )
        
        # Получаем tariff_id из последнего платежа
        last_payment = payments[-1] if payments else None
        tariff_id_from_payment = last_payment.tariff_id if last_payment else None
        
        # Получаем лимит трафика из тарифа
        traffic_limit_mb = 0
        if tariff_id_from_payment:
            tariff_row = self.tariff_repo.get_tariff(tariff_id_from_payment)
            if tariff_row and len(tariff_row) > 4:
                traffic_limit_mb = tariff_row[4] or 0
        
        # Атомарное обновление через SQL
        async with open_async_connection(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """
                    UPDATE subscriptions
                    SET expires_at = ?, last_updated_at = ?, tariff_id = COALESCE(?, (
                        SELECT tariff_id FROM payments
                        WHERE subscription_id = ? AND status = 'completed'
                        ORDER BY created_at DESC
                        LIMIT 1
                    ))
                    WHERE id = ?
                    """,
                    (new_expires_at, int(time.time()), tariff_id_from_payment, subscription_id, subscription_id)
                )
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                raise e
        
        # Обновляем лимит трафика безопасно (сохраняем реферальный бонус)
        if traffic_limit_mb > 0:
            await self._update_subscription_traffic_limit_safe(subscription_id, traffic_limit_mb)
        
        return new_expires_at
    
    async def _update_subscription_traffic_limit_safe(
        self,
        subscription_id: int,
        tariff_limit_mb: int
    ) -> None:
        """
        Безопасно обновить лимит трафика подписки, сохраняя реферальный бонус.
        
        Если текущий лимит больше лимита тарифа, это может быть реферальный бонус - сохраняем его.
        """
        # Получаем текущий лимит подписки
        async with open_async_connection(self.db_path) as conn:
            async with conn.execute(
                "SELECT traffic_limit_mb FROM subscriptions WHERE id = ?",
                (subscription_id,)
            ) as cursor:
                row = await cursor.fetchone()
                current_limit_mb = row[0] if row and row[0] is not None else None
        
        # Определяем новый лимит
        if current_limit_mb is None:
            # Лимит не установлен - используем лимит из тарифа
            new_limit_mb = tariff_limit_mb
        elif current_limit_mb > tariff_limit_mb and tariff_limit_mb > 0:
            # Текущий лимит больше лимита тарифа - возможно, это реферальный бонус
            # Сохраняем текущий лимит, чтобы не потерять бонус
            new_limit_mb = current_limit_mb
            logger.info(
                f"[SUBSCRIPTION] Keeping current traffic limit {current_limit_mb} MB for subscription {subscription_id} "
                f"(includes bonus, tariff limit is {tariff_limit_mb} MB)"
            )
        elif current_limit_mb == 0:
            # Безлимит (0) - сохраняем безлимит
            new_limit_mb = 0
        else:
            # Текущий лимит <= лимит тарифа - обновляем из тарифа
            # ВАЖНО: Если текущий лимит меньше лимита тарифа, это может быть ошибка - обновляем
            new_limit_mb = tariff_limit_mb
            if current_limit_mb is not None and current_limit_mb < tariff_limit_mb:
                logger.info(
                    f"[SUBSCRIPTION] Updating traffic limit for subscription {subscription_id} "
                    f"from {current_limit_mb} MB to {tariff_limit_mb} MB (current < tariff)"
                )
        
        await self.subscription_repo.update_subscription_traffic_limit_async(subscription_id, new_limit_mb)
        logger.info(
            f"[SUBSCRIPTION] Updated subscription {subscription_id} traffic_limit_mb to {new_limit_mb} MB "
            f"(tariff limit: {tariff_limit_mb} MB, previous: {current_limit_mb} MB)"
        )
    
    async def _update_subscription_expires_at(
        self,
        subscription_id: int,
        new_expires_at: int,
        tariff_id: int,
        traffic_limit_mb: int = 0
    ) -> None:
        """
        Атомарно обновить expires_at, tariff_id и traffic_limit_mb подписки (для продления).
        
        Используется при продлении существующей подписки - обновляет expires_at и лимит трафика.
        ВАЖНО: Использует безопасное обновление лимита, сохраняя реферальный бонус.
        """
        # Безопасно обновляем лимит трафика (сохраняем реферальный бонус)
        await self._update_subscription_traffic_limit_safe(subscription_id, traffic_limit_mb)
        
        # Обновляем expires_at и tariff_id
        async with open_async_connection(self.db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    """
                    UPDATE subscriptions
                    SET expires_at = ?, last_updated_at = ?, tariff_id = ?
                    WHERE id = ?
                    """,
                    (new_expires_at, int(time.time()), tariff_id, subscription_id)
                )
                await conn.commit()
                logger.info(
                    f"[SUBSCRIPTION] Updated subscription {subscription_id}: "
                    f"expires_at={new_expires_at}, tariff_id={tariff_id}"
                )
            except Exception as e:
                await conn.rollback()
                raise e
    
    async def _send_universal_notification(
        self,
        payment: Payment,
        subscription_row: Tuple,
        tariff: Dict[str, Any],
        new_expires_at: int,
        was_created: bool,
        subscription_id: int
    ) -> bool:
        """
        Отправить универсальное уведомление о подписке (для покупки и продления).
        
        Текст уведомления одинаковый для всех случаев:
        "Подписка обновлена!" + новый срок действия
        """
        try:
            subscription_token = subscription_row[2]
            subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_token}"
            
            # Форматируем дату истечения
            expiry_date_str = datetime.fromtimestamp(new_expires_at).strftime('%Y-%m-%d %H:%M:%S')
            
            # Универсальное уведомление
            msg = (
                f"✅ *Подписка обновлена!*\n\n"
                f"🔗 *Ссылка (коснитесь, чтобы скопировать):*\n"
                f"`{subscription_url}`\n\n"
                f"⏳ *Срок действия:* до {expiry_date_str}\n\n"
                f"💡 Подписка автоматически обновится в вашем приложении"
            )
            
            logger.info(
                f"[SUBSCRIPTION] Sending universal notification to user {payment.user_id} "
                f"for subscription {subscription_id}, was_created={was_created}"
            )
            
            notification_sent = await self._send_notification_simple(payment.user_id, msg)
            
            if not notification_sent:
                logger.warning(
                    f"[SUBSCRIPTION] Failed to send universal notification to user {payment.user_id} "
                    f"for subscription {subscription_id}"
                )
                return False
            
            # Отправляем Outline ключи только при первом платеже (payments_count == 1)
            if was_created:
                # Получаем количество completed платежей для подписки
                async with open_async_connection(self.db_path) as conn:
                    async with conn.execute(
                        """
                        SELECT COUNT(*) FROM payments
                        WHERE subscription_id = ? AND status = 'completed'
                        """,
                        (subscription_id,)
                    ) as cursor:
                        payments_count = (await cursor.fetchone())[0] or 0
                
                # Отправляем Outline ключи только при первом платеже
                if payments_count == 1:
                    await self._send_outline_backup_keys_notification(
                        payment.user_id,
                        subscription_id,
                        tariff,
                        new_expires_at
                    )
            
            return True
            
        except Exception as e:
            logger.error(
                f"[SUBSCRIPTION] Error sending universal notification to user {payment.user_id}: {e}",
                exc_info=True
            )
            return False
    
    async def _send_outline_backup_keys_notification(
        self,
        user_id: int,
        subscription_id: int,
        tariff: Dict[str, Any],
        expires_at: int
    ) -> None:
        """
        Отправить уведомление о запасных Outline ключах (только при первом платеже).
        """
        try:
            now = int(time.time())
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
                outline_msg = "🎁 *Также мы подготовили для вас запасные Outline ключи (потребуется скачать другое приложение):*\n\n"
                
                for access_url, key_expiry_at, country in outline_keys:
                    remaining_time = key_expiry_at - now
                    country_text = f"🌍 *Страна:* {country}\n" if country else ""
                    outline_msg += (
                        f"{country_text}"
                        f"{format_key_message_unified(access_url, 'outline', tariff, remaining_time)}\n\n"
                    )
                
                outline_notification_sent = await self._send_notification_simple(user_id, outline_msg)
                if outline_notification_sent:
                    logger.info(
                        f"[SUBSCRIPTION] Outline backup keys notification sent to user {user_id} "
                        f"for subscription {subscription_id}"
                    )
                else:
                    logger.warning(
                        f"[SUBSCRIPTION] Failed to send Outline backup keys notification to user {user_id} "
                        f"for subscription {subscription_id}"
                    )
        except Exception as e:
            logger.error(
                f"[SUBSCRIPTION] Error sending Outline backup keys notification for subscription {subscription_id}: {e}",
                exc_info=True
            )
    
    async def _send_notification_simple(self, user_id: int, message: str) -> bool:
        """
        Отправить уведомление пользователю - использует safe_send_message с встроенным retry.
        Если get_bot_instance() is None (вебхуки в процессе админки), отправка через Telegram API.
        """
        try:
            bot = get_bot_instance()
            if bot:
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
                logger.warning(f"[SUBSCRIPTION] Failed to send notification to user {user_id} after retries")
                return False

            token = app_settings.TELEGRAM_BOT_TOKEN
            if not token:
                logger.warning(f"[SUBSCRIPTION] No bot token for API fallback, cannot send to user {user_id}")
                return False
            logger.info("[SUBSCRIPTION] Sending user notification via Telegram API (bot not available)")
            return await _send_via_telegram_api(
                token,
                user_id,
                message,
                parse_mode="Markdown",
                disable_web_page_preview=True,
                max_retries=3,
                reply_markup=_MAIN_MENU_REPLY_MARKUP,
            )
        except Exception as e:
            logger.error(f"[SUBSCRIPTION] Error sending notification to user {user_id}: {e}", exc_info=True)
            return False
    
    async def _send_admin_purchase_notification(
        self,
        payment: Payment,
        subscription_id: int,
        tariff: Dict[str, Any],
        expires_at: int,
        is_new: bool
    ) -> None:
        """
        Отправить уведомление администратору о покупке/продлении подписки.
        Если get_bot_instance() возвращает None (вебхуки в процессе админки),
        отправка идёт через Telegram API напрямую.
        """
        try:
            admin_id = app_settings.ADMIN_ID
            if not admin_id:
                logger.debug("[SUBSCRIPTION] ADMIN_ID not set, skipping admin notification")
                return
            
            token = app_settings.TELEGRAM_BOT_TOKEN
            if not token:
                logger.warning("[SUBSCRIPTION] TELEGRAM_BOT_TOKEN not set, cannot send admin notification")
                return
            
            expires_date = datetime.fromtimestamp(expires_at).strftime("%d.%m.%Y %H:%M")
            payment_method_map = {
                "yookassa": "YooKassa",
                "platega": "Platega",
                "cryptobot": "CryptoBot"
            }
            _m = (payment.method.value if payment.method else "") or ""
            payment_method = payment_method_map.get(_m.lower(), _m or "—")
            purchase_type = "новая" if is_new else "продление"
            message = (
                f"💳 *Покупка подписки*\n\n"
                f"👤 Пользователь: `{payment.user_id}`\n"
                f"📋 Подписка: #{subscription_id}\n"
                f"📦 Тариф: {tariff.get('name', 'N/A')}\n"
                f"💰 Сумма: {payment.amount} ₽\n"
                f"💳 Способ оплаты: {payment_method}\n"
                f"📅 Действует до: {expires_date}\n"
                f"🔄 Тип: {purchase_type}\n"
                f"🧾 Платеж: `{payment.payment_id}`"
            )
            message_plain = (
                f"💳 Покупка подписки\n\n"
                f"👤 Пользователь: {payment.user_id}\n"
                f"📋 Подписка: #{subscription_id}\n"
                f"📦 Тариф: {tariff.get('name', 'N/A')}\n"
                f"💰 Сумма: {payment.amount} ₽\n"
                f"💳 Способ оплаты: {payment_method}\n"
                f"📅 Действует до: {expires_date}\n"
                f"🔄 Тип: {purchase_type}\n"
                f"🧾 Платеж: {payment.payment_id}"
            )

            bot = get_bot_instance()
            if bot:
                await safe_send_message(
                    bot,
                    admin_id,
                    message,
                    parse_mode="Markdown",
                    mark_blocked=False
                )
                logger.info(f"[SUBSCRIPTION] Admin notification sent for payment {payment.payment_id} (via bot instance)")
                return

            logger.info(
                "[SUBSCRIPTION] Sending admin purchase notification via Telegram API (bot not available), admin_id=%s, payment_id=%s",
                admin_id,
                payment.payment_id,
            )
            ok = await _send_via_telegram_api(
                token, admin_id, message_plain, parse_mode=""
            )
            if ok:
                logger.info(f"[SUBSCRIPTION] Admin notification sent for payment {payment.payment_id} (via Telegram API)")
            else:
                logger.error(
                    f"[SUBSCRIPTION] Failed to send admin notification for payment {payment.payment_id} after retries"
                )
        except Exception as e:
            logger.error(f"[SUBSCRIPTION] Error sending admin notification: {e}", exc_info=True)
