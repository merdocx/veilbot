"""
Маршруты для управления серверами
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
import sys
import os
import logging
from urllib.parse import urlparse, urlunparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.repositories.server_repository import ServerRepository
from app.settings import settings

from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token, validate_csrf_token
from ..dependencies.templates import templates
from .models import ServerForm

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH


def _normalize_v2ray_api_url(api_url: str) -> str:
    """Ensure V2Ray API URL contains /api suffix."""
    parsed = urlparse(api_url.strip())
    path = (parsed.path or "").rstrip('/')
    segments = [seg for seg in path.split('/') if seg]
    if not segments:
        path = "/api"
    elif "api" not in segments:
        path = f"{path}/api" if path else "/api"
    if not path.startswith("/"):
        path = f"/{path}"
    normalized = parsed._replace(path=path)
    return urlunparse(normalized).rstrip('/')


@router.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request):
    """Страница списка серверов"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    repo = ServerRepository(DATABASE_PATH)
    servers = repo.list_servers()
    server_ids = [s[0] for s in servers]
    outline_key_counts = repo.outline_key_counts(server_ids)
    v2ray_key_counts = repo.v2ray_key_counts(server_ids)
    servers_with_counts = []
    for s in servers:
        outline_count = outline_key_counts.get(s[0], 0)
        v2ray_count = v2ray_key_counts.get(s[0], 0)
        total_count = outline_count + v2ray_count
        servers_with_counts.append(s + (total_count,))
    
    # Подсчет активных серверов
    active_servers = sum(1 for s in servers if s[5])  # s[5] is the active field
    
    return templates.TemplateResponse("servers.html", {
        "request": request, 
        "servers": servers_with_counts,
        "active_servers": active_servers,
        "csrf_token": get_csrf_token(request)
    })


@router.post("/add_server")
async def add_server(
    request: Request,
    name: str = Form(...),
    api_url: str = Form(...),
    cert_sha256: str = Form(""),
    max_keys: int = Form(...),
    country: str = Form(""),
    protocol: str = Form("outline"),
    domain: str = Form(""),
    api_key: str = Form(""),
    v2ray_path: str = Form("/v2ray"),
    csrf_token: str = Form(...)
):
    """Добавление нового сервера"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    # Проверка CSRF токена
    if not validate_csrf_token(request, csrf_token):
        log_admin_action(request, "CSRF_ATTACK", f"Invalid CSRF token for add_server")
        return templates.TemplateResponse("servers.html", {
            "request": request, 
            "error": "Invalid request. Please try again.",
            "servers": [],
            "active_servers": 0,
            "csrf_token": get_csrf_token(request)
        })
    
    # Получаем значение чекбокса напрямую из формы
    # Чекбоксы отправляются только если отмечены, поэтому проверяем наличие поля
    form_data = await request.form()
    available_for_purchase = form_data.get("available_for_purchase") == "on"
    
    try:
        # Валидация входных данных
        server_data = ServerForm(
            name=name,
            api_url=api_url,
            cert_sha256=cert_sha256,
            max_keys=max_keys,
            protocol=protocol,
            domain=domain,
            api_key=api_key,
            v2ray_path=v2ray_path
        )
        
        api_url_to_store = server_data.api_url
        if server_data.protocol == 'v2ray':
            api_url_to_store = _normalize_v2ray_api_url(server_data.api_url)

        ServerRepository(DATABASE_PATH).add_server(
            name=server_data.name,
            api_url=api_url_to_store,
            cert_sha256=server_data.cert_sha256,
            max_keys=server_data.max_keys,
            country=country,
            protocol=server_data.protocol,
            domain=server_data.domain,
            api_key=server_data.api_key,
            v2ray_path=server_data.v2ray_path,
            available_for_purchase=1 if available_for_purchase else 0,
        )
        
        # Инвалидируем кэш меню бота
        try:
            from bot.keyboards import invalidate_menu_cache
            invalidate_menu_cache()
        except Exception as e:
            logging.warning(f"Failed to invalidate menu cache: {e}")
        
        log_admin_action(
            request,
            "ADD_SERVER",
            f"Name: {server_data.name}, URL: {api_url_to_store}, Protocol: {server_data.protocol}, Country: {country}"
        )
        
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except ValueError as e:
        log_admin_action(request, "ADD_SERVER_FAILED", f"Validation error: {str(e)}")
        repo = ServerRepository(DATABASE_PATH)
        servers = repo.list_servers()
        return templates.TemplateResponse("servers.html", {
            "request": request, 
            "error": f"Validation error: {str(e)}",
            "servers": servers,
            "active_servers": sum(1 for s in servers if s[5]),
            "csrf_token": get_csrf_token(request)
        })
    except Exception as e:
        log_admin_action(request, "ADD_SERVER_ERROR", f"Database error: {str(e)}")
        repo = ServerRepository(DATABASE_PATH)
        servers = repo.list_servers()
        return templates.TemplateResponse("servers.html", {
            "request": request, 
            "error": "Database error occurred",
            "servers": servers,
            "active_servers": sum(1 for s in servers if s[5]),
            "csrf_token": get_csrf_token(request)
        })


@router.get("/delete_server/{server_id}", response_class=HTMLResponse)
async def delete_server(request: Request, server_id: int):
    """Удаление сервера"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    try:
        repo = ServerRepository(DATABASE_PATH)
        server = repo.get_server(server_id)
        if server:
            log_admin_action(request, "DELETE_SERVER", f"ID: {server_id}, Name: {server[1]}")
        repo.delete_server(server_id)
        
        # Инвалидируем кэш меню бота
        try:
            from bot.keyboards import invalidate_menu_cache
            invalidate_menu_cache()
        except Exception as e:
            logging.warning(f"Failed to invalidate menu cache: {e}")
        
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except Exception as e:
        log_admin_action(request, "DELETE_SERVER_ERROR", f"ID: {server_id}, Error: {str(e)}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)


@router.get("/servers/edit/{server_id}")
async def edit_server_page(request: Request, server_id: int):
    """Страница редактирования сервера"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    server = ServerRepository(DATABASE_PATH).get_server(server_id)
    
    if not server:
        return RedirectResponse(url="/servers")
    
    return templates.TemplateResponse("edit_server.html", {
        "request": request, 
        "server": {
            "id": server[0],
            "name": server[1],
            "api_url": server[2],
            "cert_sha256": server[3] or "",
            "max_keys": server[4],
            "active": server[5],
            "country": server[6] or "",
            "protocol": server[7] or "outline",
            "domain": server[8] or "",
            "api_key": server[9] or "",
            "v2ray_path": server[10] if len(server) > 10 else "/v2ray",
            "available_for_purchase": server[11] if len(server) > 11 else 1,
        },
        "csrf_token": get_csrf_token(request)
    })


@router.post("/servers/edit/{server_id}")
async def edit_server(
    request: Request,
    server_id: int,
    name: str = Form(...),
    api_url: str = Form(...),
    cert_sha256: str = Form(""),
    max_keys: int = Form(...),
    country: str = Form(""),
    protocol: str = Form("outline"),
    domain: str = Form(""),
    api_key: str = Form(""),
    v2ray_path: str = Form("/v2ray"),
    active: bool = Form(False),
    csrf_token: str = Form(...)
):
    """Обновление сервера"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    # Проверка CSRF токена
    if not validate_csrf_token(request, csrf_token):
        log_admin_action(request, "CSRF_ATTACK", f"Invalid CSRF token for edit_server")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    
    # Получаем значение чекбокса напрямую из формы
    # Чекбоксы отправляются только если отмечены, поэтому проверяем наличие поля
    form_data = await request.form()
    available_for_purchase = form_data.get("available_for_purchase") == "on"
    
    try:
        # Валидация
        server_data = ServerForm(
            name=name,
            api_url=api_url,
            cert_sha256=cert_sha256,
            max_keys=max_keys,
            protocol=protocol,
            domain=domain,
            api_key=api_key,
            v2ray_path=v2ray_path
        )
        
        api_url_to_store = server_data.api_url
        if server_data.protocol == 'v2ray':
            api_url_to_store = _normalize_v2ray_api_url(server_data.api_url)

        ServerRepository(DATABASE_PATH).update_server(
            server_id=server_id,
            name=server_data.name,
            api_url=api_url_to_store,
            cert_sha256=server_data.cert_sha256,
            max_keys=server_data.max_keys,
            country=country,
            protocol=server_data.protocol,
            domain=server_data.domain,
            api_key=server_data.api_key,
            v2ray_path=server_data.v2ray_path,
            active=1 if active else 0,
            available_for_purchase=1 if available_for_purchase else 0,
        )
        
        # Инвалидируем кэш меню бота
        try:
            from bot.keyboards import invalidate_menu_cache
            invalidate_menu_cache()
        except Exception as e:
            logging.warning(f"Failed to invalidate menu cache: {e}")
        
        log_admin_action(request, "EDIT_SERVER", f"ID: {server_id}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except ValueError as e:
        log_admin_action(request, "EDIT_SERVER_FAILED", f"Validation error: {str(e)}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except Exception as e:
        log_admin_action(request, "EDIT_SERVER_ERROR", f"Database error: {str(e)}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
