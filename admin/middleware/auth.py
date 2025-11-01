"""
Модуль авторизации и аутентификации для админки
"""
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from functools import wraps
import logging

logger = logging.getLogger(__name__)


def require_auth(request: Request):
    """
    Dependency для проверки авторизации администратора
    
    Использование:
        @router.get("/some_route")
        async def some_route(request: Request, auth: bool = Depends(require_auth)):
            # ...
    """
    if not request.session.get("admin_logged_in"):
        raise HTTPException(status_code=403, detail="Not authenticated")
    return True


def get_current_admin(request: Request):
    """
    Получить информацию о текущем администраторе
    
    Returns:
        dict: Информация о текущем администраторе или None
    """
    if not request.session.get("admin_logged_in"):
        return None
    return {
        "username": request.session.get("admin_username", "admin"),
        "logged_in": True
    }


def check_auth(request: Request):
    """
    Проверка авторизации (для HTML страниц с редиректом)
    
    Returns:
        bool: True если авторизован, иначе выбрасывает RedirectResponse
    
    Usage:
        @router.get("/some_page")
        async def some_page(request: Request):
            if not check_auth(request):
                return RedirectResponse(url="/login", status_code=303)
            # ...
    """
    if not request.session.get("admin_logged_in"):
        raise HTTPException(status_code=403, detail="Not authenticated")
    return True

