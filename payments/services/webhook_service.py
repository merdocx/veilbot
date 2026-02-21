import json
import logging
from typing import Dict, Any, Optional
from fastapi import Request, HTTPException
from yookassa.domain.common import SecurityHelper

from ..repositories.payment_repository import PaymentRepository
from ..services.payment_service import PaymentService
from ..services.subscription_purchase_service import SubscriptionPurchaseService
from ..models.payment import PaymentStatus

logger = logging.getLogger(__name__)


class WebhookService:
    """Сервис для обработки webhook'ов от платежных систем"""
    
    def __init__(
        self,
        payment_repo: PaymentRepository,
        payment_service: PaymentService,
        webhook_secret: Optional[str] = None
    ):
        self.payment_repo = payment_repo
        self.payment_service = payment_service
        self.webhook_secret = webhook_secret
    
    async def handle_yookassa_webhook(self, data: Dict[str, Any]) -> bool:
        """
        Обработка webhook от YooKassa
        
        Args:
            data: Данные webhook'а
            
        Returns:
            True если обработка успешна
        """
        try:
            logger.info(f"Processing YooKassa webhook: {data}")
            
            # Извлекаем данные платежа
            payment_object = data.get("object", {})
            payment_id = payment_object.get("id")
            status = payment_object.get("status")
            
            if not payment_id:
                logger.error("No payment ID in webhook data")
                return False
            
            if status == "succeeded":
                # Обрабатываем успешный платеж
                # КРИТИЧНО: Используем атомарное обновление статуса для предотвращения race conditions
                # Сначала пытаемся атомарно обновить статус с pending -> paid
                from ..models.payment import PaymentStatus
                
                # Получаем текущий статус платежа
                payment = await self.payment_repo.get_by_payment_id(payment_id)
                if not payment:
                    logger.error(f"Payment {payment_id} not found in database")
                    return False
                
                # Используем атомарное обновление для предотвращения параллельной обработки
                # Обновляем статус только если он еще pending
                if payment.status == PaymentStatus.PENDING:
                    # Атомарно обновляем статус
                    atomic_success = await self.payment_repo.try_update_status(
                        payment_id, 
                        PaymentStatus.PAID, 
                        PaymentStatus.PENDING
                    )
                    if not atomic_success:
                        # Статус уже изменился другим процессом
                        logger.info(f"Payment {payment_id} already processed by another process")
                        # Проверяем, был ли платеж успешно обработан
                        updated_payment = await self.payment_repo.get_by_payment_id(payment_id)
                        if updated_payment and updated_payment.status == PaymentStatus.COMPLETED:
                            logger.info(f"Payment {payment_id} already completed, skipping")
                            return True
                        return False
                
                # Теперь обрабатываем платеж
                success = await self.payment_service.process_payment_success(payment_id)
                if success:
                    logger.info(f"Payment {payment_id} processed successfully via webhook")
                    
                    # Проверяем, является ли это платежом за подписку
                    payment = await self.payment_repo.get_by_payment_id(payment_id)
                    if payment and payment.metadata and payment.metadata.get('key_type') == 'subscription' and payment.protocol == 'v2ray':
                        # КРИТИЧНО: Проверяем статус перед обработкой (защита от повторной обработки)
                        # YooKassa может отправить несколько webhook'ов для одного платежа
                        if payment.status == PaymentStatus.COMPLETED:
                            logger.info(
                                f"Payment {payment_id} already completed, skipping subscription processing. "
                                f"This webhook was likely a duplicate."
                            )
                        else:
                            # КРИТИЧНО: Обрабатываем подписку СРАЗУ при получении webhook'а
                            # Уведомление должно быть отправлено немедленно, без откладывания на фоновые задачи
                            subscription_service = SubscriptionPurchaseService()
                            success, error_msg = await subscription_service.process_subscription_purchase(payment_id)
                            
                            if success:
                                logger.info(f"Subscription purchase processed successfully for payment {payment_id} via webhook, notification sent")
                            else:
                                # Если обработка не удалась, логируем ошибку, но не блокируем webhook
                                # Платеж уже помечен как paid, обработка будет повторена при следующем webhook'е или проверке статуса
                                logger.error(
                                    f"CRITICAL: Failed to process subscription purchase for payment {payment_id} via webhook: {error_msg}. "
                                    f"Will retry on next webhook or status check."
                                )
                                # НЕ возвращаем False, т.к. платеж уже помечен как paid
                                # YooKassa может повторно отправить webhook, и мы попробуем обработать снова
                        
                        # НЕ вызываем process_paid_payments_without_keys для подписок
                        # Это предотвращает дублирование обработки
                    else:
                        # Для обычных платежей (не подписок) используем старую логику
                        try:
                            await self.payment_service.process_paid_payments_without_keys()
                            logger.info(f"Key creation triggered for payment {payment_id} via webhook")
                        except Exception as e:
                            logger.error(f"Error creating key for payment {payment_id} via webhook: {e}")
                            # Не возвращаем False, т.к. платеж уже обработан
                            # Ключ будет создан фоновой задачей
                    
                    return True
                else:
                    logger.error(f"Failed to process payment {payment_id} via webhook")
                    return False
            elif status in ["canceled", "failed"]:
                # Помечаем как неудачный
                await self.payment_repo.update_status(payment_id, PaymentStatus.FAILED)
                logger.info(f"Payment {payment_id} marked as failed via webhook")
                return True
            else:
                logger.info(f"Payment {payment_id} status: {status} (no action needed)")
                return True
                
        except Exception as e:
            logger.error(f"Error processing YooKassa webhook: {e}")
            return False
    
    async def verify_yookassa_signature(self, body: bytes, signature: str) -> bool:
        """
        Проверка подписи webhook от YooKassa
        
        YooKassa использует IP-whitelist для HTTP уведомлений.
        Дополнительно можно проверить подпись, если она передается в заголовках.
        
        Args:
            body: Тело запроса (не используется для IP-based проверки)
            signature: Подпись из заголовка (опционально)
            
        Returns:
            True если подпись верна или проверка IP прошла успешно
        """
        try:
            # YooKassa HTTP уведомления защищены через IP whitelist
            # Проверка IP выполняется в process_webhook_request через SecurityHelper
            # Если передана подпись в заголовке, можно дополнительно проверить её
            
            if signature and self.webhook_secret:
                # Проверка HMAC подписи если используется custom secret
                import hmac
                import hashlib
                expected_signature = hmac.new(
                    self.webhook_secret.encode('utf-8'),
                    body,
                    hashlib.sha256
                ).hexdigest()
                return hmac.compare_digest(signature, expected_signature)
            
            # Если подпись не передана, полагаемся на IP проверку
            # (которая выполняется в process_webhook_request)
            return True
            
        except Exception as e:
            logger.error(f"Error verifying YooKassa signature: {e}")
            return False
    
    async def handle_stripe_webhook(self, data: Dict[str, Any]) -> bool:
        """
        Обработка webhook от Stripe (для будущего расширения)
        
        Args:
            data: Данные webhook'а
            
        Returns:
            True если обработка успешна
        """
        try:
            logger.info(f"Processing Stripe webhook: {data}")
            # Реализация для Stripe
            return True
            
        except Exception as e:
            logger.error(f"Error processing Stripe webhook: {e}")
            return False
    
    async def handle_paypal_webhook(self, data: Dict[str, Any]) -> bool:
        """
        Обработка webhook от PayPal (для будущего расширения)
        
        Args:
            data: Данные webhook'а
            
        Returns:
            True если обработка успешна
        """
        try:
            logger.info(f"Processing PayPal webhook: {data}")
            # Реализация для PayPal
            return True
            
        except Exception as e:
            logger.error(f"Error processing PayPal webhook: {e}")
            return False
    
    async def handle_platega_webhook(self, data: Dict[str, Any]) -> bool:
        """
        Обработка webhook от Platega
        """
        try:
            if not data:
                logger.error("Empty Platega webhook payload")
                return False

            if not self.payment_service.platega_service:
                logger.error("Platega service not configured, cannot process webhook")
                return False

            tx_id, status = await self.payment_service.platega_service.parse_webhook(data)
            if not tx_id:
                logger.error("No transactionId in Platega webhook")
                return False

            payment = await self.payment_repo.get_by_payment_id(tx_id)
            if not payment:
                logger.error(f"Payment {tx_id} not found for Platega webhook")
                return False

            paid = self.payment_service.platega_service.is_paid_status(status)
            if paid:
                # Атомарно переводим в PAID, если еще pending
                if payment.status == PaymentStatus.PENDING:
                    atomic_success = await self.payment_repo.try_update_status(
                        tx_id, PaymentStatus.PAID, PaymentStatus.PENDING
                    )
                    if not atomic_success:
                        # Статус уже изменился другим процессом
                        logger.info(f"Payment {tx_id} already processed by another process")
                        # Проверяем, был ли платеж успешно обработан
                        updated_payment = await self.payment_repo.get_by_payment_id(tx_id)
                        if updated_payment and updated_payment.status == PaymentStatus.COMPLETED:
                            logger.info(f"Payment {tx_id} already completed, skipping")
                            return True
                        return False

                success = await self.payment_service.process_payment_success(tx_id)
                if success:
                    # Запускаем обработку ключей/подписок по аналогии с YooKassa
                    # КРИТИЧНО: Проверяем статус перед обработкой подписки (защита от повторной обработки)
                    # Platega может отправить несколько webhook'ов для одного платежа
                    payment_check = await self.payment_repo.get_by_payment_id(tx_id)
                    if (
                        payment_check
                        and payment_check.metadata
                        and payment_check.metadata.get("key_type") == "subscription"
                        and payment_check.protocol == "v2ray"
                    ):
                        # Проверяем, не обработан ли уже платеж
                        if payment_check.status == PaymentStatus.COMPLETED:
                            logger.info(
                                f"Payment {tx_id} already completed, skipping subscription processing via Platega webhook. "
                                f"This webhook was likely a duplicate."
                            )
                        else:
                            # КРИТИЧНО: Обрабатываем подписку СРАЗУ при получении webhook'а
                            subscription_service = SubscriptionPurchaseService()
                            ok, error_msg = await subscription_service.process_subscription_purchase(tx_id)
                            if ok:
                                logger.info(f"Subscription purchase processed successfully for payment {tx_id} via Platega webhook, notification sent")
                            else:
                                # Если обработка не удалась, логируем ошибку, но не блокируем webhook
                                # Платеж уже помечен как paid, обработка будет повторена при следующем webhook'е или проверке статуса
                                logger.error(
                                    f"CRITICAL: Failed to process subscription purchase for payment {tx_id} via Platega webhook: {error_msg}. "
                                    f"Will retry on next webhook or status check."
                                )
                        # НЕ вызываем process_paid_payments_without_keys для подписок
                        # Это предотвращает дублирование обработки
                    else:
                        # Для обычных платежей (не подписок) используем старую логику
                        try:
                            await self.payment_service.process_paid_payments_without_keys()
                            logger.info(f"Key creation triggered for payment {tx_id} via Platega webhook")
                        except Exception as e:
                            logger.error(f"Error creating key for payment {tx_id} via Platega webhook: {e}")
                            # Не возвращаем False, т.к. платеж уже обработан
                            # Ключ будет создан фоновой задачей
                    return True

            # Обработка неуспешных статусов
            if status and str(status).lower() in {"failed", "canceled", "cancelled", "expired"}:
                await self.payment_repo.update_status(tx_id, PaymentStatus.FAILED)
                logger.info(f"Payment {tx_id} marked as failed via Platega webhook, status={status}")
                return True

            logger.info(f"Platega webhook processed without status change for {tx_id}, status={status}")
            return True
        except Exception as e:
            logger.error(f"Error processing Platega webhook: {e}", exc_info=True)
            return False
    
    async def process_webhook_request(self, request: Request, provider: str) -> Dict[str, Any]:
        """
        Обработка webhook запроса
        
        Args:
            request: FastAPI request объект
            provider: Провайдер платежной системы
            
        Returns:
            Результат обработки
        """
        try:
            # Получаем тело запроса
            body = await request.body()

            # Безопасность: проверка источника ЮKassa
            if provider.lower() == "yookassa":
                client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
                    request.client.host if request.client else ""
                )
                ip_trusted = False
                try:
                    ip_trusted = SecurityHelper().is_ip_trusted(client_ip)
                except Exception:
                    ip_trusted = False

                # Альтернатива: кастомный секрет через заголовок
                secret_header = request.headers.get("X-Webhook-Secret") or request.headers.get(
                    "X-YooKassa-Webhook-Secret"
                )
                has_valid_secret = bool(self.webhook_secret and secret_header == self.webhook_secret)

                if not (ip_trusted or has_valid_secret):
                    logger.error(f"Untrusted webhook source: ip={client_ip}")
                    raise HTTPException(status_code=403, detail="Untrusted webhook source")
            
            # Парсим JSON
            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in webhook: {e}")
                raise HTTPException(status_code=400, detail="Invalid JSON")
            
            # Обрабатываем в зависимости от провайдера
            if provider.lower() == "yookassa":
                success = await self.handle_yookassa_webhook(data)
            elif provider.lower() == "platega":
                success = await self.handle_platega_webhook(data)
            elif provider.lower() == "stripe":
                success = await self.handle_stripe_webhook(data)
            elif provider.lower() == "paypal":
                success = await self.handle_paypal_webhook(data)
            else:
                logger.error(f"Unknown payment provider: {provider}")
                raise HTTPException(status_code=400, detail="Unknown provider")
            
            if success:
                return {"status": "ok", "processed": True}
            else:
                return {"status": "error", "processed": False}
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing webhook request: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")
    
    async def get_webhook_logs(self, limit: int = 100) -> list:
        """
        Получение логов webhook'ов (для админки)
        
        Args:
            limit: Лимит записей
            
        Returns:
            Список логов
        """
        try:
            # В реальной реализации здесь должна быть работа с логами
            # Пока что возвращаем пустой список
            logger.info(f"Getting webhook logs (limit: {limit})")
            return []
            
        except Exception as e:
            logger.error(f"Error getting webhook logs: {e}")
            return []
