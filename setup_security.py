#!/usr/bin/env python3
"""
VeilBot Security Setup Script
This script helps configure the bot with secure environment variables.
"""

import os
import secrets
import sys
from pathlib import Path

def generate_secure_token():
    """Generate a secure random token"""
    return secrets.token_urlsafe(32)

def create_env_file():
    """Create .env file for bot configuration"""
    env_content = """# VeilBot Bot Configuration
# ⚠️ SECURITY WARNING: Replace these values with your actual credentials
# ⚠️ NEVER commit this file to version control

# Telegram Bot Configuration
# Get your token from @BotFather
TELEGRAM_BOT_TOKEN=***REMOVED***

# YooKassa Payment Configuration
# Get these from your YooKassa dashboard
YOOKASSA_SHOP_ID=your_shop_id_here
YOOKASSA_API_KEY=your_api_key_here
YOOKASSA_RETURN_URL=https://t.me/your_bot_username

# ⚠️ IMPORTANT SECURITY STEPS:
# 1. Replace all placeholder values with your actual credentials
# 2. Regenerate your Telegram bot token at @BotFather
# 3. Update YooKassa API key in your payment provider dashboard
# 4. Set proper file permissions: chmod 600 .env
# 5. Add .env to .gitignore to prevent accidental commits
"""
    
    with open('.env', 'w') as f:
        f.write(env_content)
    
    print("✅ Created .env file for bot configuration")
    print("⚠️  IMPORTANT: Update .env with your actual credentials")

def check_security():
    """Check current security status"""
    print("🔍 Security Status Check:")
    print("-" * 40)
    
    # Check if .env exists
    if os.path.exists('.env'):
        print("✅ .env file exists")
    else:
        print("❌ .env file missing")
    
    # Check file permissions
    if os.path.exists('.env'):
        perms = oct(os.stat('.env').st_mode)[-3:]
        if perms == '600':
            print("✅ .env file permissions are secure (600)")
        else:
            print(f"⚠️  .env file permissions: {perms} (should be 600)")
    
    # Check if secrets are hardcoded
    config_file = Path('config.py')
    if config_file.exists():
        content = config_file.read_text()
        if 'TELEGRAM_BOT_TOKEN = "' in content:
            print("❌ Hardcoded secrets found in config.py")
        else:
            print("✅ No hardcoded secrets in config.py")
    
    # Check database permissions
    if os.path.exists('vpn.db'):
        perms = oct(os.stat('vpn.db').st_mode)[-3:]
        if perms in ['640', '600']:
            print("✅ Database file permissions are secure")
        else:
            print(f"⚠️  Database file permissions: {perms} (should be 640 or 600)")
    
    print("-" * 40)

def main():
    """Main setup function"""
    print("🔐 VeilBot Security Setup")
    print("=" * 40)
    
    if len(sys.argv) > 1 and sys.argv[1] == 'check':
        check_security()
        return
    
    print("This script will help you set up secure configuration for VeilBot.")
    print()
    
    # Create .env file
    create_env_file()
    
    print()
    print("🔧 Next Steps:")
    print("1. Edit .env file with your actual credentials")
    print("2. Set secure file permissions: chmod 600 .env")
    print("3. Add .env to .gitignore")
    print("4. Regenerate your Telegram bot token")
    print("5. Update YooKassa API credentials")
    print()
    print("Run 'python3 setup_security.py check' to verify security status")

if __name__ == "__main__":
    main() 