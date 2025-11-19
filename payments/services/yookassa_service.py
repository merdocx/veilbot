import aiohttp
import json
import base64
import logging
from typing import Tuple, Optional, Dict, Any
from datetime import datetime, timezone
import os

from ..models.payment import Payment
from ..models.enums import PaymentStatus, PaymentMethod, PaymentReceiptType, PaymentVATCode, PaymentMode

logger = logging.getLogger(__name__)


class YooKassaService:
    """Асинхронный сервис для работы с YooKassa API"""
    
    def __init__(self, shop_id: str, api_key: str, return_url: str, test_mode: bool = False):
        self.shop_id = shop_id
        self.api_key = api_key
        self.return_url = return_url
        self.test_mode = test_mode
        
        # Базовый URL API
        self.base_url = "https://api.yookassa.ru/v3"
        if test_mode:
            self.base_url = "https://api.yookassa.ru/v3"  # YooKassa использует один URL для тестов и продакшена
        
        # Заголовки для авторизации
        self.headers = {
            "Authorization": f"Basic {base64.b64encode(f'{shop_id}:{api_key}'.encode()).decode()}",
            "Content-Type": "application/json",
            "Idempotence-Key": ""  # Будет генерироваться для каждого запроса
        }
        
        logger.info(f"YooKassa service initialized: shop_id={shop_id}, test_mode={test_mode}")
    
    def _generate_idempotence_key(self) -> str:
        """Генерация ключа идемпотентности"""
        import uuid
        return str(uuid.uuid4())
    
    async def create_payment(
        self, 
        amount: int, 
        description: str, 
        email: str,
        payment_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Создание платежа в YooKassa
        
        Args:
            amount: Сумма в копейках
            description: Описание платежа
            email: Email для чека
            payment_id: Внешний ID платежа
            metadata: Дополнительные данные
            
        Returns:
            Tuple[payment_id, confirmation_url] или (None, None) при ошибке
        """
        try:
            # Используем переданный ID платежа или генерируем новый
            if not payment_id:
                payment_id = f"payment_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{amount}"
            
            # Формируем данные платежа
            payment_data = {
                "id": payment_id,  # Используем переданный ID
                "amount": {
                    "value": f"{amount / 100:.2f}",  # Конвертируем копейки в рубли
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": self.return_url
                },
                "capture": True,
                "description": description,
                "metadata": metadata or {},
                "receipt": {
                    "customer": {
                        "email": email
                    },
                    "items": [
                        {
                            "description": description,
                            "quantity": 1.0,
                            "amount": {
                                "value": f"{amount / 100:.2f}",
                                "currency": "RUB"
                            },
                            "vat_code": PaymentVATCode.NO_VAT.value,
                            "payment_mode": PaymentMode.FULL_PREPAYMENT.value,
                            "payment_subject": PaymentReceiptType.SERVICE.value
                        }
                    ]
                }
            }
            
            # В тестовом режиме без валидных реквизитов — возвращаем фейковый платеж без обращения к API
            if self.test_mode and (
                os.getenv("PAYMENT_FAKE_MODE", "true").lower() == "true"
                or not self.shop_id
                or not self.api_key
                or self.shop_id in {"123456"}
                or self.api_key in {"test_api_key"}
            ):
                fake_id = payment_id or f"test_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                fake_url = f"https://example.test/confirm/{fake_id}"
                logger.info(f"[TEST_MODE] Returning fake YooKassa payment: {fake_id}")
                return fake_id, fake_url

            # Обновляем заголовки с новым ключом идемпотентности
            headers = self.headers.copy()
            headers["Idempotence-Key"] = self._generate_idempotence_key()
            
            logger.info(f"Creating YooKassa payment: amount={amount}, description={description}, email={email}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/payments",
                    headers=headers,
                    json=payment_data
                ) as response:
                    if response.status in (200, 201):
                        data = await response.json()
                        yookassa_payment_id = data.get("id")
                        confirmation_url = data.get("confirmation", {}).get("confirmation_url")
                        
                        logger.info(f"YooKassa payment created successfully: {yookassa_payment_id}")
                        return yookassa_payment_id, confirmation_url
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create YooKassa payment: {response.status} - {error_text}")
                        return None, None
                        
        except Exception as e:
            logger.error(f"Error creating YooKassa payment: {e}")
            return None, None
    
    async def check_payment(self, payment_id: str) -> bool:
        """
        Проверка статуса платежа в YooKassa
        
        Args:
            payment_id: ID платежа в YooKassa
            
        Returns:
            True если платеж оплачен, False в противном случае
        """
        try:
            logger.info(f"Checking YooKassa payment status: {payment_id}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/payments/{payment_id}",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        status = data.get("status")
                        
                        logger.info(f"YooKassa payment {payment_id} status: {status}")
                        return status == "succeeded"
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to check YooKassa payment: {response.status} - {error_text}")
                        return False
                        
        except Exception as e:
            logger.error(f"Error checking YooKassa payment: {e}")
            return False
    
    async def refund_payment(self, payment_id: str, amount: int, reason: str = "Возврат") -> bool:
        """
        Возврат платежа в YooKassa
        
        Args:
            payment_id: ID платежа в YooKassa
            amount: Сумма возврата в копейках
            reason: Причина возврата
            
        Returns:
            True если возврат успешен, False в противном случае
        """
        try:
            refund_data = {
                "amount": {
                    "value": f"{amount / 100:.2f}",
                    "currency": "RUB"
                },
                "description": reason
            }
            
            # Обновляем заголовки с новым ключом идемпотентности
            headers = self.headers.copy()
            headers["Idempotence-Key"] = self._generate_idempotence_key()
            
            logger.info(f"Refunding YooKassa payment: {payment_id}, amount={amount}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/refunds",
                    headers=headers,
                    json=refund_data
                ) as response:
                    if response.status in (200, 201):
                        data = await response.json()
                        refund_id = data.get("id")
                        logger.info(f"YooKassa refund created successfully: {refund_id}")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to refund YooKassa payment: {response.status} - {error_text}")
                        return False
                        
        except Exception as e:
            logger.error(f"Error refunding YooKassa payment: {e}")
            return False
    
    async def get_payment_info(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """
        Получение информации о платеже
        
        Args:
            payment_id: ID платежа в YooKassa
            
        Returns:
            Словарь с информацией о платеже или None при ошибке
        """
        try:
            logger.info(f"Getting YooKassa payment info: {payment_id}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/payments/{payment_id}",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Retrieved YooKassa payment info: {payment_id}")
                        return data
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to get YooKassa payment info: {response.status} - {error_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error getting YooKassa payment info: {e}")
            return None
    
    def is_test_mode(self) -> bool:
        """Проверка режима работы"""
        return self.test_mode
