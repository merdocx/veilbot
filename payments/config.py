"""
Конфигурация для платежного модуля

Настройки для интеграции с основным приложением.
"""

import os
from typing import Dict, Any
from dataclasses import dataclass


@dataclass
class PaymentConfig:
    """Конфигурация платежного модуля"""
    
    # YooKassa настройки
    yookassa_shop_id: str
    yookassa_api_key: str
    yookassa_return_url: str
    yookassa_test_mode: bool = False
    
    # База данных
    database_path: str = "vpn.db"
    
    # Настройки логирования
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Настройки платежей
    default_currency: str = "RUB"
    default_provider: str = "yookassa"
    payment_timeout_minutes: int = 5
    check_interval_seconds: int = 5
    
    # Настройки webhook
    webhook_secret: str = ""
    webhook_timeout: int = 30
    
    # Настройки очистки
    cleanup_expired_hours: int = 24
    
    @classmethod
    def from_env(cls) -> 'PaymentConfig':
        """Создание конфигурации из переменных окружения"""
        return cls(
            yookassa_shop_id=os.getenv('YOOKASSA_SHOP_ID', ''),
            yookassa_api_key=os.getenv('YOOKASSA_API_KEY', ''),
            yookassa_return_url=os.getenv('YOOKASSA_RETURN_URL', ''),
            yookassa_test_mode=os.getenv('YOOKASSA_TEST_MODE', 'false').lower() == 'true',
            database_path=os.getenv('DATABASE_PATH', 'vpn.db'),
            log_level=os.getenv('PAYMENT_LOG_LEVEL', 'INFO'),
            default_currency=os.getenv('PAYMENT_DEFAULT_CURRENCY', 'RUB'),
            default_provider=os.getenv('PAYMENT_DEFAULT_PROVIDER', 'yookassa'),
            payment_timeout_minutes=int(os.getenv('PAYMENT_TIMEOUT_MINUTES', '5')),
            check_interval_seconds=int(os.getenv('PAYMENT_CHECK_INTERVAL', '5')),
            webhook_secret=os.getenv('PAYMENT_WEBHOOK_SECRET', ''),
            webhook_timeout=int(os.getenv('PAYMENT_WEBHOOK_TIMEOUT', '30')),
            cleanup_expired_hours=int(os.getenv('PAYMENT_CLEANUP_HOURS', '24'))
        )
    
    def validate(self) -> bool:
        """Валидация конфигурации"""
        required_fields = [
            'yookassa_shop_id',
            'yookassa_api_key',
            'yookassa_return_url'
        ]
        
        for field in required_fields:
            if not getattr(self, field):
                raise ValueError(f"Missing required configuration: {field}")
        
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь"""
        return {
            'yookassa_shop_id': self.yookassa_shop_id,
            'yookassa_api_key': self.yookassa_api_key,
            'yookassa_return_url': self.yookassa_return_url,
            'yookassa_test_mode': self.yookassa_test_mode,
            'database_path': self.database_path,
            'log_level': self.log_level,
            'log_format': self.log_format,
            'default_currency': self.default_currency,
            'default_provider': self.default_provider,
            'payment_timeout_minutes': self.payment_timeout_minutes,
            'check_interval_seconds': self.check_interval_seconds,
            'webhook_secret': self.webhook_secret,
            'webhook_timeout': self.webhook_timeout,
            'cleanup_expired_hours': self.cleanup_expired_hours
        }


class PaymentServiceFactory:
    """Фабрика для создания сервисов платежей"""
    
    def __init__(self, config: PaymentConfig):
        self.config = config
        self._payment_service = None
        self._yookassa_service = None
        self._payment_repo = None
    
    def create_yookassa_service(self):
        """Создание YooKassa сервиса"""
        if self._yookassa_service is None:
            from .services.yookassa_service import YooKassaService
            
            self._yookassa_service = YooKassaService(
                shop_id=self.config.yookassa_shop_id,
                api_key=self.config.yookassa_api_key,
                return_url=self.config.yookassa_return_url,
                test_mode=self.config.yookassa_test_mode
            )
        
        return self._yookassa_service
    
    def create_payment_repository(self):
        """Создание репозитория платежей"""
        if self._payment_repo is None:
            from .repositories.payment_repository import PaymentRepository
            
            self._payment_repo = PaymentRepository(
                db_path=self.config.database_path
            )
        
        return self._payment_repo
    
    def create_payment_service(self):
        """Создание основного сервиса платежей"""
        if self._payment_service is None:
            from .services.payment_service import PaymentService
            
            yookassa_service = self.create_yookassa_service()
            payment_repo = self.create_payment_repository()
            
            self._payment_service = PaymentService(
                payment_repo=payment_repo,
                yookassa_service=yookassa_service
            )
        
        return self._payment_service
    
    def create_webhook_service(self):
        """Создание webhook сервиса"""
        from .services.webhook_service import WebhookService
        
        payment_service = self.create_payment_service()
        payment_repo = self.create_payment_repository()
        
        return WebhookService(
            payment_repo=payment_repo,
            payment_service=payment_service
        )


# Глобальный экземпляр фабрики
_payment_factory = None


def get_payment_factory() -> PaymentServiceFactory:
    """Получение глобального экземпляра фабрики"""
    global _payment_factory
    if _payment_factory is None:
        config = PaymentConfig.from_env()
        config.validate()
        _payment_factory = PaymentServiceFactory(config)
    
    return _payment_factory


def get_payment_service():
    """Получение сервиса платежей"""
    factory = get_payment_factory()
    return factory.create_payment_service()


def get_webhook_service():
    """Получение webhook сервиса"""
    factory = get_payment_factory()
    return factory.create_webhook_service()


def initialize_payment_module(config: PaymentConfig = None):
    """Инициализация платежного модуля"""
    global _payment_factory
    
    if config is None:
        config = PaymentConfig.from_env()
    
    config.validate()
    _payment_factory = PaymentServiceFactory(config)
    
    # Инициализируем сервисы
    factory = get_payment_factory()
    factory.create_payment_service()
    factory.create_webhook_service()
    
    return factory
