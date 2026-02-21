from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

from .enums import PaymentStatus, PaymentProvider, PaymentMethod, PaymentCurrency


@dataclass
class Payment:
    """Модель платежа"""
    id: Optional[int] = None
    payment_id: str = ""
    user_id: int = 0
    tariff_id: int = 0
    amount: int = 0
    currency: PaymentCurrency = PaymentCurrency.RUB
    email: Optional[str] = None
    status: PaymentStatus = PaymentStatus.PENDING
    country: Optional[str] = None
    protocol: str = "outline"
    provider: PaymentProvider = PaymentProvider.YOOKASSA
    method: Optional[PaymentMethod] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    subscription_id: Optional[int] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.updated_at is None:
            self.updated_at = datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь"""
        return {
            'id': self.id,
            'payment_id': self.payment_id,
            'user_id': self.user_id,
            'tariff_id': self.tariff_id,
            'amount': self.amount,
            'currency': self.currency.value,
            'email': self.email,
            'status': self.status.value,
            'country': self.country,
            'protocol': self.protocol,
            'provider': self.provider.value,
            'method': self.method.value if self.method else None,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'paid_at': self.paid_at.isoformat() if self.paid_at else None,
            'metadata': self.metadata,
            'subscription_id': self.subscription_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Payment':
        """Создание из словаря"""
        # Преобразование строк в enum
        if 'currency' in data and data['currency']:
            data['currency'] = PaymentCurrency(data['currency'])
        if 'status' in data and data['status']:
            data['status'] = PaymentStatus(data['status'])
        if 'provider' in data and data['provider']:
            data['provider'] = PaymentProvider(data['provider'])
        if 'method' in data and data['method']:
            data['method'] = PaymentMethod(data['method'])
        
        # Преобразование строк в datetime
        for field in ['created_at', 'updated_at', 'paid_at']:
            if field in data and data[field]:
                data[field] = datetime.fromisoformat(data[field])
        
        return cls(**data)
    
    def is_paid(self) -> bool:
        """Проверить, оплачен ли платеж"""
        return self.status == PaymentStatus.PAID
    
    def is_pending(self) -> bool:
        """Проверить, ожидает ли платеж оплаты"""
        return self.status == PaymentStatus.PENDING
    
    def is_failed(self) -> bool:
        """Проверить, неудачен ли платеж"""
        return self.status in [PaymentStatus.FAILED, PaymentStatus.CANCELLED, PaymentStatus.EXPIRED]
    
    def mark_as_paid(self):
        """Отметить как оплаченный"""
        self.status = PaymentStatus.PAID
        self.paid_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
    
    def mark_as_failed(self):
        """Отметить как неудачный"""
        self.status = PaymentStatus.FAILED
        self.updated_at = datetime.now(timezone.utc)
    
    def mark_as_expired(self):
        """Отметить как истекший (не был оплачен в отведенное время)"""
        self.status = PaymentStatus.EXPIRED
        self.updated_at = datetime.now(timezone.utc)
    
    def mark_as_cancelled(self):
        """Отметить как отмененный"""
        self.status = PaymentStatus.CANCELLED
        self.updated_at = datetime.now(timezone.utc)
    
    def mark_as_completed(self):
        """Отметить как закрытый (ключ выдан/продлен)"""
        self.status = PaymentStatus.COMPLETED
        self.updated_at = datetime.now(timezone.utc)


class PaymentCreate(BaseModel):
    """Модель для создания платежа"""
    user_id: int = Field(..., description="ID пользователя")
    tariff_id: int = Field(..., description="ID тарифа")
    amount: int = Field(..., description="Сумма платежа в копейках")
    currency: PaymentCurrency = Field(PaymentCurrency.RUB, description="Валюта")
    email: Optional[str] = Field(None, description="Email для чека")
    country: Optional[str] = Field(None, description="Страна")
    protocol: str = Field("outline", description="VPN протокол")
    provider: PaymentProvider = Field(PaymentProvider.YOOKASSA, description="Платежный провайдер")
    description: Optional[str] = Field(None, description="Описание платежа")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Дополнительные данные")
    
    @field_validator('amount')
    @classmethod
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError('Amount must be positive')
        return v
    
    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        if v and '@' not in v:
            raise ValueError('Invalid email format')
        return v


class PaymentUpdate(BaseModel):
    """Модель для обновления платежа"""
    status: Optional[PaymentStatus] = Field(None, description="Статус платежа")
    method: Optional[PaymentMethod] = Field(None, description="Метод оплаты")
    description: Optional[str] = Field(None, description="Описание")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Дополнительные данные")


class PaymentFilter(BaseModel):
    """Фильтр для поиска платежей"""
    user_id: Optional[int] = Field(None, description="ID пользователя")
    tariff_id: Optional[int] = Field(None, description="ID тарифа")
    status: Optional[PaymentStatus] = Field(None, description="Статус")
    provider: Optional[PaymentProvider] = Field(None, description="Провайдер")
    country: Optional[str] = Field(None, description="Страна")
    protocol: Optional[str] = Field(None, description="Протокол")
    is_paid: Optional[bool] = Field(None, description="Оплаченные платежи")
    is_pending: Optional[bool] = Field(None, description="Ожидающие платежи")
    created_after: Optional[datetime] = Field(None, description="Созданные после")
    created_before: Optional[datetime] = Field(None, description="Созданные до")
    search_query: Optional[str] = Field(None, description="Поисковый запрос по всем полям")
    limit: int = Field(100, description="Лимит результатов")
    offset: int = Field(0, description="Смещение")
