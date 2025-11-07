"""
Маршруты для очистки данных
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.settings import settings
from outline import delete_key
from app.repositories.key_repository import KeyRepository
from app.infra.sqlite_utils import open_connection

from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token, validate_csrf_token
from ..dependencies.templates import templates

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH
DB_PATH = DATABASE_PATH


@router.get("/cleanup")
async def cleanup_page(request: Request):
    """Страница очистки старых данных"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    return templates.TemplateResponse("cleanup.html", {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "expired_count": 0,
        "deleted_from_outline": 0,
        "deleted_from_v2ray": 0,
        "deleted_count": 0,
        "errors": []
    })


@router.post("/cleanup")
async def cleanup(request: Request, csrf_token: str = Form(...)):
    """Выполнение очистки старых данных"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    # Проверка CSRF токена
    if not validate_csrf_token(request, csrf_token):
        log_admin_action(request, "CSRF_ATTACK", f"Invalid CSRF token for cleanup")
        return templates.TemplateResponse("cleanup.html", {
            "request": request, 
            "error": "Invalid request. Please try again.",
            "csrf_token": get_csrf_token(request),
            "expired_count": 0,
            "deleted_from_outline": 0,
            "deleted_from_v2ray": 0,
            "deleted_count": 0,
            "errors": []
        })
    
    log_admin_action(request, "CLEANUP_STARTED")
    
    try:
        key_repo = KeyRepository(DB_PATH)
        now = int(time.time())
        expired_outline_keys = key_repo.get_expired_outline_keys(now)
        expired_v2ray_keys = key_repo.get_expired_v2ray_keys(now)
        
        deleted_count = 0
        deleted_from_outline = 0
        deleted_from_v2ray = 0
        errors = []
        
        # Обработка истекших Outline ключей
        for key_id, outline_key_id, server_id in expired_outline_keys:
            try:
                if outline_key_id and server_id:
                    with open_connection(DB_PATH) as conn:
                        c = conn.cursor()
                        c.execute("SELECT api_url, cert_sha256 FROM servers WHERE id = ?", (server_id,))
                        server = c.fetchone()
                        if server:
                            try:
                                delete_key(server[0], server[1], outline_key_id)
                                deleted_from_outline += 1
                            except Exception as e:
                                errors.append(f"Failed to delete key {outline_key_id} from Outline: {str(e)}")
                key_repo.delete_outline_key_by_id(key_id)
                deleted_count += 1
            except Exception as e:
                errors.append(f"Failed to delete Outline key {key_id}: {str(e)}")
        
        # Обработка истекших V2Ray ключей
        for key_id, v2ray_uuid, server_id in expired_v2ray_keys:
            try:
                if v2ray_uuid and server_id:
                    with open_connection(DB_PATH) as conn:
                        c = conn.cursor()
                        c.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (server_id,))
                        server = c.fetchone()
                        if server:
                            try:
                                from vpn_protocols import V2RayProtocol
                                protocol_client = V2RayProtocol(server[0], server[1])
                                await protocol_client.delete_user(v2ray_uuid)
                                deleted_from_v2ray += 1
                            except Exception as e:
                                errors.append(f"Failed to delete V2Ray user {v2ray_uuid}: {str(e)}")
                key_repo.delete_v2ray_key_by_id(key_id)
                deleted_count += 1
            except Exception as e:
                errors.append(f"Failed to delete V2Ray key {key_id}: {str(e)}")
        
        log_admin_action(
            request,
            "CLEANUP_COMPLETED",
            f"Deleted: {deleted_count}, Outline deleted: {deleted_from_outline}, V2Ray deleted: {deleted_from_v2ray}, Errors: {len(errors)}"
        )
        
        return templates.TemplateResponse("cleanup.html", {
            "request": request,
            "csrf_token": get_csrf_token(request),
            "expired_count": len(expired_outline_keys) + len(expired_v2ray_keys),
            "deleted_from_outline": deleted_from_outline,
            "deleted_from_v2ray": deleted_from_v2ray,
            "deleted_count": deleted_count,
            "errors": errors
        })
    except Exception as e:
        log_admin_action(request, "CLEANUP_ERROR", f"Error: {str(e)}")
        return templates.TemplateResponse("cleanup.html", {
            "request": request,
            "csrf_token": get_csrf_token(request),
            "error": f"Cleanup failed: {str(e)}",
            "expired_count": 0,
            "deleted_from_outline": 0,
            "deleted_from_v2ray": 0,
            "deleted_count": 0,
            "errors": []
        })

