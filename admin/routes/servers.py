"""
Маршруты для управления серверами
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
import sys
import os
import logging
import asyncio
from urllib.parse import urlparse, urlunparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.repositories.server_repository import ServerRepository
from app.settings import settings
from app.infra.sqlite_utils import open_connection
from app.infra.foreign_keys import safe_foreign_keys_off
from vpn_protocols import V2RayProtocol
from outline import delete_key as outline_delete_key

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


def _prepare_servers_context(repo: ServerRepository, search_query: str | None = None) -> tuple[list[dict], int]:
    """Build a list of server dicts ready for template rendering."""
    raw_servers = repo.list_servers(search_query=search_query)
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
            access_level,
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
                "access_level": access_level or 'all',
                "domain": domain or "",
                "display_host": display_host,
            }
        )

    active_servers = sum(1 for server in servers_for_template if server["active"])
    return servers_for_template, active_servers


@router.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request, q: str | None = None):
    """Страница списка серверов"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")

    repo = ServerRepository(DATABASE_PATH)
    search_query = q.strip() if q and q.strip() else None
    servers_for_template, active_servers = _prepare_servers_context(repo, search_query=search_query)

    return templates.TemplateResponse(
        "servers.html",
        {
            "request": request,
            "servers": servers_for_template,
            "active_servers": active_servers,
            "search_query": search_query or '',
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
    
    # Получаем значение access_level из формы
    form_data = await request.form()
    access_level = form_data.get("access_level", "all")
    if access_level not in ['all', 'paid', 'vip']:
        access_level = 'all'
    
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
            access_level=access_level,
        )
        
        # Если это новый активный V2Ray сервер, создаем ключи для всех активных подписок
        # Проверка access_level будет выполняться внутри create_keys_for_new_server для каждого пользователя
        if server_data.protocol == 'v2ray':
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
            api_url = server[2] if len(server) > 2 else ""
            api_key = server[9] if len(server) > 9 else ""
            cert_sha256 = server[3] if len(server) > 3 else ""
        else:
            server_protocol = None
            api_url = ""
            api_key = ""
            cert_sha256 = ""
        
        # Удаляем все ключи с сервера перед удалением из БД
        if server and server_protocol:
            logging.info(f"Server {server_id} is being deleted. Deleting all keys from server and database.")
            await _delete_all_keys_from_server(server_id, server_protocol, api_url, api_key, cert_sha256)
        
        deletion_stats = repo.delete_server(server_id)
        log_admin_action(
            request,
            "DELETE_SERVER_CLEANUP",
            (
                f"ID: {server_id}, outline_keys_deleted={deletion_stats.get('outline_keys_deleted', 0)}, "
                f"v2ray_keys_deleted={deletion_stats.get('v2ray_keys_deleted', 0)}, "
                f"subscriptions_affected={deletion_stats.get('subscriptions_affected', 0)}"
            ),
        )
        
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
    
    error_message = request.session.pop("edit_server_error", None)
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
            "access_level": server[11] if len(server) > 11 else 'all',
        },
        "csrf_token": get_csrf_token(request),
        "error_message": error_message,
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
    
    # Получаем значение access_level из формы
    form_data = await request.form()
    access_level = form_data.get("access_level", "all")
    if access_level not in ['all', 'paid', 'vip']:
        access_level = 'all'
    
    # Получаем текущее состояние сервера для сравнения
    repo = ServerRepository(DATABASE_PATH)
    current_server = repo.get_server(server_id)
    current_active = current_server[5] if current_server and len(current_server) > 5 else 0
    current_access_level = current_server[11] if current_server and len(current_server) > 11 else 'all'
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

        # Если сервер деактивируется (active: 1 -> 0), удаляем все ключи с сервера и из БД
        # ВАЖНО: Выполняем удаление в фоне, чтобы не блокировать ответ пользователю
        if current_active == 1 and new_active == 0:
            logging.info(f"Server {server_id} is being deactivated. Scheduling key deletion in background.")
            # Используем текущие данные сервера из БД для удаления ключей
            current_api_url = current_server[2] if current_server and len(current_server) > 2 else ""
            current_api_key = current_server[9] if current_server and len(current_server) > 9 else ""
            current_cert_sha256 = current_server[3] if current_server and len(current_server) > 3 else ""
            # Запускаем удаление в фоне, чтобы не блокировать ответ
            asyncio.create_task(
                _delete_all_keys_from_server(server_id, current_protocol, current_api_url, current_api_key, current_cert_sha256)
            )

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
            access_level=access_level,
        )
        
        # Инвалидируем кэш меню бота
        try:
            from bot.keyboards import invalidate_menu_cache
            invalidate_menu_cache()
        except Exception as e:
            logging.warning(f"Failed to invalidate menu cache: {e}")
        
        # Инвалидируем кэш подписок, если это V2Ray сервер и изменились статусы
        if server_data.protocol == 'v2ray' or current_protocol == 'v2ray':
            if (current_active != new_active or current_access_level != access_level):
                try:
                    from bot.services.subscription_service import invalidate_subscriptions_cache_for_server
                    invalidate_subscriptions_cache_for_server(server_id)
                except Exception as e:
                    logging.warning(f"Failed to invalidate subscription cache for server {server_id}: {e}")
        
        log_admin_action(request, "EDIT_SERVER", f"ID: {server_id}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except ValueError as e:
        log_admin_action(request, "EDIT_SERVER_FAILED", f"Validation error: {str(e)}")
        request.session["edit_server_error"] = str(e)
        return RedirectResponse(url=f"/servers/edit/{server_id}", status_code=HTTP_303_SEE_OTHER)
    except Exception as e:
        log_admin_action(request, "EDIT_SERVER_ERROR", f"Database error: {str(e)}")
        request.session["edit_server_error"] = str(e)
        return RedirectResponse(url=f"/servers/edit/{server_id}", status_code=HTTP_303_SEE_OTHER)


async def _delete_all_keys_from_server(
    server_id: int,
    protocol: str,
    api_url: str,
    api_key: str,
    cert_sha256: str
) -> None:
    """
    Удалить все ключи с сервера и из БД при деактивации сервера.
    
    Args:
        server_id: ID сервера
        protocol: Протокол сервера ('v2ray' или 'outline')
        api_url: URL API сервера
        api_key: API ключ (для V2Ray)
        cert_sha256: SHA256 сертификата (для Outline)
    """
    logging.info(f"Deleting all keys from server {server_id} (protocol: {protocol})")
    
    deleted_from_server = 0
    deleted_from_db = 0
    errors = []
    
    try:
        # Получаем все ключи с этого сервера из БД
        with open_connection(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            
            if protocol == 'v2ray':
                # Получаем V2Ray ключи
                cursor.execute("""
                    SELECT id, v2ray_uuid, user_id
                    FROM v2ray_keys
                    WHERE server_id = ?
                """, (server_id,))
                v2ray_keys = cursor.fetchall()
                
                # Удаляем V2Ray ключи с сервера
                # ВАЖНО: Добавляем таймаут для каждой операции удаления, чтобы не зависать
                if v2ray_keys and api_url and api_key:
                    protocol_client = None
                    try:
                        protocol_client = V2RayProtocol(api_url, api_key)
                        for key_id, v2ray_uuid, user_id in v2ray_keys:
                            if v2ray_uuid:
                                try:
                                    # Добавляем таймаут 5 секунд на каждое удаление
                                    result = await asyncio.wait_for(
                                        protocol_client.delete_user(v2ray_uuid),
                                        timeout=5.0
                                    )
                                    if result:
                                        deleted_from_server += 1
                                        logging.info(f"Deleted V2Ray key {v2ray_uuid} from server {server_id}")
                                    else:
                                        errors.append(f"Failed to delete V2Ray key {v2ray_uuid} from server")
                                except asyncio.TimeoutError:
                                    error_msg = f"Timeout deleting V2Ray key {v2ray_uuid} from server {server_id}"
                                    logging.warning(error_msg)
                                    errors.append(error_msg)
                                except Exception as e:
                                    error_msg = f"Error deleting V2Ray key {v2ray_uuid}: {e}"
                                    logging.error(error_msg, exc_info=True)
                                    errors.append(error_msg)
                    finally:
                        if protocol_client:
                            try:
                                await protocol_client.close()
                            except Exception:
                                pass
                
                # Удаляем V2Ray ключи из БД
                with safe_foreign_keys_off(cursor):
                    cursor.execute("DELETE FROM v2ray_keys WHERE server_id = ?", (server_id,))
                    deleted_from_db += cursor.rowcount
                    conn.commit()
                    logging.info(f"Deleted {cursor.rowcount} V2Ray keys from database for server {server_id}")
            
            elif protocol == 'outline':
                # Получаем Outline ключи
                cursor.execute("""
                    SELECT id, key_id, user_id
                    FROM keys
                    WHERE server_id = ? AND protocol = 'outline'
                """, (server_id,))
                outline_keys = cursor.fetchall()
                
                # Удаляем Outline ключи с сервера
                # ВАЖНО: Выполняем в отдельном потоке с таймаутом, чтобы не блокировать
                if outline_keys and api_url and cert_sha256:
                    for key_id, outline_key_id, user_id in outline_keys:
                        if outline_key_id:
                            try:
                                # Выполняем синхронную операцию в отдельном потоке с таймаутом
                                loop = asyncio.get_event_loop()
                                result = await asyncio.wait_for(
                                    loop.run_in_executor(
                                        None,
                                        outline_delete_key,
                                        api_url,
                                        cert_sha256,
                                        outline_key_id
                                    ),
                                    timeout=5.0
                                )
                                if result:
                                    deleted_from_server += 1
                                    logging.info(f"Deleted Outline key {outline_key_id} from server {server_id}")
                                else:
                                    errors.append(f"Failed to delete Outline key {outline_key_id} from server")
                            except asyncio.TimeoutError:
                                error_msg = f"Timeout deleting Outline key {outline_key_id} from server {server_id}"
                                logging.warning(error_msg)
                                errors.append(error_msg)
                            except Exception as e:
                                error_msg = f"Error deleting Outline key {outline_key_id}: {e}"
                                logging.error(error_msg, exc_info=True)
                                errors.append(error_msg)
                
                # Удаляем Outline ключи из БД
                with safe_foreign_keys_off(cursor):
                    cursor.execute("DELETE FROM keys WHERE server_id = ? AND protocol = 'outline'", (server_id,))
                    deleted_from_db += cursor.rowcount
                    conn.commit()
                    logging.info(f"Deleted {cursor.rowcount} Outline keys from database for server {server_id}")
            
            else:
                logging.warning(f"Unknown protocol {protocol} for server {server_id}")
        
        logging.info(
            f"Server {server_id} deactivation: deleted {deleted_from_server} keys from server, "
            f"{deleted_from_db} keys from database. Errors: {len(errors)}"
        )
        
        if errors:
            logging.warning(f"Errors during key deletion for server {server_id}: {errors}")
    
    except Exception as e:
        logging.error(f"Error deleting keys from server {server_id}: {e}", exc_info=True)
        # Продолжаем выполнение даже при ошибках - ключи все равно нужно удалить из БД
