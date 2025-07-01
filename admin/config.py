import os
import secrets
from dotenv import load_dotenv
from passlib.context import CryptContext

# Load environment variables
load_dotenv()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")

# Database configuration
DATABASE_PATH = os.getenv("DATABASE_PATH", "/root/veilbot/vpn.db")

# Rate limiting
RATE_LIMIT_LOGIN = "5/minute"
RATE_LIMIT_API = "100/minute"

# Session configuration
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", "3600"))  # 1 hour
SESSION_SECURE = os.getenv("SESSION_SECURE", "False").lower() == "true"

# Security headers
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' fonts.googleapis.com; font-src fonts.gstatic.com; img-src 'self' data:;"
}

def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)

def generate_secure_password() -> str:
    """Generate a secure random password"""
    return secrets.token_urlsafe(16)

def setup_initial_admin():
    """Setup initial admin credentials if not configured"""
    if not ADMIN_PASSWORD_HASH:
        # Generate a secure password
        password = generate_secure_password()
        hashed = hash_password(password)
        print(f"🔐 Initial admin setup:")
        print(f"Username: {ADMIN_USERNAME}")
        print(f"Password: {password}")
        print(f"Add to .env file:")
        print(f"ADMIN_PASSWORD_HASH={hashed}")
        return False
    return True 