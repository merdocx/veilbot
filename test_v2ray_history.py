#!/usr/bin/env python3
"""
Test script for V2Ray historical traffic data endpoints
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vpn_protocols import ProtocolFactory

async def test_v2ray_history_endpoints():
    """Test V2Ray historical traffic data endpoints"""
    
    # Get V2Ray server configuration from database
    import sqlite3
    
    conn = sqlite3.connect('vpn.db')
    cursor = conn.cursor()
    
    # Get V2Ray server with API key
    cursor.execute("""
        SELECT s.id, s.name, s.api_url, s.api_key
        FROM servers s
        WHERE s.protocol = 'v2ray' AND s.api_url IS NOT NULL
        LIMIT 1
    """)
    
    server_data = cursor.fetchone()
    conn.close()
    
    if not server_data:
        print("❌ No V2Ray server found with API configuration")
        return
    
    server_id, server_name, api_url, api_key = server_data
    
    print(f"🔧 Testing V2Ray server: {server_name}")
    print(f"📡 API URL: {api_url}")
    print(f"🔑 API Key: {'*' * 10 if api_key else 'Not set'}")
    print("=" * 60)
    
    # Create V2Ray protocol instance
    server_config = {
        'api_url': api_url,
        'api_key': api_key
    }
    
    v2ray = ProtocolFactory.create_protocol('v2ray', server_config)
    
    # Test 1: Get traffic history (all keys)
    print("\n📊 Test 1: Getting traffic history (all keys)")
    print("-" * 40)
    try:
        history = await v2ray.get_traffic_history()
        if history:
            data = history.get('data', {})
            total_keys = data.get('total_keys', 0)
            active_keys = data.get('active_keys', 0)
            total_traffic = data.get('total_traffic_formatted', '0 B')
            
            print(f"✅ Traffic history retrieved successfully")
            print(f"   Total keys: {total_keys}")
            print(f"   Active keys: {active_keys}")
            print(f"   Total traffic: {total_traffic}")
            
            # Show individual key details
            keys = data.get('keys', [])
            if keys:
                print(f"\n   Individual keys:")
                for key in keys[:3]:  # Show first 3 keys
                    key_name = key.get('key_name', 'Unknown')
                    port = key.get('port', 0)
                    total = key.get('total_traffic', {}).get('total_formatted', '0 B')
                    connections = key.get('total_traffic', {}).get('total_connections', 0)
                    print(f"     - {key_name} (port {port}): {total}, {connections} connections")
        else:
            print("❌ Failed to get traffic history")
    except Exception as e:
        print(f"❌ Error getting traffic history: {e}")
    
    # Test 2: Get daily traffic stats
    print("\n📈 Test 2: Getting daily traffic stats")
    print("-" * 40)
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        daily_stats = await v2ray.get_daily_traffic_stats(today)
        if daily_stats:
            data = daily_stats.get('data', {})
            date = data.get('date', 'Unknown')
            total_bytes = data.get('total_bytes', 0)
            total_formatted = data.get('total_formatted', '0 B')
            active_keys = data.get('active_keys', 0)
            total_sessions = data.get('total_sessions', 0)
            
            print(f"✅ Daily stats retrieved successfully")
            print(f"   Date: {date}")
            print(f"   Total traffic: {total_formatted}")
            print(f"   Active keys: {active_keys}")
            print(f"   Total sessions: {total_sessions}")
        else:
            print("❌ Failed to get daily traffic stats")
    except Exception as e:
        print(f"❌ Error getting daily traffic stats: {e}")
    
    # Test 3: Get all keys and test individual key history
    print("\n🔑 Test 3: Getting individual key traffic history")
    print("-" * 40)
    try:
        all_keys = await v2ray.get_all_keys()
        if all_keys:
            print(f"✅ Found {len(all_keys)} keys")
            
            # Test first key's history
            if len(all_keys) > 0:
                first_key = all_keys[0]
                key_id = first_key.get('id')
                key_name = first_key.get('name', 'Unknown')
                
                print(f"   Testing key: {key_name} (ID: {key_id})")
                
                key_history = await v2ray.get_key_traffic_history(key_id)
                if key_history:
                    data = key_history.get('data', {})
                    key_uuid = data.get('key_uuid', 'Unknown')
                    port = data.get('port', 0)
                    total_traffic = data.get('total_traffic', {})
                    total_formatted = total_traffic.get('total_formatted', '0 B')
                    total_connections = total_traffic.get('total_connections', 0)
                    
                    print(f"   ✅ Key history retrieved successfully")
                    print(f"      UUID: {key_uuid}")
                    print(f"      Port: {port}")
                    print(f"      Total traffic: {total_formatted}")
                    print(f"      Total connections: {total_connections}")
                    
                    # Show daily stats if available
                    daily_stats = data.get('daily_stats', {})
                    if daily_stats:
                        print(f"      Daily stats available for {len(daily_stats)} days")
                else:
                    print(f"   ❌ Failed to get key history")
            else:
                print("   No keys found to test")
        else:
            print("❌ Failed to get all keys")
    except Exception as e:
        print(f"❌ Error testing individual key history: {e}")
    
    # Test 4: Test cleanup functionality (dry run)
    print("\n🧹 Test 4: Testing traffic history cleanup")
    print("-" * 40)
    try:
        # This is a dry run - we won't actually clean up data
        print("   ⚠️  Cleanup test skipped (would delete data)")
        print("   To test cleanup, uncomment the following lines:")
        print("   # cleanup_result = await v2ray.cleanup_traffic_history(days_to_keep=30)")
        print("   # print(f'Cleanup result: {cleanup_result}')")
    except Exception as e:
        print(f"❌ Error testing cleanup: {e}")
    
    print("\n" + "=" * 60)
    print("🎯 V2Ray Historical Traffic Data Test Complete!")
    print("=" * 60)

async def main():
    """Main function"""
    print("🚀 Starting V2Ray Historical Traffic Data Test")
    print("=" * 60)
    
    try:
        await test_v2ray_history_endpoints()
    except Exception as e:
        print(f"💥 Test failed with error: {e}")
        return 1
    
    return 0

if __name__ == '__main__':
    exit_code = asyncio.run(main())
    sys.exit(exit_code) 