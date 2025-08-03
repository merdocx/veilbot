#!/usr/bin/env python3
"""
Simple test script for CI/CD pipeline
This script tests basic functionality without connecting to Telegram API
"""

import sys
import os

def test_imports():
    """Test that all required modules can be imported"""
    try:
        import aiogram
        import fastapi
        import uvicorn
        import aiohttp
        import yookassa
        import cryptography
        print("✅ All required modules imported successfully")
        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False

def test_syntax():
    """Test syntax of main Python files"""
    files_to_test = [
        'bot.py',
        'vpn_protocols.py',
        'admin/admin_routes.py',
        'config.py',
        'outline.py'
    ]
    
    all_passed = True
    for file_path in files_to_test:
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    compile(f.read(), file_path, 'exec')
                print(f"✅ {file_path} - syntax OK")
            except SyntaxError as e:
                print(f"❌ {file_path} - syntax error: {e}")
                all_passed = False
        else:
            print(f"⚠️  {file_path} - file not found")
    
    return all_passed

def test_config():
    """Test configuration file structure"""
    try:
        import config
        print("✅ config.py loaded successfully")
        return True
    except Exception as e:
        print(f"❌ config.py error: {e}")
        return False

def main():
    """Run all tests"""
    print("🚀 Starting CI tests...")
    
    tests = [
        ("Import test", test_imports),
        ("Syntax test", test_syntax),
        ("Config test", test_config)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n📋 Running {test_name}...")
        if test_func():
            passed += 1
        else:
            print(f"❌ {test_name} failed")
    
    print(f"\n📊 Test results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        return 0
    else:
        print("💥 Some tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())
