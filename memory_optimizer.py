"""
Модуль для оптимизации памяти VeilBot
"""

import gc
import sys
import weakref
from typing import Any, Dict, Optional, Callable
from functools import wraps
import logging

logger = logging.getLogger(__name__)

class MemoryOptimizer:
    """Класс для оптимизации использования памяти"""
    
    def __init__(self):
        self._cache = {}
        self._lazy_loaders = {}
        self._memory_stats = {
            'objects_created': 0,
            'objects_cached': 0,
            'cache_hits': 0,
            'cache_misses': 0
        }
    
    def lazy_load(self, key: str, loader_func: Callable, *args, **kwargs):
        """Lazy loading с кэшированием"""
        if key not in self._cache:
            try:
                self._cache[key] = loader_func(*args, **kwargs)
                self._memory_stats['objects_created'] += 1
                self._memory_stats['cache_misses'] += 1
                logger.debug(f"Lazy loaded: {key}")
            except Exception as e:
                logger.error(f"Error lazy loading {key}: {e}")
                return None
        else:
            self._memory_stats['cache_hits'] += 1
        
        return self._cache[key]
    
    def get_cached(self, key: str) -> Optional[Any]:
        """Получить объект из кэша"""
        return self._cache.get(key)
    
    def clear_cache(self, key: Optional[str] = None):
        """Очистить кэш"""
        if key:
            if key in self._cache:
                del self._cache[key]
                logger.debug(f"Cleared cache for: {key}")
        else:
            self._cache.clear()
            logger.debug("Cleared all cache")
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Получить статистику использования памяти"""
        return {
            **self._memory_stats,
            'cache_size': len(self._cache),
            'cache_keys': list(self._cache.keys())
        }
    
    def optimize_memory(self):
        """Принудительная оптимизация памяти"""
        # Очистка кэша
        self.clear_cache()
        
        # Принудительный сбор мусора
        collected = gc.collect()
        logger.info(f"Memory optimization: collected {collected} objects")
        
        return collected

# Глобальный экземпляр оптимизатора
memory_optimizer = MemoryOptimizer()

def lazy_load_decorator(key: str):
    """Декоратор для lazy loading функций"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return memory_optimizer.lazy_load(key, func, *args, **kwargs)
        return wrapper
    return decorator

def memory_efficient(func):
    """Декоратор для оптимизации памяти функций"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Выполняем функцию
        result = func(*args, **kwargs)
        
        # Принудительная очистка памяти после выполнения
        if hasattr(memory_optimizer, '_cache') and len(memory_optimizer._cache) > 100:
            # Если кэш слишком большой, очищаем старые записи
            old_keys = list(memory_optimizer._cache.keys())[:50]
            for key in old_keys:
                memory_optimizer.clear_cache(key)
        
        return result
    return wrapper

class LazyServiceLoader:
    """Класс для lazy loading сервисов"""
    
    def __init__(self):
        self._services = {}
    
    def get_payment_service(self):
        """Lazy loading для платежного сервиса"""
        if 'payment_service' not in self._services:
            try:
                from payments.config import initialize_payment_module
                from payments.adapters.legacy_adapter import set_payment_service
                
                payment_service = initialize_payment_module()
                service_instance = payment_service.create_payment_service()
                set_payment_service(service_instance)
                
                self._services['payment_service'] = service_instance
                logger.info("Payment service loaded lazily")
            except Exception as e:
                logger.error(f"Error loading payment service: {e}")
                return None
        
        return self._services['payment_service']
    
    def get_vpn_service(self):
        """Lazy loading для VPN сервиса"""
        if 'vpn_service' not in self._services:
            try:
                from vpn_protocols import ProtocolFactory
                
                self._services['vpn_service'] = ProtocolFactory()
                logger.info("VPN service loaded lazily")
            except Exception as e:
                logger.error(f"Error loading VPN service: {e}")
                return None
        
        return self._services['vpn_service']
    
    def get_security_logger(self):
        """Lazy loading для системы безопасности"""
        if 'security_logger' not in self._services:
            try:
                from security_logger import security_logger
                
                self._services['security_logger'] = security_logger
                logger.info("Security logger loaded lazily")
            except Exception as e:
                logger.error(f"Error loading security logger: {e}")
                return None
        
        return self._services['security_logger']
    
    def clear_services(self):
        """Очистить все сервисы"""
        self._services.clear()
        logger.info("All services cleared")

# Глобальный экземпляр загрузчика сервисов
service_loader = LazyServiceLoader()

def optimize_imports():
    """Оптимизация импортов - удаление неиспользуемых"""
    
    # Список модулей, которые можно импортировать лениво
    lazy_modules = {
        'payments.config': 'initialize_payment_module',
        'payments.adapters.legacy_adapter': 'set_payment_service',
        'vpn_protocols': 'ProtocolFactory',
        'security_logger': 'security_logger'
    }
    
    # Проверяем, какие модули действительно используются
    used_modules = set()
    
    for module_name, func_name in lazy_modules.items():
        try:
            module = __import__(module_name, fromlist=[func_name])
            if hasattr(module, func_name):
                used_modules.add(module_name)
        except ImportError:
            logger.warning(f"Module {module_name} not available")
    
    return used_modules

def get_memory_usage():
    """Получить информацию об использовании памяти"""
    import psutil
    import os
    
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    
    return {
        'rss': memory_info.rss,  # Resident Set Size
        'vms': memory_info.vms,  # Virtual Memory Size
        'percent': process.memory_percent(),
        'available': psutil.virtual_memory().available
    }

def log_memory_usage():
    """Логировать использование памяти"""
    memory_info = get_memory_usage()
    
    logger.info(f"Memory usage: RSS={memory_info['rss'] / 1024 / 1024:.2f}MB, "
                f"VMS={memory_info['vms'] / 1024 / 1024:.2f}MB, "
                f"Percent={memory_info['percent']:.2f}%")

# Функции для удобного использования
def get_payment_service():
    """Получить платежный сервис с lazy loading"""
    return service_loader.get_payment_service()

def get_vpn_service():
    """Получить VPN сервис с lazy loading"""
    return service_loader.get_vpn_service()

def get_security_logger():
    """Получить логгер безопасности с lazy loading"""
    return service_loader.get_security_logger()

def optimize_memory():
    """Принудительная оптимизация памяти"""
    return memory_optimizer.optimize_memory()

def get_memory_stats():
    """Получить статистику памяти"""
    return {
        'optimizer_stats': memory_optimizer.get_memory_stats(),
        'memory_usage': get_memory_usage()
    }
