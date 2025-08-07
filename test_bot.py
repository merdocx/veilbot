#!/usr/bin/env python3
"""
Basic tests for VeilBot project
"""

import unittest
import os
import sys
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

class TestConfig(unittest.TestCase):
    """Test configuration loading"""
    
    def test_config_import(self):
        """Test that config can be imported"""
        try:
            from config import TELEGRAM_BOT_TOKEN, PROTOCOLS, validate_configuration
            self.assertTrue(True, "Config imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import config: {e}")
    
    def test_protocols_config(self):
        """Test protocols configuration"""
        from config import PROTOCOLS
        self.assertIn('outline', PROTOCOLS)
        self.assertIn('v2ray', PROTOCOLS)
        self.assertIn('name', PROTOCOLS['outline'])
        self.assertIn('name', PROTOCOLS['v2ray'])

class TestDatabase(unittest.TestCase):
    """Test database functionality"""
    
    def test_db_import(self):
        """Test that database module can be imported"""
        try:
            from db import init_db
            self.assertTrue(True, "Database module imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import database module: {e}")

class TestValidators(unittest.TestCase):
    """Test validation functions"""
    
    def test_validators_import(self):
        """Test that validators can be imported"""
        try:
            from validators import input_validator, db_validator, business_validator
            self.assertTrue(True, "Validators imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import validators: {e}")

class TestVPNProtocols(unittest.TestCase):
    """Test VPN protocols"""
    
    def test_vpn_protocols_import(self):
        """Test that VPN protocols can be imported"""
        try:
            from vpn_protocols import ProtocolFactory, get_protocol_instructions
            self.assertTrue(True, "VPN protocols imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import VPN protocols: {e}")
    
    def test_v2ray_new_methods_exist(self):
        """Test that new V2Ray historical methods exist"""
        try:
            from vpn_protocols import V2RayProtocol
            # Check if new methods exist
            methods = [
                'get_traffic_history',
                'get_key_traffic_history', 
                'get_daily_traffic_stats',
                'reset_key_traffic_history',
                'cleanup_traffic_history',
                'get_key_monthly_traffic',
                'get_monthly_traffic'
            ]
            for method in methods:
                self.assertTrue(hasattr(V2RayProtocol, method), f"Method {method} not found")
            self.assertTrue(True, "All new V2Ray methods exist")
        except ImportError as e:
            self.fail(f"Failed to import V2RayProtocol: {e}")

class TestPayment(unittest.TestCase):
    """Test payment functionality"""
    
    def test_payment_module_import(self):
        """Test that new payment module can be imported"""
        try:
            from payments.config import initialize_payment_module
            self.assertTrue(True, "New payment module imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import new payment module: {e}")
    
    def test_legacy_adapter_import(self):
        """Test that legacy adapter can be imported"""
        try:
            from payments.adapters.legacy_adapter import LegacyPaymentAdapter
            self.assertTrue(True, "Legacy adapter imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import legacy adapter: {e}")

class TestUtils(unittest.TestCase):
    """Test utility functions"""
    
    def test_utils_import(self):
        """Test that utils can be imported"""
        try:
            from utils import get_db_cursor
            self.assertTrue(True, "Utils imported successfully")
        except ImportError as e:
            self.fail(f"Failed to import utils: {e}")

class TestProjectStructure(unittest.TestCase):
    """Test project structure"""
    
    def test_required_files_exist(self):
        """Test that required files exist"""
        required_files = [
            'bot.py',
            'config.py',
            'db.py',
            'requirements.txt',
            '.env.example'
        ]
        
        for file_path in required_files:
            self.assertTrue(
                os.path.exists(file_path),
                f"Required file {file_path} does not exist"
            )
    
    def test_payments_module_exists(self):
        """Test that payments module exists"""
        self.assertTrue(
            os.path.exists('payments'),
            "Payments module directory does not exist"
        )
        self.assertTrue(
            os.path.exists('payments/__init__.py'),
            "Payments module __init__.py does not exist"
        )
    
    def test_directories_exist(self):
        """Test that required directories exist"""
        required_dirs = [
            'admin',
            'docs'
        ]
        
        for dir_path in required_dirs:
            self.assertTrue(
                os.path.exists(dir_path),
                f"Required directory {dir_path} does not exist"
            )
        
        # Check optional directories (may be empty after cleanup)
        optional_dirs = [
            'scripts',
            'setup'
        ]
        
        for dir_path in optional_dirs:
            if os.path.exists(dir_path):
                print(f"✅ {dir_path} exists (may be empty)")
            else:
                print(f"⚠️ {dir_path} missing (optional after cleanup)")

if __name__ == '__main__':
    # Run tests
    unittest.main(verbosity=2) 