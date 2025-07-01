#!/usr/bin/env python3
"""
Database Encryption Module for VeilBot
Handles encryption and decryption of sensitive database fields.
"""

import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import sqlite3
import logging

class DatabaseEncryption:
    def __init__(self, master_key=None):
        """Initialize encryption with master key or generate new one"""
        if master_key:
            self.master_key = master_key
        else:
            # Try to get from environment or generate new
            self.master_key = os.getenv('DB_ENCRYPTION_KEY')
            if not self.master_key:
                self.master_key = Fernet.generate_key().decode()
                print(f"⚠️  Generated new encryption key: {self.master_key}")
                print("⚠️  Add this to your .env file as DB_ENCRYPTION_KEY")
        
        # Create Fernet cipher
        self.cipher = Fernet(self.master_key.encode())
    
    def encrypt(self, data: str) -> str:
        """Encrypt sensitive data"""
        if not data:
            return data
        try:
            encrypted = self.cipher.encrypt(data.encode())
            return base64.b64encode(encrypted).decode()
        except Exception as e:
            logging.error(f"Encryption error: {e}")
            return data
    
    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt sensitive data"""
        if not encrypted_data:
            return encrypted_data
        try:
            # Check if data is already encrypted (base64 format)
            if not self._is_encrypted(encrypted_data):
                return encrypted_data
            
            decoded = base64.b64decode(encrypted_data.encode())
            decrypted = self.cipher.decrypt(decoded)
            return decrypted.decode()
        except Exception as e:
            logging.error(f"Decryption error: {e}")
            return encrypted_data
    
    def _is_encrypted(self, data: str) -> bool:
        """Check if data appears to be encrypted"""
        try:
            # Try to decode as base64
            base64.b64decode(data.encode())
            return True
        except:
            return False
    
    def migrate_database(self, db_path: str):
        """Migrate existing database to use encryption"""
        print("🔐 Starting database encryption migration...")
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        try:
            # Get all sensitive data
            cursor.execute("SELECT id, access_url, key_id FROM keys")
            keys = cursor.fetchall()
            
            encrypted_count = 0
            for key_id, access_url, outline_key_id in keys:
                if access_url and not self._is_encrypted(access_url):
                    encrypted_url = self.encrypt(access_url)
                    cursor.execute("UPDATE keys SET access_url = ? WHERE id = ?", (encrypted_url, key_id))
                    encrypted_count += 1
                
                if outline_key_id and not self._is_encrypted(outline_key_id):
                    encrypted_outline_id = self.encrypt(outline_key_id)
                    cursor.execute("UPDATE keys SET key_id = ? WHERE id = ?", (encrypted_outline_id, key_id))
            
            # Encrypt server API URLs
            cursor.execute("SELECT id, api_url FROM servers")
            servers = cursor.fetchall()
            
            for server_id, api_url in servers:
                if api_url and not self._is_encrypted(api_url):
                    encrypted_url = self.encrypt(api_url)
                    cursor.execute("UPDATE servers SET api_url = ? WHERE id = ?", (encrypted_url, server_id))
                    encrypted_count += 1
            
            conn.commit()
            print(f"✅ Encrypted {encrypted_count} sensitive fields")
            
        except Exception as e:
            print(f"❌ Migration error: {e}")
            conn.rollback()
        finally:
            conn.close()
    
    def create_encrypted_connection(self, db_path: str):
        """Create a database connection with encryption wrapper"""
        return EncryptedDBConnection(db_path, self)

class EncryptedDBConnection:
    """Database connection wrapper that handles encryption/decryption"""
    
    def __init__(self, db_path: str, encryption: DatabaseEncryption):
        self.db_path = db_path
        self.encryption = encryption
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
    
    def execute(self, query: str, params=None):
        """Execute query with automatic decryption of results"""
        cursor = self.conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        return cursor
    
    def fetchone(self):
        """Fetch one row with decryption"""
        cursor = self.conn.cursor()
        row = cursor.fetchone()
        if row:
            return self._decrypt_row(row)
        return row
    
    def fetchall(self):
        """Fetch all rows with decryption"""
        cursor = self.conn.cursor()
        rows = cursor.fetchall()
        return [self._decrypt_row(row) for row in rows]
    
    def _decrypt_row(self, row):
        """Decrypt sensitive fields in a row"""
        if not row:
            return row
        
        # Convert row to dict for easier manipulation
        row_dict = dict(row)
        
        # Decrypt sensitive fields
        if 'access_url' in row_dict and row_dict['access_url']:
            row_dict['access_url'] = self.encryption.decrypt(row_dict['access_url'])
        
        if 'key_id' in row_dict and row_dict['key_id']:
            row_dict['key_id'] = self.encryption.decrypt(row_dict['key_id'])
        
        if 'api_url' in row_dict and row_dict['api_url']:
            row_dict['api_url'] = self.encryption.decrypt(row_dict['api_url'])
        
        return row_dict
    
    def commit(self):
        """Commit changes"""
        self.conn.commit()
    
    def close(self):
        """Close connection"""
        self.conn.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

# Global encryption instance
db_encryption = DatabaseEncryption()

def get_encrypted_db_connection(db_path: str):
    """Get encrypted database connection"""
    return db_encryption.create_encrypted_connection(db_path)

def encrypt_value(value: str) -> str:
    """Encrypt a single value"""
    return db_encryption.encrypt(value)

def decrypt_value(value: str) -> str:
    """Decrypt a single value"""
    return db_encryption.decrypt(value)

if __name__ == "__main__":
    # Test encryption
    test_data = "test_vpn_key_123"
    encrypted = encrypt_value(test_data)
    decrypted = decrypt_value(encrypted)
    
    print(f"Original: {test_data}")
    print(f"Encrypted: {encrypted}")
    print(f"Decrypted: {decrypted}")
    print(f"Match: {test_data == decrypted}") 