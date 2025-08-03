import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Bot configuration from environment variables
TELEGRAM_BOT_TOKEN = "7474256709:AAGhs1vSl1Mz3IJza-F08F63EIj1evi6neg"
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_API_KEY = os.getenv("YOOKASSA_API_KEY")
YOOKASSA_RETURN_URL = os.getenv("YOOKASSA_RETURN_URL")

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
        'default_path': '/v2ray',
        'api_key': 'QBDMqDzCRh17NIGUsKDtWtoUmvwRVvSHHp4W8OCMcOM='
    }
}

# Validate required environment variables
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
if not YOOKASSA_SHOP_ID:
    raise ValueError("YOOKASSA_SHOP_ID environment variable is required")
if not YOOKASSA_API_KEY:
    raise ValueError("YOOKASSA_API_KEY environment variable is required")
if not YOOKASSA_RETURN_URL:
    raise ValueError("YOOKASSA_RETURN_URL environment variable is required")
