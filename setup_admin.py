#!/usr/bin/env python3
"""
Setup script for VeilBot Admin Panel
Generates secure admin credentials and configuration
"""

import os
import secrets
from passlib.context import CryptContext

def setup_admin():
    """Setup initial admin credentials"""
    print("🔐 VeilBot Admin Panel Setup")
    print("=" * 40)
    
    # Generate secure secret key
    secret_key = secrets.token_urlsafe(32)
    
    # Generate secure admin password
    admin_password = secrets.token_urlsafe(16)
    
    # Hash the password
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password_hash = pwd_context.hash(admin_password)
    
    # Create .env file content
    env_content = f"""# Admin Panel Security Configuration
SECRET_KEY={secret_key}
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH={password_hash}

# Database
DATABASE_PATH=/root/veilbot/vpn.db

# Session Configuration
SESSION_MAX_AGE=3600
SESSION_SECURE=False

# Rate Limiting
RATE_LIMIT_LOGIN=5/minute
RATE_LIMIT_API=100/minute
"""
    
    # Write .env file
    with open('.env', 'w') as f:
        f.write(env_content)
    
    print("✅ Configuration generated successfully!")
    print()
    print("🔑 Admin Credentials:")
    print(f"Username: admin")
    print(f"Password: {admin_password}")
    print()
    print("⚠️  IMPORTANT: Save these credentials securely!")
    print("⚠️  The password will not be shown again.")
    print()
    print("📁 Configuration saved to .env file")
    print()
    print("🚀 You can now start the admin panel:")
    print("   cd admin && python main.py")

if __name__ == "__main__":
    setup_admin() 