import os
import re
from dotenv import load_dotenv
from typing import Dict, Any

# Load environment variables
load_dotenv()

def validate_telegram_token(token: str) -> bool:
    """Валидация Telegram Bot Token"""
    if not token:
        return False
    # Telegram Bot Token format: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz
    pattern = r'^\d+:[A-Za-z0-9_-]{35}$'
    return bool(re.match(pattern, token))

def validate_yookassa_credentials(shop_id: str, api_key: str) -> bool:
    """Валидация YooKassa учетных данных"""
    if not shop_id or not api_key:
        return False
    # Shop ID: только цифры
    if not shop_id.isdigit():
        return False
    # API Key: может быть live_ или test_ + base64 формат
    if not re.match(r'^(live_|test_)[A-Za-z0-9+/=_-]+$', api_key):
        return False
    return True

def validate_url(url: str) -> bool:
    """Валидация URL"""
    if not url:
        return False
    pattern = r'^https?://[^\s/$.?#].[^\s]*$'
    return bool(re.match(pattern, url))

# Bot configuration from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_API_KEY = os.getenv("YOOKASSA_API_KEY")
YOOKASSA_RETURN_URL = os.getenv("YOOKASSA_RETURN_URL")

# Database configuration
DATABASE_PATH = os.getenv("DATABASE_PATH", "vpn.db")
DB_ENCRYPTION_KEY = os.getenv("DB_ENCRYPTION_KEY")

# Admin panel configuration
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")
SECRET_KEY = os.getenv("SECRET_KEY")

# Session configuration
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", "3600"))
SESSION_SECURE = os.getenv("SESSION_SECURE", "True").lower() == "true"

# Rate limiting configuration
RATE_LIMIT_LOGIN = os.getenv("RATE_LIMIT_LOGIN", "5/minute")
RATE_LIMIT_API = os.getenv("RATE_LIMIT_API", "100/minute")

# VPN Protocols configuration
PROTOCOLS = {
    'outline': {
        'name': 'Outline VPN',
        'description': 'Современный VPN протокол с высокой скоростью',
        'icon': '🔒',
        'default_port': 443
    },
    'v2ray': {
        'name': 'V2Ray VLESS',
        'description': 'Продвинутый протокол с обфускацией трафика и Reality',
        'icon': '🛡️',
        'default_port': 443,
        'default_path': '/v2ray'
    }
}

def validate_configuration() -> Dict[str, Any]:
    """Валидация всей конфигурации"""
    errors = []
    warnings = []
    
    # Validate Telegram Bot Token
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN environment variable is required")
    elif not validate_telegram_token(TELEGRAM_BOT_TOKEN):
        errors.append("Invalid TELEGRAM_BOT_TOKEN format")
    
    # Validate YooKassa credentials
    if not YOOKASSA_SHOP_ID or not YOOKASSA_API_KEY:
        errors.append("YOOKASSA_SHOP_ID and YOOKASSA_API_KEY environment variables are required")
    elif not validate_yookassa_credentials(YOOKASSA_SHOP_ID, YOOKASSA_API_KEY):
        errors.append("Invalid YooKassa credentials format")
    
    # Validate YooKassa return URL
    if not YOOKASSA_RETURN_URL:
        errors.append("YOOKASSA_RETURN_URL environment variable is required")
    elif not validate_url(YOOKASSA_RETURN_URL):
        errors.append("Invalid YOOKASSA_RETURN_URL format")
    
    # Validate database configuration
    if not DATABASE_PATH:
        errors.append("DATABASE_PATH environment variable is required")
    
    # Validate admin configuration
    if not ADMIN_PASSWORD_HASH:
        warnings.append("ADMIN_PASSWORD_HASH not set - admin panel may not work")
    
    if not SECRET_KEY:
        warnings.append("SECRET_KEY not set - using default (insecure)")
    
    # Validate session configuration
    if SESSION_MAX_AGE <= 0:
        errors.append("SESSION_MAX_AGE must be positive")
    
    return {
        'errors': errors,
        'warnings': warnings,
        'is_valid': len(errors) == 0
    }

# Validate configuration on import
config_validation = validate_configuration()

if not config_validation['is_valid']:
    print("Configuration errors:")
    for error in config_validation['errors']:
        print(f"  ❌ {error}")
    raise ValueError("Invalid configuration. Please check environment variables.")

if config_validation['warnings']:
    print("Configuration warnings:")
    for warning in config_validation['warnings']:
        print(f"  ⚠️ {warning}")

# Export validated configuration
__all__ = [
    'TELEGRAM_BOT_TOKEN', 'YOOKASSA_SHOP_ID', 'YOOKASSA_API_KEY', 
    'YOOKASSA_RETURN_URL', 'DATABASE_PATH', 'DB_ENCRYPTION_KEY',
    'ADMIN_USERNAME', 'ADMIN_PASSWORD_HASH', 'SECRET_KEY',
    'SESSION_MAX_AGE', 'SESSION_SECURE', 'RATE_LIMIT_LOGIN', 
    'RATE_LIMIT_API', 'PROTOCOLS', 'validate_configuration'
]
