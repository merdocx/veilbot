"""
Защита от брутфорса для админки
"""
import time
from collections import defaultdict
from datetime import datetime, timedelta
import logging
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse, HTMLResponse

logger = logging.getLogger(__name__)

# In-memory хранилище попыток входа (в продакшене лучше использовать Redis)
_login_attempts = defaultdict(list)
_blocked_ips = {}  # IP -> unblock_timestamp


class BruteforceProtection:
    """
    Защита от брутфорса на основе IP адреса
    
    Правила:
    - 5 неудачных попыток входа за 15 минут = блокировка на 1 час
    - Блокировка увеличивается экспоненциально при повторных нарушениях
    """
    
    MAX_ATTEMPTS = 5
    ATTEMPT_WINDOW = 900  # 15 минут в секундах
    BLOCK_DURATION = 3600  # 1 час в секундах
    MAX_BLOCK_DURATION = 86400  # Максимум 24 часа
    
    @staticmethod
    def get_client_ip(request: Request) -> str:
        """Получить IP адрес клиента с учётом прокси"""
        # Проверяем заголовки прокси
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Берём первый IP из списка
            return forwarded_for.split(",")[0].strip()
        
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        
        # Прямое соединение
        if request.client:
            return request.client.host
        
        return "unknown"
    
    @staticmethod
    def is_ip_blocked(ip: str) -> tuple[bool, int | None]:
        """
        Проверка, заблокирован ли IP
        
        Returns:
            (is_blocked, seconds_until_unblock)
        """
        if ip not in _blocked_ips:
            return False, None
        
        unblock_time = _blocked_ips[ip]
        now = time.time()
        
        if now < unblock_time:
            return True, int(unblock_time - now)
        
        # Время блокировки истекло
        del _blocked_ips[ip]
        return False, None
    
    @staticmethod
    def record_failed_login(ip: str):
        """Записать неудачную попытку входа"""
        now = time.time()
        
        # Очищаем старые попытки (старше окна)
        attempts = _login_attempts[ip]
        attempts = [t for t in attempts if now - t < BruteforceProtection.ATTEMPT_WINDOW]
        attempts.append(now)
        _login_attempts[ip] = attempts
        
        # Если превышен лимит, блокируем IP
        if len(attempts) >= BruteforceProtection.MAX_ATTEMPTS:
            # Вычисляем время блокировки (экспоненциально увеличивается)
            violations = sum(1 for blocked_time in _blocked_ips.values() if blocked_time > now)
            block_duration = min(
                BruteforceProtection.BLOCK_DURATION * (2 ** violations),
                BruteforceProtection.MAX_BLOCK_DURATION
            )
            
            unblock_time = now + block_duration
            _blocked_ips[ip] = unblock_time
            
            logger.warning(
                f"IP {ip} blocked for {block_duration} seconds due to brute force attempts. "
                f"Total violations: {violations + 1}"
            )
    
    @staticmethod
    def record_successful_login(ip: str):
        """Записать успешный вход (очищает попытки)"""
        if ip in _login_attempts:
            del _login_attempts[ip]
        # Не удаляем из _blocked_ips, пусть время блокировки истечёт естественным образом
    
    @staticmethod
    def check_bruteforce(request: Request) -> None:
        """
        Проверить защиту от брутфорса и выбросить исключение если IP заблокирован
        
        Raises:
            HTTPException: если IP заблокирован
        """
        ip = BruteforceProtection.get_client_ip(request)
        
        if ip == "unknown":
            # Если не можем определить IP, пропускаем (но логируем)
            logger.warning("Could not determine client IP for bruteforce check")
            return
        
        is_blocked, seconds_remaining = BruteforceProtection.is_ip_blocked(ip)
        
        if is_blocked:
            hours = seconds_remaining // 3600
            minutes = (seconds_remaining % 3600) // 60
            
            error_message = (
                f"Доступ временно заблокирован из-за множественных "
                f"неудачных попыток входа. "
                f"Попробуйте снова через {hours}ч {minutes}м"
            )
            
            logger.warning(
                f"Blocked IP {ip} attempted to access login. "
                f"Unblock in {seconds_remaining} seconds"
            )
            
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=error_message
            )


def get_bruteforce_protection() -> BruteforceProtection:
    """Получить экземпляр защиты от брутфорса"""
    return BruteforceProtection

