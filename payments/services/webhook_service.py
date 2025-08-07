import json
import logging
from typing import Dict, Any, Optional
from fastapi import Request, HTTPException

from ..repositories.payment_repository import PaymentRepository
from ..services.payment_service import PaymentService
from ..models.payment import PaymentStatus

logger = logging.getLogger(__name__)


class WebhookService:
    """Сервис для обработки webhook'ов от платежных систем"""
    
    def __init__(
        self,
        payment_repo: PaymentRepository,
        payment_service: PaymentService
    ):
        self.payment_repo = payment_repo
        self.payment_service = payment_service
    
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
                success = await self.payment_service.process_payment_success(payment_id)
                if success:
                    logger.info(f"Payment {payment_id} processed successfully via webhook")
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
        
        Args:
            body: Тело запроса
            signature: Подпись из заголовка
            
        Returns:
            True если подпись верна
        """
        try:
            # В реальной реализации здесь должна быть проверка подписи
            # Пока что возвращаем True для совместимости
            logger.info("YooKassa signature verification (placeholder)")
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
            
            # Парсим JSON
            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in webhook: {e}")
                raise HTTPException(status_code=400, detail="Invalid JSON")
            
            # Обрабатываем в зависимости от провайдера
            if provider.lower() == "yookassa":
                success = await self.handle_yookassa_webhook(data)
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
