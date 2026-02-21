"""
Маршруты для очистки данных
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
import sys
import os
import time
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.settings import settings
from app.repositories.subscription_repository import SubscriptionRepository

from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token, validate_csrf_token
from ..dependencies.templates import templates
from .subscriptions import _delete_subscription_internal

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH
DB_PATH = DATABASE_PATH
logger = logging.getLogger(__name__)


@router.get("/cleanup")
async def cleanup_page(request: Request):
    """Страница очистки истекших подписок"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    return templates.TemplateResponse("cleanup.html", {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "expired_count": 0,
        "deleted_subscriptions": 0,
        "deleted_from_v2ray": 0,
        "deleted_keys_from_db": 0,
        "errors": []
    })


@router.post("/cleanup")
async def cleanup(request: Request, csrf_token: str = Form(...)):
    """
    Очистка по истекшим подпискам.
    
    Логика:
    1. Находим все подписки, у которых expires_at <= now (истекшие).
    2. Для каждой подписки вызываем _delete_subscription_internal:
       - удаляются все связанные ключи (v2ray и outline) из БД,
       - для V2Ray‑ключей выполняется удаление с серверов через API,
       - удаляется сама подписка.
    """
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    # Проверка CSRF токена
    if not validate_csrf_token(request, csrf_token):
        log_admin_action(request, "CSRF_ATTACK", "Invalid CSRF token for cleanup")
        return templates.TemplateResponse("cleanup.html", {
            "request": request,
            "error": "Invalid request. Please try again.",
            "csrf_token": get_csrf_token(request),
            "expired_count": 0,
            "deleted_subscriptions": 0,
            "deleted_from_v2ray": 0,
            "deleted_keys_from_db": 0,
            "errors": []
        })
    
    log_admin_action(request, "CLEANUP_STARTED", "Starting cleanup of expired subscriptions")
    
    try:
        subscription_repo = SubscriptionRepository(DB_PATH)
        now = int(time.time())
        # Синхронный метод возвращает все истекшие подписки (включая деактивированные)
        expired_subscriptions = subscription_repo.get_expired_subscriptions(now)
        
        expired_count = len(expired_subscriptions)
        deleted_subscriptions = 0
        deleted_from_v2ray = 0
        deleted_keys_from_db = 0
        errors: list[str] = []
        
        for sub_id, user_id, token in expired_subscriptions:
            try:
                result = await _delete_subscription_internal(request, sub_id)
                deleted_subscriptions += 1
                deleted_from_v2ray += result.get("deleted_v2ray_count", 0)
                deleted_keys_from_db += result.get("deleted_keys_count", 0)
            except Exception as e:
                logger.error(f"Failed to delete expired subscription {sub_id}: {e}", exc_info=True)
                errors.append(f"Failed to delete subscription {sub_id}: {str(e)}")
        
        log_admin_action(
            request,
            "CLEANUP_COMPLETED",
            f"Expired subscriptions: {expired_count}, "
            f"deleted subscriptions: {deleted_subscriptions}, "
            f"keys deleted from DB: {deleted_keys_from_db}, "
            f"V2Ray keys deleted from servers: {deleted_from_v2ray}, "
            f"errors: {len(errors)}"
        )
        
        return templates.TemplateResponse("cleanup.html", {
            "request": request,
            "csrf_token": get_csrf_token(request),
            "expired_count": expired_count,
            "deleted_subscriptions": deleted_subscriptions,
            "deleted_from_v2ray": deleted_from_v2ray,
            "deleted_keys_from_db": deleted_keys_from_db,
            "errors": errors
        })
    except Exception as e:
        logger.error(f"Cleanup failed: {e}", exc_info=True)
        log_admin_action(request, "CLEANUP_ERROR", f"Error: {str(e)}")
        return templates.TemplateResponse("cleanup.html", {
            "request": request,
            "csrf_token": get_csrf_token(request),
            "error": f"Cleanup failed: {str(e)}",
            "expired_count": 0,
            "deleted_subscriptions": 0,
            "deleted_from_v2ray": 0,
            "deleted_keys_from_db": 0,
            "errors": []
        })

