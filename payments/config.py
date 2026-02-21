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
    
    # Platega настройки
    platega_base_url: str = "https://app.platega.io"
    platega_merchant_id: str = ""
    platega_secret: str = ""
    platega_callback_url: str = ""
    
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
        """Создание конфигурации из окружения с fallback на app.settings.

        Значения из переменных окружения имеют приоритет перед app.settings.
        """
        try:
            from app.settings import settings
        except Exception:
            settings = None  # type: ignore

        def env_or(default_key: str, settings_value: str | None, default_fallback: str = '') -> str:
            val = os.getenv(default_key)
            if val is not None:
                return val
            return settings_value or default_fallback if settings is not None else default_fallback

        return cls(
            yookassa_shop_id=env_or('YOOKASSA_SHOP_ID', getattr(settings, 'YOOKASSA_SHOP_ID', None), ''),
            yookassa_api_key=env_or('YOOKASSA_API_KEY', getattr(settings, 'YOOKASSA_API_KEY', None), ''),
            yookassa_return_url=env_or('YOOKASSA_RETURN_URL', getattr(settings, 'YOOKASSA_RETURN_URL', None), ''),
            yookassa_test_mode=os.getenv('YOOKASSA_TEST_MODE', 'false').lower() == 'true',
            platega_base_url=os.getenv('PLATEGA_BASE_URL', getattr(settings, 'PLATEGA_BASE_URL', 'https://app.platega.io')),
            platega_merchant_id=env_or('PLATEGA_MERCHANT_ID', getattr(settings, 'PLATEGA_MERCHANT_ID', None), ''),
            platega_secret=env_or('PLATEGA_SECRET', getattr(settings, 'PLATEGA_SECRET', None), ''),
            platega_callback_url=os.getenv('PLATEGA_CALLBACK_URL', getattr(settings, 'PLATEGA_CALLBACK_URL', '')),
            database_path=os.getenv('DATABASE_PATH', getattr(settings, 'DATABASE_PATH', 'vpn.db')),
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
        has_yookassa = all(getattr(self, field) for field in ['yookassa_shop_id', 'yookassa_api_key', 'yookassa_return_url'])
        has_platega = bool(self.platega_merchant_id and self.platega_secret)

        if not (has_yookassa or has_platega):
            raise ValueError("Missing payment configuration: provide YooKassa or Platega credentials")
        
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь"""
        return {
            'yookassa_shop_id': self.yookassa_shop_id,
            'yookassa_api_key': self.yookassa_api_key,
            'yookassa_return_url': self.yookassa_return_url,
            'yookassa_test_mode': self.yookassa_test_mode,
            'platega_base_url': self.platega_base_url,
            'platega_merchant_id': self.platega_merchant_id,
            'platega_secret': self.platega_secret,
            'platega_callback_url': self.platega_callback_url,
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
        self._platega_service = None
        self._payment_repo = None
    
    def create_yookassa_service(self):
        """Создание YooKassa сервиса"""
        if self._yookassa_service is None:
            if not (self.config.yookassa_shop_id and self.config.yookassa_api_key and self.config.yookassa_return_url):
                return None
            from .services.yookassa_service import YooKassaService
            
            self._yookassa_service = YooKassaService(
                shop_id=self.config.yookassa_shop_id,
                api_key=self.config.yookassa_api_key,
                return_url=self.config.yookassa_return_url,
                test_mode=self.config.yookassa_test_mode
            )
        
        return self._yookassa_service

    def create_platega_service(self):
        """Создание Platega сервиса"""
        if self._platega_service is None:
            if not (self.config.platega_merchant_id and self.config.platega_secret):
                return None
            from .services.platega_service import PlategaService

            # Используем return_url YooKassa как базовый редирект, если не задан отдельный
            from app.settings import settings as app_settings

            self._platega_service = PlategaService(
                merchant_id=self.config.platega_merchant_id,
                api_secret=self.config.platega_secret,
                base_url=self.config.platega_base_url,
                callback_url=self.config.platega_callback_url or None,
                return_url=getattr(app_settings, "YOOKASSA_RETURN_URL", None),
                failed_url=getattr(app_settings, "YOOKASSA_RETURN_URL", None),
            )
        return self._platega_service
    
    def create_payment_repository(self):
        """Создание репозитория платежей"""
        if self._payment_repo is None:
            from .repositories.payment_repository import PaymentRepository
            
            self._payment_repo = PaymentRepository(db_path=self.config.database_path)
        
        return self._payment_repo
    
    def create_cryptobot_service(self):
        """Создание CryptoBot сервиса"""
        from .services.cryptobot_service import CryptoBotService
        from app.settings import settings as app_settings
        
        if not app_settings.CRYPTOBOT_API_TOKEN:
            return None
        
        return CryptoBotService(
            api_token=app_settings.CRYPTOBOT_API_TOKEN,
            api_url=app_settings.CRYPTOBOT_API_URL
        )
    
    def create_payment_service(self):
        """Создание основного сервиса платежей"""
        if self._payment_service is None:
            from .services.payment_service import PaymentService
            
            yookassa_service = self.create_yookassa_service()
            payment_repo = self.create_payment_repository()
            cryptobot_service = self.create_cryptobot_service()
            platega_service = self.create_platega_service()
            
            self._payment_service = PaymentService(
                payment_repo=payment_repo,
                yookassa_service=yookassa_service,
                cryptobot_service=cryptobot_service,
                platega_service=platega_service,
            )
        
        return self._payment_service
    
    def create_webhook_service(self):
        """Создание webhook сервиса"""
        from .services.webhook_service import WebhookService
        
        payment_service = self.create_payment_service()
        payment_repo = self.create_payment_repository()
        
        return WebhookService(
            payment_repo=payment_repo,
            payment_service=payment_service,
            webhook_secret=self.config.webhook_secret or None
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
