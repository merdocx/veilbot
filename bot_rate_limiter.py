"""
Rate limiting для бота VeilBot
"""
import time
import logging
from typing import Dict, Tuple
from collections import defaultdict
from functools import wraps
from aiogram import types

logger = logging.getLogger(__name__)


class RateLimiter:
    """Простой rate limiter для бота"""
    
    def __init__(self):
        # Словарь: user_id -> [(timestamp, action), ...]
        self._requests: Dict[int, list] = defaultdict(list)
        self._limits: Dict[str, Tuple[int, int]] = {
            # action: (max_requests, window_seconds)
            "buy": (5, 60),  # 5 покупок в минуту
            "keys": (10, 60),  # 10 запросов "Мои ключи" в минуту
            "renew": (3, 60),  # 3 продления в минуту
            "reissue": (3, 300),  # 3 перевыпуска в 5 минут
            "change_protocol": (5, 300),  # 5 смен протокола в 5 минут
            "change_country": (5, 300),  # 5 смен страны в 5 минут
            "default": (20, 60),  # 20 запросов в минуту по умолчанию
        }
    
    def _cleanup_old_requests(self, user_id: int, action: str):
        """Удаляет старые запросы из истории"""
        window = self._limits.get(action, self._limits["default"])[1]
        cutoff_time = time.time() - window
        
        if user_id in self._requests:
            self._requests[user_id] = [
                (ts, act) for ts, act in self._requests[user_id]
                if ts > cutoff_time or act != action
            ]
    
    def is_allowed(self, user_id: int, action: str = "default") -> bool:
        """
        Проверяет, разрешен ли запрос
        
        Args:
            user_id: ID пользователя
            action: Тип действия
            
        Returns:
            True если запрос разрешен, False если превышен лимит
        """
        max_requests, window = self._limits.get(action, self._limits["default"])
        
        # Очищаем старые запросы
        self._cleanup_old_requests(user_id, action)
        
        # Считаем запросы для этого действия
        current_time = time.time()
        user_requests = [
            ts for ts, act in self._requests[user_id]
            if act == action and current_time - ts < window
        ]
        
        if len(user_requests) >= max_requests:
            logger.warning(
                f"Rate limit exceeded for user {user_id}, action {action}: "
                f"{len(user_requests)}/{max_requests} requests in {window}s"
            )
            return False
        
        # Добавляем новый запрос
        self._requests[user_id].append((current_time, action))
        return True
    
    def get_remaining_time(self, user_id: int, action: str = "default") -> int:
        """
        Возвращает время до сброса лимита в секундах
        
        Args:
            user_id: ID пользователя
            action: Тип действия
            
        Returns:
            Время в секундах до следующего разрешенного запроса
        """
        _, window = self._limits.get(action, self._limits["default"])
        
        # Очищаем старые запросы
        self._cleanup_old_requests(user_id, action)
        
        # Находим самый старый запрос для этого действия
        user_requests = [
            ts for ts, act in self._requests[user_id]
            if act == action
        ]
        
        if not user_requests:
            return 0
        
        oldest_request = min(user_requests)
        elapsed = time.time() - oldest_request
        remaining = max(0, window - int(elapsed))
        
        return remaining


# Глобальный экземпляр rate limiter
_rate_limiter = RateLimiter()


def rate_limit(action: str = "default", max_requests: int = None, window_seconds: int = None):
    """
    Декоратор для rate limiting обработчиков бота
    
    Args:
        action: Тип действия (для разных лимитов)
        max_requests: Максимальное количество запросов (переопределяет дефолт)
        window_seconds: Окно времени в секундах (переопределяет дефолт)
    
    Usage:
        @rate_limit("buy")
        async def handle_buy_menu(message: types.Message):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(message: types.Message, *args, **kwargs):
            user_id = message.from_user.id
            
            # Если указаны кастомные лимиты, создаем временный лимитер
            if max_requests is not None and window_seconds is not None:
                temp_limiter = RateLimiter()
                temp_limiter._limits[action] = (max_requests, window_seconds)
                limiter = temp_limiter
            else:
                limiter = _rate_limiter
            
            if not limiter.is_allowed(user_id, action):
                remaining = limiter.get_remaining_time(user_id, action)
                minutes = remaining // 60
                seconds = remaining % 60
                
                if minutes > 0:
                    time_str = f"{minutes} мин. {seconds} сек."
                else:
                    time_str = f"{seconds} сек."
                
                await message.answer(
                    f"⏱️ Слишком много запросов. Попробуйте через {time_str}.",
                    reply_markup=None
                )
                return
            
            return await func(message, *args, **kwargs)
        
        return wrapper
    return decorator

