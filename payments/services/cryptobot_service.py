import asyncio
import logging
import json
import aiohttp
from typing import Optional, Tuple, Dict, Any
from datetime import datetime

from app.settings import settings

logger = logging.getLogger(__name__)


class CryptoBotService:
    """Сервис для работы с CryptoBot API"""
    
    def __init__(
        self,
        api_token: Optional[str] = None,
        api_url: Optional[str] = None
    ):
        self.api_token = api_token or settings.CRYPTOBOT_API_TOKEN
        self.api_url = (api_url or settings.CRYPTOBOT_API_URL).rstrip('/')
        self.headers = {
            'Crypto-Pay-API-Token': self.api_token,
            'Content-Type': 'application/json'
        }
        self._timeout = aiohttp.ClientTimeout(total=30, connect=5, sock_connect=5, sock_read=25)
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Выполнить запрос к CryptoBot API"""
        if not self.api_token:
            logger.error("CryptoBot API token is not set")
            return False, None
        
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    json=data if data else None
                ) as response:
                    response_data = await response.json()
                    
                    if response.status == 200:
                        if response_data.get('ok'):
                            return True, response_data.get('result')
                        else:
                            error = response_data.get('error', {})
                            error_code = error.get('code', 'unknown')
                            error_name = error.get('name', 'unknown')
                            logger.error(f"CryptoBot API error: {error_code} - {error_name}")
                            return False, None
                    else:
                        logger.error(f"CryptoBot API request failed: {response.status} - {response_data}")
                        return False, None
                        
        except aiohttp.ClientError as e:
            logger.error(f"CryptoBot API connection error: {e}")
            return False, None
        except Exception as e:
            logger.error(f"CryptoBot API unexpected error: {e}")
            return False, None
    
    async def create_invoice(
        self,
        amount: float,
        asset: str = "USDT",
        description: Optional[str] = None,
        paid_btn_name: str = "callback",
        paid_btn_url: Optional[str] = None,
        expires_in: int = 3600,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """
        Создать инвойс для оплаты
        
        Args:
            amount: Сумма в USD
            asset: Криптовалюта (USDT, BTC, ETH и т.д.)
            description: Описание платежа
            paid_btn_name: Название кнопки после оплаты
            paid_btn_url: URL для кнопки после оплаты
            expires_in: Время истечения в секундах (по умолчанию 1 час)
            metadata: Дополнительные данные
            
        Returns:
            Tuple[invoice_id, payment_url, invoice_hash] или (None, None, None) при ошибке
        """
        data = {
            "asset": asset,
            "amount": str(amount),
            "expires_in": expires_in,
            "paid_btn_name": paid_btn_name
        }
        
        if description:
            data["description"] = description
        
        if paid_btn_url:
            data["paid_btn_url"] = paid_btn_url
        
        if metadata:
            # CryptoBot API требует metadata как строку (payload)
            # Конвертируем словарь в JSON строку
            data["payload"] = json.dumps(metadata)
        
        success, result = await self._make_request("POST", "/createInvoice", data)
        
        if success and result:
            invoice_id = result.get("invoice_id")
            # Используем bot_invoice_url (новое поле) или pay_url (deprecated, для обратной совместимости)
            pay_url = result.get("bot_invoice_url") or result.get("pay_url")
            invoice_hash = result.get("hash")
            logger.info(f"CryptoBot invoice created: {invoice_id}")
            return invoice_id, pay_url, invoice_hash
        
        return None, None, None
    
    async def get_invoice(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        """
        Получить информацию об инвойсе
        
        Args:
            invoice_id: ID инвойса
            
        Returns:
            Информация об инвойсе или None при ошибке
        """
        success, result = await self._make_request(
            "GET",
            f"/getInvoices?invoice_ids={invoice_id}"
        )
        
        if success and result:
            invoices = result.get("items", [])
            if invoices:
                return invoices[0]
        
        return None
    
    async def check_invoice_status(self, invoice_id: int) -> Optional[str]:
        """
        Проверить статус инвойса
        
        Args:
            invoice_id: ID инвойса
            
        Returns:
            Статус инвойса (active, paid, expired) или None при ошибке
        """
        invoice = await self.get_invoice(invoice_id)
        if invoice:
            return invoice.get("status")
        return None
    
    async def is_invoice_paid(self, invoice_id: int) -> bool:
        """
        Проверить, оплачен ли инвойс
        
        Args:
            invoice_id: ID инвойса
            
        Returns:
            True если инвойс оплачен
        """
        status = await self.check_invoice_status(invoice_id)
        return status == "paid"
    
    async def get_me(self) -> Optional[Dict[str, Any]]:
        """
        Получить информацию о боте CryptoBot
        
        Returns:
            Информация о боте или None при ошибке
        """
        success, result = await self._make_request("GET", "/getMe")
        
        if success and result:
            return result
        
        return None
    
    async def verify_webhook(self, payload: Dict[str, Any], secret: Optional[str] = None) -> bool:
        """
        Верифицировать webhook (если используется секрет)
        
        Args:
            payload: Данные webhook
            secret: Секрет для верификации (если используется)
            
        Returns:
            True если webhook валиден
        """
        # CryptoBot не использует подпись webhook по умолчанию
        # Но можно добавить проверку по другим параметрам
        # Пока что просто проверяем наличие обязательных полей
        if not payload:
            return False
        
        update_type = payload.get("update_type")
        if not update_type:
            return False
        
        # Проверяем, что это событие об инвойсе
        if update_type == "invoice_paid":
            payload_data = payload.get("payload", {})
            if not payload_data.get("invoice_id"):
                return False
        
        return True

