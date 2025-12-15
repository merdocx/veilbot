import logging
import aiohttp
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class PlategaService:
    """Сервис интеграции с Platega API"""

    def __init__(
        self,
        merchant_id: str,
        api_secret: str,
        base_url: str = "https://app.platega.io",
        callback_url: Optional[str] = None,
        return_url: Optional[str] = None,
        failed_url: Optional[str] = None,
        timeout_seconds: int = 30,
    ):
        self.merchant_id = merchant_id
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.callback_url = callback_url
        # URL для редиректа после оплаты/ошибки
        self.return_url = return_url
        self.failed_url = failed_url or return_url
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=5)
        self._headers = {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.api_secret,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        json_payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=self._headers,
                    json=json_payload,
                    params=params,
                ) as response:
                    data = await response.json(content_type=None)
                    if 200 <= response.status < 300:
                        return True, data
                    logger.error(
                        "Platega API error: status=%s, body=%s", response.status, data
                    )
                    return False, data
        except Exception as e:
            logger.error(f"Platega API request failed: {e}", exc_info=True)
            return False, None

    async def create_payment(
        self,
        amount: int,
        description: str,
        email: str,
        payment_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        currency: str = "RUB",
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Создать ссылку на оплату через Platega (/transaction/process).
        amount передается в копейках — конвертируем в рубли (float).
        """
        amount_value = round(amount / 100, 2)

        # Маппинг способа оплаты: по умолчанию российские карты (10),
        # но если в metadata передан platega_payment_method, используем его.
        payment_method_int = 10
        if metadata and "platega_payment_method" in metadata:
            try:
                payment_method_int = int(metadata["platega_payment_method"])
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid platega_payment_method in metadata: %r, fallback to 10",
                    metadata.get("platega_payment_method"),
                )

        payload: Dict[str, Any] = {
            "paymentMethod": payment_method_int,
            "paymentDetails": {
                "amount": amount_value,
                "currency": currency,
            },
            "description": description,
            "return": self.return_url or self.callback_url or "https://t.me/veilbot_bot",
            "failedUrl": self.failed_url or self.return_url or self.callback_url or "https://t.me/veilbot_bot",
            "payload": payment_id,
        }

        success, data = await self._request("POST", "/transaction/process", json_payload=payload)
        if not success or not data:
            return None, None

        transaction_id = data.get("transactionId") or data.get("id") or data.get("paymentId")
        confirmation_url = (
            data.get("paymentUrl")
            or data.get("url")
            or data.get("confirmationUrl")
            or data.get("redirectUrl")
            or data.get("redirect")
        )

        if not transaction_id or not confirmation_url:
            logger.error(f"Platega create_payment returned incomplete data: {data}")
            return None, None

        return str(transaction_id), str(confirmation_url)

    async def check_payment(self, transaction_id: str) -> bool:
        """Проверить статус транзакции в Platega (GET /transaction/{id})."""
        success, data = await self._request(
            "GET", f"/transaction/{transaction_id}"
        )
        if not success or not data:
            return False

        status = data.get("status")
        if not status:
            logger.warning(f"Platega status missing for transaction {transaction_id}: {data}")
            return False

        status_norm = str(status).lower()
        return status_norm in {"paid", "success", "succeeded", "completed"}

    async def parse_webhook(self, payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        """
        Извлечь payment_id и статус из webhook.
        Возвращает (transaction_id, status_str)
        """
        if not payload:
            return None, None

        tx_id = payload.get("id") or payload.get("transactionId")
        status = payload.get("status")
        if status:
            status = str(status).lower()
        return tx_id, status

    @staticmethod
    def is_paid_status(status: Optional[str]) -> bool:
        if not status:
            return False
        # По документации Platega успешный статус CONFIRMED
        return str(status).upper() == "CONFIRMED"



