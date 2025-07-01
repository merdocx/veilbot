#!/usr/bin/env python3
"""
Enhanced Rate Limiter for VeilBot
Implements exponential backoff and IP-based blocking.
"""

import time
import threading
from collections import defaultdict, deque
from typing import Dict, Deque, Tuple
import logging

class EnhancedRateLimiter:
    def __init__(self):
        self.locks = defaultdict(threading.Lock)
        self.attempts = defaultdict(lambda: deque())
        self.blocked_ips = defaultdict(float)
        
        # Configuration
        self.max_attempts = 5  # Max attempts before blocking
        self.window_size = 60  # Time window in seconds
        self.block_duration = 300  # Block duration in seconds (5 minutes)
        self.exponential_backoff = True
        
        # Cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
    
    def is_allowed(self, identifier: str, action: str = "default") -> Tuple[bool, str]:
        """
        Check if request is allowed
        
        Returns:
            Tuple[bool, str]: (allowed, reason)
        """
        key = f"{identifier}:{action}"
        
        with self.locks[key]:
            now = time.time()
            
            # Check if IP is blocked
            if identifier in self.blocked_ips:
                block_until = self.blocked_ips[identifier]
                if now < block_until:
                    remaining = int(block_until - now)
                    return False, f"IP blocked for {remaining} seconds"
                else:
                    # Unblock expired IP
                    del self.blocked_ips[identifier]
            
            # Clean old attempts
            self._clean_old_attempts(key, now)
            
            # Check current attempts
            attempts = self.attempts[key]
            if len(attempts) >= self.max_attempts:
                # Block the IP
                self.blocked_ips[identifier] = now + self.block_duration
                
                # Clear attempts for this action
                self.attempts[key].clear()
                
                return False, f"Too many attempts. IP blocked for {self.block_duration} seconds"
            
            # Add current attempt
            attempts.append(now)
            
            # Calculate backoff if needed
            if self.exponential_backoff and len(attempts) > 1:
                backoff_time = min(2 ** (len(attempts) - 1), 30)  # Max 30 seconds
                return True, f"Backoff: {backoff_time}s"
            
            return True, "OK"
    
    def record_success(self, identifier: str, action: str = "default"):
        """Record successful attempt (resets backoff)"""
        key = f"{identifier}:{action}"
        
        with self.locks[key]:
            # Clear attempts on success
            self.attempts[key].clear()
    
    def _clean_old_attempts(self, key: str, now: float):
        """Remove attempts older than window_size"""
        attempts = self.attempts[key]
        
        # Remove attempts older than window_size
        while attempts and (now - attempts[0]) > self.window_size:
            attempts.popleft()
    
    def _cleanup_loop(self):
        """Background cleanup loop"""
        while True:
            try:
                time.sleep(60)  # Run every minute
                now = time.time()
                
                # Clean up blocked IPs
                expired_ips = [
                    ip for ip, block_until in self.blocked_ips.items()
                    if now > block_until
                ]
                for ip in expired_ips:
                    del self.blocked_ips[ip]
                
                # Clean up old attempts
                for key in list(self.attempts.keys()):
                    self._clean_old_attempts(key, now)
                    
                    # Remove empty attempt lists
                    if not self.attempts[key]:
                        del self.attempts[key]
                
                if expired_ips:
                    logging.info(f"Cleaned up {len(expired_ips)} expired IP blocks")
                    
            except Exception as e:
                logging.error(f"Rate limiter cleanup error: {e}")

# Global rate limiter instance
rate_limiter = EnhancedRateLimiter()

def check_rate_limit(identifier: str, action: str = "default") -> Tuple[bool, str]:
    """Check if request is allowed by rate limiter"""
    return rate_limiter.is_allowed(identifier, action)

def record_success(identifier: str, action: str = "default"):
    """Record successful attempt"""
    rate_limiter.record_success(identifier, action)

def get_rate_limit_stats(identifier: str = None) -> Dict:
    """Get rate limiter statistics"""
    return rate_limiter.get_stats(identifier)
