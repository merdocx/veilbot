import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Bot configuration from environment variables
TELEGRAM_BOT_TOKEN = "***REMOVED***"
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_API_KEY = os.getenv("YOOKASSA_API_KEY")
YOOKASSA_RETURN_URL = os.getenv("YOOKASSA_RETURN_URL")

# Validate required environment variables
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
if not YOOKASSA_SHOP_ID:
    raise ValueError("YOOKASSA_SHOP_ID environment variable is required")
if not YOOKASSA_API_KEY:
    raise ValueError("YOOKASSA_API_KEY environment variable is required")
if not YOOKASSA_RETURN_URL:
    raise ValueError("YOOKASSA_RETURN_URL environment variable is required")
