"""
Backward-compat import surface for legacy modules.
This module now proxies settings from app.settings without validating on import.
"""

from app.settings import settings as _settings

TELEGRAM_BOT_TOKEN = _settings.TELEGRAM_BOT_TOKEN
YOOKASSA_SHOP_ID = _settings.YOOKASSA_SHOP_ID
YOOKASSA_API_KEY = _settings.YOOKASSA_API_KEY
YOOKASSA_RETURN_URL = _settings.YOOKASSA_RETURN_URL

DATABASE_PATH = _settings.DATABASE_PATH
DB_ENCRYPTION_KEY = _settings.DB_ENCRYPTION_KEY

ADMIN_USERNAME = _settings.ADMIN_USERNAME
ADMIN_PASSWORD_HASH = _settings.ADMIN_PASSWORD_HASH
SECRET_KEY = _settings.SECRET_KEY

ADMIN_ID = _settings.ADMIN_ID
SUPPORT_USERNAME = _settings.SUPPORT_USERNAME

SESSION_MAX_AGE = _settings.SESSION_MAX_AGE
SESSION_SECURE = _settings.SESSION_SECURE

RATE_LIMIT_LOGIN = _settings.RATE_LIMIT_LOGIN
RATE_LIMIT_API = _settings.RATE_LIMIT_API

PROTOCOLS = _settings.PROTOCOLS

def validate_configuration():
    """Compatibility shim: delegate to settings.validate_startup()."""
    return _settings.validate_startup()

__all__ = [
    'TELEGRAM_BOT_TOKEN', 'YOOKASSA_SHOP_ID', 'YOOKASSA_API_KEY',
    'YOOKASSA_RETURN_URL', 'DATABASE_PATH', 'DB_ENCRYPTION_KEY',
    'ADMIN_USERNAME', 'ADMIN_PASSWORD_HASH', 'SECRET_KEY',
    'ADMIN_ID', 'SUPPORT_USERNAME', 'SESSION_MAX_AGE', 'SESSION_SECURE', 
    'RATE_LIMIT_LOGIN', 'RATE_LIMIT_API', 'PROTOCOLS', 'validate_configuration'
]

