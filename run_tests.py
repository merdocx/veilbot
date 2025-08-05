#!/usr/bin/env python3
"""
Test runner for VeilBot project
"""

import os
import sys
import unittest
import subprocess

def run_basic_tests():
    """Run basic unit tests"""
    print("🧪 Running basic unit tests...")
    loader = unittest.TestLoader()
    suite = loader.discover('.', pattern='test_*.py')
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()

def run_configuration_tests():
    """Test configuration with mock environment variables"""
    print("🔧 Testing configuration validation...")
    try:
        # Set mock environment variables
        os.environ['TELEGRAM_BOT_TOKEN'] = '123456789:KhO-lzCMPJqZF8CD07t-RSEgZJol2i7_yez'
        os.environ['YOOKASSA_SHOP_ID'] = '123456'
        os.environ['YOOKASSA_API_KEY'] = 'test_1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef'
        os.environ['YOOKASSA_RETURN_URL'] = 'https://example.com'
        os.environ['ADMIN_ID'] = '123456789'
        
        from config import validate_configuration
        result = validate_configuration()
        
        if result['is_valid']:
            print("✅ Configuration validation passed")
            return True
        else:
            print("❌ Configuration validation failed:")
            for error in result['errors']:
                print(f"  - {error}")
            return False
    except Exception as e:
        print(f"❌ Configuration test failed: {e}")
        return False

def run_import_tests():
    """Test that all modules can be imported"""
    print("📦 Testing module imports...")
    try:
        # Test basic imports that don't require environment variables
        from config import PROTOCOLS
        from db import init_db
        from validators import input_validator, db_validator, business_validator
        from vpn_protocols import ProtocolFactory
        from utils import get_db_cursor
        print("✅ All basic imports successful")
        return True
    except Exception as e:
        print(f"❌ Import test failed: {e}")
        return False

def run_syntax_tests():
    """Test for syntax errors in Python files"""
    print("🔍 Checking for syntax errors...")
    python_files = [
        'bot.py', 'config.py', 'db.py', 'payment.py', 
        'validators.py', 'vpn_protocols.py', 'utils.py',
        'test_bot.py', 'run_tests.py'
    ]
    
    success = True
    for file_path in python_files:
        if os.path.exists(file_path):
            try:
                subprocess.run([sys.executable, '-m', 'py_compile', file_path], 
                             check=True, capture_output=True)
                print(f"✅ {file_path} - No syntax errors")
            except subprocess.CalledProcessError as e:
                print(f"❌ {file_path} - Syntax error: {e}")
                success = False
        else:
            print(f"⚠️ {file_path} - File not found")
    
    return success

def run_structure_tests():
    """Test project structure"""
    print("📋 Testing project structure...")
    
    required_files = [
        'bot.py', 'config.py', 'db.py', 'payment.py', 
        'requirements.txt', '.env.example'
    ]
    
    required_dirs = ['admin', 'docs', 'scripts', 'setup']
    
    success = True
    
    # Check required files
    for file_path in required_files:
        if os.path.exists(file_path):
            print(f"✅ {file_path} exists")
        else:
            print(f"❌ {file_path} missing")
            success = False
    
    # Check required directories
    for dir_path in required_dirs:
        if os.path.exists(dir_path):
            print(f"✅ {dir_path}/ exists")
        else:
            print(f"❌ {dir_path}/ missing")
            success = False
    
    return success

def main():
    """Main test runner"""
    print("🚀 Starting VeilBot test suite...")
    print("=" * 50)
    
    tests = [
        ("Basic Unit Tests", run_basic_tests),
        ("Configuration Tests", run_configuration_tests),
        ("Import Tests", run_import_tests),
        ("Syntax Tests", run_syntax_tests),
        ("Structure Tests", run_structure_tests),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n📝 {test_name}")
        print("-" * 30)
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} failed with exception: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 50)
    print("📊 Test Results Summary:")
    print("=" * 50)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
        if result:
            passed += 1
    
    print(f"\n🎯 Overall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print("💥 Some tests failed!")
        return 1

if __name__ == '__main__':
    sys.exit(main()) 