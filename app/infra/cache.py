"""
Simple in-memory cache for V2Ray traffic data
"""
import time
import threading
from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class CacheEntry:
    """Cache entry with timestamp"""
    data: Any
    timestamp: float
    ttl: float

class SimpleCache:
    """Thread-safe in-memory cache"""
    
    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired"""
        with self._lock:
            if key not in self._cache:
                return None
            
            entry = self._cache[key]
            if time.time() - entry.timestamp > entry.ttl:
                del self._cache[key]
                return None
            
            return entry.data
    
    def set(self, key: str, value: Any, ttl: float = 300) -> None:
        """Set value in cache with TTL"""
        with self._lock:
            self._cache[key] = CacheEntry(
                data=value,
                timestamp=time.time(),
                ttl=ttl
            )
    
    def delete(self, key: str) -> None:
        """Delete key from cache"""
        with self._lock:
            self._cache.pop(key, None)
    
    def clear(self) -> None:
        """Clear all cache entries"""
        with self._lock:
            self._cache.clear()
    
    def cleanup_expired(self) -> int:
        """Remove expired entries, return count of removed items"""
        with self._lock:
            now = time.time()
            expired_keys = [
                key for key, entry in self._cache.items()
                if now - entry.timestamp > entry.ttl
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)
    
    def size(self) -> int:
        """Get current cache size"""
        with self._lock:
            return len(self._cache)

# Global cache instance
traffic_cache = SimpleCache()

def get_v2ray_traffic_cache_key(server_id: int, server_config: Dict[str, str]) -> str:
    """Generate cache key for V2Ray server traffic"""
    api_url = server_config.get('api_url', '')
    return f"v2ray_traffic:{server_id}:{api_url}"

def get_cached_v2ray_traffic(server_id: int, server_config: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Get cached V2Ray traffic data"""
    key = get_v2ray_traffic_cache_key(server_id, server_config)
    return traffic_cache.get(key)

def cache_v2ray_traffic(server_id: int, server_config: Dict[str, str], data: Dict[str, Any], ttl: int = 300) -> None:
    """Cache V2Ray traffic data"""
    key = get_v2ray_traffic_cache_key(server_id, server_config)
    traffic_cache.set(key, data, ttl)

def invalidate_v2ray_traffic_cache(server_id: int, server_config: Dict[str, str]) -> None:
    """Invalidate V2Ray traffic cache for specific server"""
    key = get_v2ray_traffic_cache_key(server_id, server_config)
    traffic_cache.delete(key)
