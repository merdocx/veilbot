from enum import Enum
from typing import List


class PaymentStatus(str, Enum):
    """Статусы платежей"""
    PENDING = "pending"
    PAID = "paid"
    COMPLETED = "completed"  # Платеж оплачен и ключ выдан/продлен
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    EXPIRED = "expired"


class PaymentProvider(str, Enum):
    """Платежные провайдеры"""
    YOOKASSA = "yookassa"
    PLATEGA = "platega"
    CRYPTOBOT = "cryptobot"
    STRIPE = "stripe"  # Для будущего расширения
    PAYPAL = "paypal"  # Для будущего расширения


class PaymentMethod(str, Enum):
    """Методы оплаты"""
    CARD = "card"
    SBP = "sbp"  # Система быстрых платежей
    WALLET = "wallet"  # Электронный кошелек
    BANK_TRANSFER = "bank_transfer"


class PaymentCurrency(str, Enum):
    """Валюты платежей"""
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


class PaymentReceiptType(str, Enum):
    """Типы чеков"""
    SERVICE = "service"
    GOODS = "goods"


class PaymentVATCode(int, Enum):
    """Коды НДС"""
    NO_VAT = 1
    VAT_0 = 2
    VAT_10 = 3
    VAT_20 = 4


class PaymentMode(str, Enum):
    """Режимы платежа"""
    FULL_PREPAYMENT = "full_prepayment"
    PARTIAL_PREPAYMENT = "partial_prepayment"
    ADVANCE = "advance"
    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    CREDIT = "credit"
    CREDIT_PAYMENT = "credit_payment"
