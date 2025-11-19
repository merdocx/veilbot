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


def _prepare_servers_context(repo: ServerRepository) -> tuple[list[dict], int]:
    """Build a list of server dicts ready for template rendering."""
    raw_servers = repo.list_servers()
    if not raw_servers:
        return [], 0

    server_ids = [row[0] for row in raw_servers]
    outline_key_counts = repo.outline_key_counts(server_ids)
    v2ray_key_counts = repo.v2ray_key_counts(server_ids)

    servers_for_template: list[dict] = []
    for row in raw_servers:
        (
            server_id,
            name,
            api_url,
            cert_sha256,
            max_keys,
            active,
            country,
            protocol,
            domain,
            api_key,
            v2ray_path,
            available_for_purchase,
        ) = row

        protocol = (protocol or "outline").lower()
        total_keys = outline_key_counts.get(server_id, 0) + v2ray_key_counts.get(server_id, 0)
        parsed_api = urlparse(api_url or "")
        host_candidate = parsed_api.netloc or parsed_api.path
        display_host = (domain or "").strip() or (host_candidate or "")

        if protocol == "v2ray":
            protocol_badge_class = "protocol-badge--v2ray"
            protocol_icon = "security"
        elif protocol == "outline":
            protocol_badge_class = "protocol-badge--outline"
            protocol_icon = "lock"
        else:
            protocol_badge_class = "protocol-badge--neutral"
            protocol_icon = "device_hub"

        servers_for_template.append(
            {
                "id": server_id,
                "name": name,
                "api_url": api_url,
                "max_keys": max_keys,
                "active": bool(active),
                "country": country or "—",
                "protocol": protocol,
                "protocol_badge_class": protocol_badge_class,
                "protocol_icon": protocol_icon,
                "protocol_label": protocol.upper(),
                "keys_total": total_keys,
                "available_for_purchase": bool(available_for_purchase),
                "domain": domain or "",
                "display_host": display_host,
            }
        )

    active_servers = sum(1 for server in servers_for_template if server["active"])
    return servers_for_template, active_servers


@router.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request):
    """Страница списка серверов"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")

    repo = ServerRepository(DATABASE_PATH)
    servers_for_template, active_servers = _prepare_servers_context(repo)

    return templates.TemplateResponse(
        "servers.html",
        {
            "request": request,
            "servers": servers_for_template,
            "active_servers": active_servers,
            "csrf_token": get_csrf_token(request),
        },
    )


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

        server_id = ServerRepository(DATABASE_PATH).add_server(
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
        
        # Если это новый активный V2Ray сервер, создаем ключи для всех активных подписок
        if server_data.protocol == 'v2ray' and available_for_purchase:
            try:
                # Запускаем фоновую задачу через отдельный механизм
                # Используем threading для запуска задачи в фоне, чтобы не блокировать ответ
                import threading
                from bot.services.background_tasks import create_keys_for_new_server
                import asyncio
                
                def run_task():
                    """Запуск асинхронной задачи в отдельном потоке"""
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(create_keys_for_new_server(server_id))
                        loop.close()
                    except Exception as e:
                        logging.error(f"Error in background task for server {server_id}: {e}", exc_info=True)
                
                thread = threading.Thread(target=run_task, daemon=True)
                thread.start()
                logging.info(f"Started background task to create keys for subscriptions on new server {server_id}")
            except Exception as e:
                logging.error(f"Failed to start background task for new server {server_id}: {e}", exc_info=True)
        
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
        servers, active_servers = _prepare_servers_context(repo)
        return templates.TemplateResponse("servers.html", {
            "request": request, 
            "error": f"Validation error: {str(e)}",
            "servers": servers,
            "active_servers": active_servers,
            "csrf_token": get_csrf_token(request)
        })
    except Exception as e:
        log_admin_action(request, "ADD_SERVER_ERROR", f"Database error: {str(e)}")
        repo = ServerRepository(DATABASE_PATH)
        servers, active_servers = _prepare_servers_context(repo)
        return templates.TemplateResponse("servers.html", {
            "request": request, 
            "error": "Database error occurred",
            "servers": servers,
            "active_servers": active_servers,
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
            # Сохраняем информацию о протоколе перед удалением для инвалидации кэша
            server_protocol = server[7] if len(server) > 7 else None
        else:
            server_protocol = None
        
        repo.delete_server(server_id)
        
        # Инвалидируем кэш меню бота
        try:
            from bot.keyboards import invalidate_menu_cache
            invalidate_menu_cache()
        except Exception as e:
            logging.warning(f"Failed to invalidate menu cache: {e}")
        
        # Инвалидируем кэш подписок, если это был V2Ray сервер
        if server_protocol == 'v2ray':
            try:
                from bot.services.subscription_service import invalidate_subscriptions_cache_for_server
                invalidate_subscriptions_cache_for_server(server_id)
            except Exception as e:
                logging.warning(f"Failed to invalidate subscription cache for server {server_id}: {e}")
        
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
    
    # Получаем текущее состояние сервера для сравнения
    repo = ServerRepository(DATABASE_PATH)
    current_server = repo.get_server(server_id)
    current_active = current_server[5] if current_server and len(current_server) > 5 else 0
    current_available = current_server[11] if current_server and len(current_server) > 11 else 1
    current_protocol = current_server[7] if current_server and len(current_server) > 7 else None
    
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

        new_active = 1 if active else 0
        new_available = 1 if available_for_purchase else 0

        repo.update_server(
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
            active=new_active,
            available_for_purchase=new_available,
        )
        
        # Инвалидируем кэш меню бота
        try:
            from bot.keyboards import invalidate_menu_cache
            invalidate_menu_cache()
        except Exception as e:
            logging.warning(f"Failed to invalidate menu cache: {e}")
        
        # Инвалидируем кэш подписок, если это V2Ray сервер и изменились статусы
        if server_data.protocol == 'v2ray' or current_protocol == 'v2ray':
            if (current_active != new_active or current_available != new_available):
                try:
                    from bot.services.subscription_service import invalidate_subscriptions_cache_for_server
                    invalidate_subscriptions_cache_for_server(server_id)
                except Exception as e:
                    logging.warning(f"Failed to invalidate subscription cache for server {server_id}: {e}")
        
        log_admin_action(request, "EDIT_SERVER", f"ID: {server_id}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except ValueError as e:
        log_admin_action(request, "EDIT_SERVER_FAILED", f"Validation error: {str(e)}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except Exception as e:
        log_admin_action(request, "EDIT_SERVER_ERROR", f"Database error: {str(e)}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
