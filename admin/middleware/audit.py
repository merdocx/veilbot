"""
Модуль аудита действий администратора
"""
import logging
from fastapi import Request
from datetime import datetime

logger = logging.getLogger(__name__)


def log_admin_action(request: Request, action: str, details: str = ""):
    """
    Логирование действий администратора
    
    Args:
        request: FastAPI Request объект
        action: Тип действия (LOGIN_SUCCESS, ADD_KEY, etc.)
        details: Дополнительные детали действия
    """
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    # Получаем username из сессии, если доступен
    username = request.session.get("admin_username", "unknown")
    
    log_message = (
        f"ADMIN_ACTION | User: {username} | IP: {client_ip} | "
        f"Action: {action} | Details: {details} | "
        f"User-Agent: {user_agent[:100]}"
    )
    
    logger.info(log_message)
    
    # Дополнительное логирование критичных действий
    critical_actions = [
        "LOGIN_SUCCESS", "LOGIN_FAILED", "ADD_SERVER", "DELETE_SERVER",
        "ADD_TARIFF", "DELETE_TARIFF", "CSRF_ATTACK"
    ]
    
    if action in critical_actions:
        logger.warning(f"CRITICAL: {log_message}")

