#!/usr/bin/env python3
"""
Database Encryption Migration Script
Migrates existing sensitive data to encrypted format.
"""

import os
import sys
from db_encryption import DatabaseEncryption

def main():
    """Main migration function"""
    print("🔐 VeilBot Database Encryption Migration")
    print("=" * 50)
    
    # Check if database exists
    db_path = "vpn.db"
    if not os.path.exists(db_path):
        print(f"❌ Database file {db_path} not found")
        return
    
    # Check if encryption key is set
    encryption_key = os.getenv('DB_ENCRYPTION_KEY')
    if not encryption_key:
        print("⚠️  DB_ENCRYPTION_KEY not set in environment")
        print("🔑 Generating new encryption key...")
        
        # Generate new key
        encryption = DatabaseEncryption()
        new_key = encryption.master_key
        
        print(f"✅ Generated encryption key: {new_key}")
        print("📝 Add this to your .env file:")
        print(f"DB_ENCRYPTION_KEY={new_key}")
        print()
        
        # Ask for confirmation
        response = input("Continue with migration? (y/N): ")
        if response.lower() != 'y':
            print("❌ Migration cancelled")
            return
    else:
        encryption = DatabaseEncryption(encryption_key)
    
    # Create backup
    backup_path = f"{db_path}.backup"
    print(f"📦 Creating backup: {backup_path}")
    try:
        import shutil
        shutil.copy2(db_path, backup_path)
        print("✅ Backup created successfully")
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        return
    
    # Perform migration
    try:
        encryption.migrate_database(db_path)
        print("✅ Migration completed successfully")
        print()
        print("🔧 Next steps:")
        print("1. Test the application to ensure encryption works")
        print("2. Update your code to use encrypted database connections")
        print("3. Keep the backup file for safety")
        print("4. Store the encryption key securely")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        print("🔄 Restoring from backup...")
        try:
            shutil.copy2(backup_path, db_path)
            print("✅ Database restored from backup")
        except Exception as restore_error:
            print(f"❌ Restore failed: {restore_error}")
            print("⚠️  Manual restore required")

if __name__ == "__main__":
    main() 