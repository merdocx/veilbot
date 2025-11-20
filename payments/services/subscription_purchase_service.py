"""
Единый сервис для обработки покупки подписки V2Ray
Обрабатывает весь процесс атомарно: проверка платежа -> создание/продление подписки -> создание ключей -> уведомление -> завершение
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
from app.infra.sqlite_utils import open_connection, open_async_connection
from app.settings import settings as app_settings
from vpn_protocols import ProtocolFactory, format_duration
from bot.core import get_bot_instance
from bot.utils import safe_send_message
from bot.keyboards import get_main_menu
from app.infra.foreign_keys import safe_foreign_keys_off

logger = logging.getLogger(__name__)


class SubscriptionPurchaseService:
    """Сервис для атомарной обработки покупки подписки"""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or app_settings.DATABASE_PATH
        self.payment_repo = PaymentRepository(db_path)
        self.subscription_repo = SubscriptionRepository(db_path)
        self.tariff_repo = TariffRepository(db_path)
    
    async def process_subscription_purchase(self, payment_id: str) -> Tuple[bool, Optional[str]]:
        """
        Обработать покупку подписки для оплаченного платежа
        
        Args:
            payment_id: ID платежа в YooKassa
            
        Returns:
            Tuple[success, error_message]
            success: True если подписка успешно создана/продлена
            error_message: Сообщение об ошибке или None при успехе
        """
        try:
            logger.info(f"Processing subscription purchase for payment {payment_id}")
            
            # Шаг 1: Получаем платеж из БД
            payment = await self.payment_repo.get_by_payment_id(payment_id)
            if not payment:
                error_msg = f"Payment {payment_id} not found"
                logger.error(error_msg)
                return False, error_msg
            
            # Шаг 2: Проверяем, что это платеж за подписку
            if not (payment.metadata and payment.metadata.get('key_type') == 'subscription'):
                error_msg = f"Payment {payment_id} is not a subscription payment"
                logger.warning(error_msg)
                return False, error_msg
            
            if payment.protocol != 'v2ray':
                error_msg = f"Payment {payment_id} protocol is not v2ray"
                logger.warning(error_msg)
                return False, error_msg
            
            # Шаг 3: Проверяем статус платежа и защита от повторной обработки
            if payment.status == PaymentStatus.COMPLETED:
                logger.info(f"Payment {payment_id} already completed, skipping")
                return True, None
            
            # Защита от одновременной обработки: проверяем флаг обработки в metadata
            if payment.metadata and payment.metadata.get('_processing_subscription'):
                logger.warning(f"Payment {payment_id} is already being processed, skipping")
                return True, None
            
            # Устанавливаем флаг обработки атомарно через обновление платежа
            if not payment.metadata:
                payment.metadata = {}
            payment.metadata['_processing_subscription'] = True
            await self.payment_repo.update(payment)
            
            # Дополнительная проверка после установки флага (на случай параллельного вызова)
            payment_check = await self.payment_repo.get_by_payment_id(payment_id)
            if payment_check and payment_check.status == PaymentStatus.COMPLETED:
                logger.info(f"Payment {payment_id} was completed by another process, skipping")
                return True, None
            
            if payment.status != PaymentStatus.PAID:
                # Обновляем статус на paid, если он еще не установлен
                if payment.paid_at is None:
                    payment.mark_as_paid()
                    await self.payment_repo.update(payment)
                    logger.info(f"Payment {payment_id} marked as paid")
            
            # Шаг 4: Получаем тариф
            tariff_row = self.tariff_repo.get_tariff(payment.tariff_id)
            if not tariff_row:
                error_msg = f"Tariff {payment.tariff_id} not found for payment {payment_id}"
                logger.error(error_msg)
                return False, error_msg
            
            tariff = {
                'id': tariff_row[0],
                'name': tariff_row[1],
                'duration_sec': tariff_row[2],
                'price_rub': tariff_row[3],
                'traffic_limit_mb': tariff_row[4] if len(tariff_row) > 4 else 0,
            }
            
            logger.info(
                f"Processing subscription purchase: payment={payment_id}, "
                f"user={payment.user_id}, tariff={tariff['name']}, duration={tariff['duration_sec']}s"
            )
            
            # Шаг 5: Проверяем существующую подписку
            now = int(time.time())
            existing_subscription = await self.subscription_repo.get_active_subscription_async(payment.user_id)
            
            if existing_subscription:
                # Продлеваем существующую подписку
                subscription_id = existing_subscription[0]
                subscription_token = existing_subscription[2]
                existing_expires_at = existing_subscription[4]
                new_expires_at = existing_expires_at + tariff['duration_sec']
                
                logger.info(
                    f"Extending existing subscription {subscription_id} for user {payment.user_id}: "
                    f"{existing_expires_at} -> {new_expires_at} (+{tariff['duration_sec']}s)"
                )
                
                # Обновляем подписку
                await self.subscription_repo.extend_subscription_async(subscription_id, new_expires_at)
                
                # Продлеваем все ключи подписки
                async with open_async_connection(self.db_path) as conn:
                    cursor = await conn.execute(
                        """
                        UPDATE v2ray_keys 
                        SET expiry_at = ? 
                        WHERE subscription_id = ? AND expiry_at > ?
                        """,
                        (new_expires_at, subscription_id, now)
                    )
                    keys_extended = cursor.rowcount
                    await conn.commit()
                
                logger.info(
                    f"Extended {keys_extended} keys for subscription {subscription_id}"
                )
                
                # Отправляем финальное уведомление пользователю
                subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_token}"
                msg = (
                    f"✅ *Подписка V2Ray успешно продлена!*\n\n"
                    f"🔗 *Ссылка подписки:*\n"
                    f"`{subscription_url}`\n\n"
                    f"⏳ *Добавлено времени:* {format_duration(tariff['duration_sec'])}\n"
                    f"📅 *Новый срок действия:* до <code>{datetime.fromtimestamp(new_expires_at).strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
                    f"💡 Подписка автоматически обновится в вашем приложении V2Ray"
                )
                
                notification_sent = await self._send_notification(payment.user_id, msg)
                
                # Обновляем флаг отправки уведомления в подписке
                if notification_sent:
                    await self.subscription_repo.mark_purchase_notification_sent_async(subscription_id)
                else:
                    logger.warning(
                        f"Failed to send purchase notification for subscription {subscription_id}, "
                        f"user {payment.user_id}. Will retry via background task."
                    )
                
                # Помечаем платеж как completed и удаляем флаг обработки
                payment.mark_as_completed()
                if payment.metadata:
                    payment.metadata.pop('_processing_subscription', None)
                await self.payment_repo.update(payment)
                
                logger.info(
                    f"Subscription {subscription_id} extended successfully for payment {payment_id}"
                )
                return True, None
            
            # Шаг 6: Создаем новую подписку
            expires_at = now + tariff['duration_sec']
            
            # Генерируем уникальный токен
            subscription_token = None
            for _ in range(10):
                token = str(uuid.uuid4())
                if not await self.subscription_repo.get_subscription_by_token_async(token):
                    subscription_token = token
                    break
            
            if not subscription_token:
                error_msg = f"Failed to generate unique subscription token after 10 attempts"
                logger.error(error_msg)
                return False, error_msg
            
            # Создаем подписку в БД
            subscription_id = await self.subscription_repo.create_subscription_async(
                user_id=payment.user_id,
                subscription_token=subscription_token,
                expires_at=expires_at,
                tariff_id=tariff['id'],
            )
            
            logger.info(
                f"Created subscription {subscription_id} for user {payment.user_id}, "
                f"expires_at={expires_at}"
            )
            
            # Шаг 7: Создаем ключи на всех активных V2Ray серверах
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
                server_config = None
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
                        # Отключаем проверку внешних ключей для этой операции
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
                        f"Created and saved key for subscription {subscription_id} on server {server_id} ({server_name}), key_id={v2ray_uuid[:8]}"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"Failed to create key for subscription {subscription_id} "
                        f"on server {server_id} ({server_name}): {e}",
                        exc_info=True,
                    )
                    # Если ключ был создан на сервере, но не сохранен в БД - пытаемся удалить его с сервера
                    if v2ray_uuid and protocol_client:
                        try:
                            await protocol_client.delete_user(v2ray_uuid)
                            logger.info(f"Cleaned up orphaned key on server {server_id} ({server_name})")
                        except Exception as cleanup_error:
                            logger.error(f"Failed to cleanup orphaned key on server {server_id}: {cleanup_error}")
                    failed_servers.append(server_id)
            
            if created_keys == 0:
                error_msg = f"Failed to create any keys for subscription {subscription_id}"
                logger.error(error_msg)
                # Удаляем подписку, если не удалось создать ни одного ключа
                await self.subscription_repo.deactivate_subscription_async(subscription_id)
                # Удаляем флаг обработки при ошибке
                try:
                    payment = await self.payment_repo.get_by_payment_id(payment_id)
                    if payment and payment.metadata:
                        payment.metadata.pop('_processing_subscription', None)
                        await self.payment_repo.update(payment)
                except Exception as cleanup_error:
                    logger.warning(f"Failed to cleanup processing flag for payment {payment_id}: {cleanup_error}")
                return False, error_msg
            
            logger.info(
                f"Created subscription {subscription_id} for user {payment.user_id}: "
                f"{created_keys} keys created, {len(failed_servers)} failed"
            )
            
            # Шаг 8: Отправляем уведомление пользователю
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
            
            notification_sent = await self._send_notification(payment.user_id, msg)
            
            # Обновляем флаг отправки уведомления в подписке
            if notification_sent:
                await self.subscription_repo.mark_purchase_notification_sent_async(subscription_id)
            else:
                logger.warning(
                    f"Failed to send purchase notification for subscription {subscription_id}, "
                    f"user {payment.user_id}. Will retry via background task."
                )
            
            # Шаг 9: Помечаем платеж как completed и удаляем флаг обработки
            payment.mark_as_completed()
            if payment.metadata:
                payment.metadata.pop('_processing_subscription', None)
            await self.payment_repo.update(payment)
            
            logger.info(
                f"Subscription purchase completed successfully: payment={payment_id}, "
                f"subscription={subscription_id}, keys={created_keys}, notification_sent={notification_sent}"
            )
            
            return True, None
            
        except Exception as e:
            error_msg = f"Error processing subscription purchase for payment {payment_id}: {e}"
            logger.error(error_msg, exc_info=True)
            # Удаляем флаг обработки при ошибке, чтобы можно было повторить попытку
            try:
                payment = await self.payment_repo.get_by_payment_id(payment_id)
                if payment and payment.metadata:
                    payment.metadata.pop('_processing_subscription', None)
                    await self.payment_repo.update(payment)
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup processing flag for payment {payment_id}: {cleanup_error}")
            return False, error_msg
    
    async def _send_notification(self, user_id: int, message: str, max_retries: int = 3) -> bool:
        """Отправить уведомление пользователю с retry механизмом"""
        import asyncio
        
        for attempt in range(max_retries):
            try:
                bot = get_bot_instance()
                if not bot:
                    logger.warning(f"Bot instance not available for user {user_id}, attempt {attempt + 1}/{max_retries}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)  # Экспоненциальная задержка: 1s, 2s, 4s
                    continue
                
                result = await safe_send_message(
                    bot,
                    user_id,
                    message,
                    reply_markup=get_main_menu(user_id),
                    disable_web_page_preview=True,
                    parse_mode="Markdown"
                )
                
                if result:
                    logger.info(f"Notification sent to user {user_id} on attempt {attempt + 1}")
                    return True
                else:
                    logger.warning(f"Failed to send notification to user {user_id}, attempt {attempt + 1}/{max_retries}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    
            except Exception as e:
                logger.error(f"Error sending notification to user {user_id}, attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        
        logger.error(f"Failed to send notification to user {user_id} after {max_retries} attempts")
        return False

