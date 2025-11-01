"""
Маршруты для управления ключами VPN
"""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse, Response
import sys
import os
import sqlite3
import time
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from io import StringIO
import csv

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.repositories.key_repository import KeyRepository
from vpn_protocols import ProtocolFactory
from outline import delete_key
from vpn_protocols import V2RayProtocol
import aiohttp
from app.settings import settings

from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token
from ..dependencies.templates import templates

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH
DB_PATH = DATABASE_PATH


async def get_key_monthly_traffic(key_uuid: str, protocol: str, server_config: dict, server_id: int = None) -> str:
    """Get monthly traffic for a specific key in GB"""
    v2ray = None
    try:
        if protocol == 'v2ray':
            # Try to get from cache first
            if server_id:
                from app.infra.cache import get_cached_v2ray_traffic, cache_v2ray_traffic
                cached_data = get_cached_v2ray_traffic(server_id, server_config)
                if cached_data:
                    # Find specific key in cached data
                    data = cached_data.get('data', {})
                    keys = data.get('keys', [])
                    logging.debug(f"Searching for uuid {key_uuid} in {len(keys)} cached keys")
                    for key in keys:
                        # Проверяем оба варианта UUID
                        cached_uuid = key.get('key_uuid') or key.get('uuid')
                        if cached_uuid == key_uuid:
                            monthly_traffic_data = key.get('monthly_traffic', {})
                            total_bytes = monthly_traffic_data.get('total_bytes', 0) if monthly_traffic_data else 0
                            if total_bytes == 0:
                                return "0 GB"
                            traffic_gb = total_bytes / (1024 * 1024 * 1024)
                            logging.debug(f"Found cached traffic for {key_uuid}: {traffic_gb:.2f} GB")
                            return f"{traffic_gb:.2f} GB"
                    logging.debug(f"Key {key_uuid} not found in cache")
                    return "0 GB"
            
            # Create V2Ray protocol instance
            v2ray = ProtocolFactory.create_protocol('v2ray', server_config)
            
            # Get monthly traffic for all keys and find specific key
            monthly_traffic = await v2ray.get_monthly_traffic()
            
            # Cache the result if we have server_id
            if server_id and monthly_traffic:
                from app.infra.cache import cache_v2ray_traffic
                cache_v2ray_traffic(server_id, server_config, monthly_traffic, ttl=300)
            
            if monthly_traffic and monthly_traffic.get('data'):
                data = monthly_traffic.get('data', {})
                keys = data.get('keys', [])
                logging.debug(f"Searching for uuid {key_uuid} in {len(keys)} keys from API")
                
                # Find the specific key by UUID (проверяем оба варианта)
                for key in keys:
                    key_uuid_from_api = key.get('key_uuid') or key.get('uuid')
                    if key_uuid_from_api == key_uuid:
                        monthly_traffic_data = key.get('monthly_traffic', {})
                        # Если monthly_traffic_data это dict, берем total_bytes из него
                        if isinstance(monthly_traffic_data, dict):
                            total_bytes = monthly_traffic_data.get('total_bytes', 0)
                        else:
                            # Если нет monthly_traffic, возможно total_bytes в корне ключа
                            total_bytes = key.get('total_bytes', 0)
                        
                        logging.debug(f"Found key in API: uuid={key_uuid_from_api}, total_bytes={total_bytes}")
                        
                        # Convert to GB and format
                        if total_bytes == 0:
                            return "0 GB"
                        
                        traffic_gb = total_bytes / (1024 * 1024 * 1024)
                        return f"{traffic_gb:.2f} GB"
                
                logging.warning(f"Key {key_uuid} not found in monthly traffic data. Available UUIDs: {[k.get('key_uuid') or k.get('uuid') for k in keys[:5]]}")
                
                # Key not found in monthly data, fallback to total traffic
                traffic_history = await v2ray.get_traffic_history()
                
                if traffic_history and traffic_history.get('data'):
                    data = traffic_history.get('data', {})
                    keys = data.get('keys', [])
                    
                    # Find the specific key by UUID (проверяем оба варианта)
                    for key in keys:
                        key_uuid_from_api = key.get('key_uuid') or key.get('uuid')
                        if key_uuid_from_api == key_uuid:
                            total_traffic = key.get('total_traffic', {})
                            if isinstance(total_traffic, dict):
                                total_bytes = total_traffic.get('total_bytes', 0)
                            else:
                                # Если total_traffic не dict, берем total_bytes из корня ключа
                                total_bytes = key.get('total_bytes', 0)
                            
                            if total_bytes == 0:
                                return "0 GB"
                            
                            traffic_gb = total_bytes / (1024 * 1024 * 1024)
                            return f"{traffic_gb:.2f} GB"
                
                # Key not found
                return "0 GB"
            else:
                logging.warning(f"No monthly traffic data available. Response: {monthly_traffic}")
                # Fallback to total traffic if monthly data not available
                traffic_history = await v2ray.get_traffic_history()
                
                if traffic_history and traffic_history.get('data'):
                    data = traffic_history.get('data', {})
                    keys = data.get('keys', [])
                    
                    # Find the specific key by UUID (проверяем оба варианта)
                    for key in keys:
                        key_uuid_from_api = key.get('key_uuid') or key.get('uuid')
                        if key_uuid_from_api == key_uuid:
                            total_traffic = key.get('total_traffic', {})
                            if isinstance(total_traffic, dict):
                                total_bytes = total_traffic.get('total_bytes', 0)
                            else:
                                # Если total_traffic не dict, берем total_bytes из корня ключа
                                total_bytes = key.get('total_bytes', 0)
                            
                            if total_bytes == 0:
                                return "0 GB"
                            
                            traffic_gb = total_bytes / (1024 * 1024 * 1024)
                            return f"{traffic_gb:.2f} GB"
                
                # Key not found
                return "0 GB"
        else:
            # For Outline, we don't have historical data yet
            return "N/A"
    except Exception as e:
        logging.error(f"Error getting monthly traffic for key {key_uuid}: {e}", exc_info=True)
        return "Error"
    finally:
        # Закрываем сессию V2Ray
        if v2ray:
            await v2ray.close()


@router.get("/keys")
async def keys_page(
    request: Request,
    page: int = 1,
    limit: int = 50,
    email: str | None = None,
    tariff_id: int | None = None,
    protocol: str | None = None,
    server_id: int | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
    export: str | None = None,
    cursor: str | None = None
):
    """Страница списка ключей"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")

    # Debug logging
    log_admin_action(request, "KEYS_PAGE_ACCESS", f"DB_PATH: {DB_PATH}")

    key_repo = KeyRepository(DB_PATH)
    total = key_repo.count_keys_unified(
        email=email,
        tariff_id=tariff_id,
        protocol=protocol,
        server_id=server_id
    )
    
    # Сортировка по дате создания по умолчанию
    sort_by_eff = 'created_at'
    sort_order_eff = 'DESC'
    
    # Используем cursor-based pagination для лучшей производительности на больших страницах
    use_cursor = cursor is not None or (page > 1 and total > 1000)
    decoded_cursor = None
    if use_cursor:
        from app.infra.pagination import KeysetPagination
        if cursor:
            decoded_cursor = KeysetPagination.decode_cursor(cursor)
            if not decoded_cursor:
                use_cursor = False
    
    rows = key_repo.list_keys_unified(
        email=email,
        tariff_id=tariff_id,
        protocol=protocol,
        server_id=server_id,
        sort_by=sort_by_eff,
        sort_order=sort_order_eff,
        limit=limit,
        offset=(page-1)*limit if not use_cursor else 0,
        cursor=cursor if use_cursor else None,
    )
    
    # Получаем данные о трафике и реальную конфигурацию для V2Ray ключей
    # Оптимизация: параллельная загрузка данных для всех V2Ray ключей
    keys_with_traffic = []
    
    # Сначала получаем server_id для всех V2Ray ключей одним запросом
    v2ray_key_ids = [key[0] for key in rows if len(key) > 8 and key[8] == 'v2ray']
    server_id_map = {}
    if v2ray_key_ids:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            placeholders = ','.join('?' * len(v2ray_key_ids))
            c.execute(f"SELECT id, server_id FROM v2ray_keys WHERE id IN ({placeholders})", v2ray_key_ids)
            for key_id, srv_id in c.fetchall():
                server_id_map[key_id] = srv_id
    
    # Асинхронная функция для получения конфигурации V2Ray
    async def fetch_v2ray_config(key_data):
        """Fetch V2Ray config for a single key"""
        key_id = key_data[0]
        v2ray_uuid = key_data[1]
        api_url = key_data[10] if len(key_data) > 10 else ''
        api_key = key_data[11] if len(key_data) > 11 else ''
        
        if not (api_url and api_key and v2ray_uuid):
            return key_id, key_data[2], "N/A"
        
        try:
            headers = {
                "X-API-Key": api_key,
                "Content-Type": "application/json"
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{api_url}/keys/{v2ray_uuid}/config",
                    headers=headers,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        config = result.get('client_config') or result.get('config', '')
                        return key_id, config.strip() if config else key_data[2], None
                    return key_id, key_data[2], None
        except Exception as e:
            logging.error(f"Error getting real V2Ray config for {v2ray_uuid}: {e}")
            return key_id, key_data[2], None
    
    # Создаем задачи для параллельной загрузки конфигураций
    v2ray_tasks = []
    v2ray_keys_data = {}
    
    for key in rows:
        if len(key) > 8 and key[8] == 'v2ray':
            v2ray_keys_data[key[0]] = key
            v2ray_tasks.append(fetch_v2ray_config(key))
        else:
            # Outline keys - сразу добавляем
            keys_with_traffic.append(list(key) + ["N/A"])
    
    # Параллельно загружаем все конфигурации
    if v2ray_tasks:
        config_results = await asyncio.gather(*v2ray_tasks, return_exceptions=True)
        
        # Обрабатываем результаты конфигураций и группируем по серверам для батчинга трафика
        server_keys_map = defaultdict(list)
        processed_configs = {}
        
        for result in config_results:
            if isinstance(result, Exception):
                logging.error(f"Error in parallel V2Ray fetch: {result}")
                continue
            
            key_id, real_config, _ = result
            key = v2ray_keys_data[key_id]
            key_list = list(key)
            key_list[2] = real_config  # Update access_url
            processed_configs[key_id] = (key_list, real_config)
            
            # Группируем по серверам для батчинга трафика
            srv_id = server_id_map.get(key_id)
            if srv_id:
                api_url = key[10] if len(key) > 10 else ''
                api_key = key[11] if len(key) > 11 else ''
                server_keys_map[srv_id].append({
                    'key_id': key_id,
                    'uuid': key[1],
                    'api_url': api_url,
                    'api_key': api_key
                })
        
        # Функция для батчинга трафика по серверу
        async def fetch_server_traffic_batch(server_id: int, server_keys_list: list):
            """Загрузить трафик для всех ключей одного сервера одним запросом"""
            if not server_keys_list:
                return {}
            
            api_url = server_keys_list[0]['api_url']
            api_key = server_keys_list[0]['api_key']
            
            if not api_url or not api_key:
                return {k['key_id']: "N/A" for k in server_keys_list}
            
            server_config = {'api_url': api_url, 'api_key': api_key}
            traffic_map = {}
            
            try:
                from app.infra.cache import get_cached_v2ray_traffic, cache_v2ray_traffic
                cached_data = get_cached_v2ray_traffic(server_id, server_config)
                
                if cached_data:
                    data = cached_data.get('data', {})
                    keys = data.get('keys', [])
                    uuid_to_traffic = {}
                    for key_data in keys:
                        uuid = key_data.get('key_uuid')
                        if uuid:
                            monthly_traffic_data = key_data.get('monthly_traffic', {})
                            total_bytes = monthly_traffic_data.get('total_bytes', 0)
                            if total_bytes > 0:
                                traffic_gb = total_bytes / (1024 * 1024 * 1024)
                                uuid_to_traffic[uuid] = f"{traffic_gb:.2f} GB"
                            else:
                                uuid_to_traffic[uuid] = "0 GB"
                    
                    for key_info in server_keys_list:
                        uuid = key_info['uuid']
                        traffic_map[key_info['key_id']] = uuid_to_traffic.get(uuid, "0 GB")
                else:
                    v2ray = ProtocolFactory.create_protocol('v2ray', server_config)
                    try:
                        monthly_traffic = await v2ray.get_monthly_traffic()
                        
                        if monthly_traffic:
                            cache_v2ray_traffic(server_id, server_config, monthly_traffic, ttl=300)
                        
                        if monthly_traffic and monthly_traffic.get('data'):
                            data = monthly_traffic.get('data', {})
                            keys = data.get('keys', [])
                            uuid_to_traffic = {}
                            
                            for key_data in keys:
                                uuid = key_data.get('key_uuid')
                                if uuid:
                                    monthly_traffic_data = key_data.get('monthly_traffic', {})
                                    total_bytes = monthly_traffic_data.get('total_bytes', 0)
                                    if total_bytes > 0:
                                        traffic_gb = total_bytes / (1024 * 1024 * 1024)
                                        uuid_to_traffic[uuid] = f"{traffic_gb:.2f} GB"
                                    else:
                                        uuid_to_traffic[uuid] = "0 GB"
                            
                            for key_info in server_keys_list:
                                uuid = key_info['uuid']
                                traffic_map[key_info['key_id']] = uuid_to_traffic.get(uuid, "0 GB")
                        else:
                            for key_info in server_keys_list:
                                traffic_map[key_info['key_id']] = "0 GB"
                    finally:
                        await v2ray.close()
                
            except Exception as e:
                logging.error(f"Error fetching traffic batch for server {server_id}: {e}")
                for key_info in server_keys_list:
                    traffic_map[key_info['key_id']] = "Error"
            
            return traffic_map
        
        # Параллельно загружаем трафик для всех серверов
        if server_keys_map:
            traffic_tasks = [
                fetch_server_traffic_batch(srv_id, keys)
                for srv_id, keys in server_keys_map.items()
            ]
            traffic_results = await asyncio.gather(*traffic_tasks, return_exceptions=True)
            
            all_traffic_map = {}
            for result in traffic_results:
                if isinstance(result, Exception):
                    logging.error(f"Error in traffic batch fetch: {result}")
                    continue
                if isinstance(result, dict):
                    all_traffic_map.update(result)
            
            # Добавляем ключи БЕЗ трафика (будет загружен по требованию через AJAX)
            for key_id, (key_list, real_config) in processed_configs.items():
                if len(key_list) > 8 and key_list[8] == 'v2ray':
                    keys_with_traffic.append(key_list + ["load"])  # "load" = маркер для ленивой загрузки
                else:
                    keys_with_traffic.append(key_list + ["N/A"])
        else:
            for key_id, (key_list, real_config) in processed_configs.items():
                if len(key_list) > 8 and key_list[8] == 'v2ray':
                    keys_with_traffic.append(key_list + ["load"])
                else:
                    keys_with_traffic.append(key_list + ["N/A"])
    
    log_admin_action(request, "KEYS_QUERY_RESULT", f"Total keys: {len(keys_with_traffic)}")

    # CSV Export
    if export and str(export).lower() in ("csv", "true", "1"):
        try:
            now_ts = int(time.time())
            buffer = StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["id", "protocol", "key", "tariff", "email", "server", "created_at", "expiry_at", "status", "traffic"])
            for k in keys_with_traffic:
                status = "active" if (k[4] and int(k[4]) > now_ts) else "expired"
                writer.writerow([
                    k[0], k[8], k[2], k[7] or '', k[6] or '', k[5] or '',
                    int(k[3] or 0), int(k[4] or 0), status, (k[-1] if len(k) else '')
                ])
            content = buffer.getvalue()
            return Response(content, media_type="text/csv", headers={
                "Content-Disposition": "attachment; filename=keys_export.csv"
            })
        except Exception as e:
            logging.error(f"CSV export error: {e}")

    # Calculate additional stats
    active_count = sum(1 for k in keys_with_traffic if k[4] and int(k[4]) > int(time.time()))
    expired_count = total - active_count
    v2ray_count = sum(1 for k in keys_with_traffic if len(k) > 8 and k[8] == 'v2ray')
    pages = (total + limit - 1) // limit
    
    # Генерируем cursor для следующей страницы
    next_cursor = None
    if len(rows) > limit:
        from app.infra.pagination import KeysetPagination
        last_row = rows[-1]
        if len(last_row) >= 4:
            created_at = last_row[3] or 0
            key_id = last_row[0]
            next_cursor = KeysetPagination.encode_cursor(created_at, key_id)
        keys_with_traffic = keys_with_traffic[:limit]
    
    return templates.TemplateResponse("keys.html", {
        "request": request, 
        "keys": keys_with_traffic,
        "current_time": int(time.time()),
        "page": page,
        "limit": limit,
        "total": total,
        "active_count": active_count,
        "expired_count": expired_count,
        "v2ray_count": v2ray_count,
        "pages": pages,
        "next_cursor": next_cursor,
        "email": email or '',
        "user_id": '',
        "server": '',
        "protocol": protocol or '',
        "filters": {"email": email or '', "tariff_id": tariff_id or '', "protocol": protocol or '', "server_id": server_id or ''},
        "sort": {"by": sort_by_eff, "order": sort_order_eff},
        "csrf_token": get_csrf_token(request),
    })


@router.get("/api/keys/{key_id}/traffic")
async def get_key_traffic_api(request: Request, key_id: int):
    """API endpoint для ленивой загрузки трафика ключа"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        key_repo = KeyRepository(DB_PATH)
        
        # Получаем информацию о ключе
        outline_key = key_repo.get_outline_key_brief(key_id)
        if outline_key:
            return JSONResponse({"traffic": "N/A", "protocol": "outline"})
        
        v2ray_key = key_repo.get_v2ray_key_brief(key_id)
        if not v2ray_key:
            return JSONResponse({"error": "Key not found"}, status_code=404)
        
        user_id, v2ray_uuid, server_id = v2ray_key
        
        # Получаем конфигурацию сервера
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (server_id,))
            server = c.fetchone()
            if not server:
                return JSONResponse({"error": "Server not found"}, status_code=404)
        
        api_url = server[0] or ''
        api_key = server[1] or ''
        server_config = {'api_url': api_url, 'api_key': api_key}
        
        # Загружаем трафик с логированием для отладки
        logging.debug(f"Fetching traffic for key_id={key_id}, uuid={v2ray_uuid}, server_id={server_id}")
        traffic = await get_key_monthly_traffic(v2ray_uuid, 'v2ray', server_config, server_id)
        logging.debug(f"Traffic result for key_id={key_id}: {traffic}")
        
        return JSONResponse({
            "traffic": traffic,
            "protocol": "v2ray",
            "key_id": key_id,
            "uuid": v2ray_uuid  # Для отладки
        })
    except Exception as e:
        logging.error(f"Error getting traffic for key {key_id}: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/keys/edit/{key_id}")
async def edit_key_route(request: Request, key_id: int):
    """Редактирование срока действия ключа"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    form = await request.form()
    new_expiry_str = form.get("new_expiry")
    
    if not new_expiry_str:
        return JSONResponse({"error": "new_expiry is required"}, status_code=400)
    
    try:
        # Парсим datetime-local format (YYYY-MM-DDTHH:mm) в timestamp
        dt = datetime.strptime(new_expiry_str, "%Y-%m-%dT%H:%M")
        new_expiry = int(dt.timestamp())
        
        key_repo = KeyRepository(DB_PATH)
        
        # Пробуем Outline сначала
        outline_key = key_repo.get_outline_key_brief(key_id)
        if outline_key:
            key_repo.update_outline_key_expiry(key_id, new_expiry)
            log_admin_action(request, "EDIT_KEY", f"Outline key {key_id}, new expiry: {new_expiry}")
            return RedirectResponse(url="/keys", status_code=303)
        
        # Пробуем V2Ray
        v2ray_key = key_repo.get_v2ray_key_brief(key_id)
        if v2ray_key:
            key_repo.update_v2ray_key_expiry(key_id, new_expiry)
            log_admin_action(request, "EDIT_KEY", f"V2Ray key {key_id}, new expiry: {new_expiry}")
            return RedirectResponse(url="/keys", status_code=303)
        
        return JSONResponse({"error": "Key not found"}, status_code=404)
        
    except Exception as e:
        logging.error(f"Error editing key {key_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/keys/delete/{key_id}")
async def delete_key_route(request: Request, key_id: int):
    """Удаление ключа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")

    key_repo = KeyRepository(DB_PATH)

    # Сначала как Outline
    outline_key = key_repo.get_outline_key_brief(key_id)
    if outline_key:
        user_id, outline_key_id, server_id = outline_key
        if outline_key_id and server_id:
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                c.execute("SELECT api_url, cert_sha256 FROM servers WHERE id = ?", (server_id,))
                server = c.fetchone()
                if server:
                    try:
                        log_admin_action(request, "OUTLINE_DELETE_ATTEMPT", f"Attempting to delete key {outline_key_id} from server {server[0]}")
                        result = delete_key(server[0], server[1], outline_key_id)
                        if result:
                            log_admin_action(request, "OUTLINE_DELETE_SUCCESS", f"Successfully deleted key {outline_key_id} from server")
                        else:
                            log_admin_action(request, "OUTLINE_DELETE_FAILED", f"Failed to delete key {outline_key_id} from server")
                    except Exception as e:
                        log_admin_action(request, "OUTLINE_DELETE_ERROR", f"Failed to delete key {outline_key_id}: {str(e)}")
        
        key_repo.delete_outline_key_by_id(key_id)
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            if user_id:
                c.execute("SELECT COUNT(*) FROM keys WHERE user_id = ?", (user_id,))
                outline_count = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM v2ray_keys WHERE user_id = ?", (user_id,))
                v2ray_count = c.fetchone()[0]
                if outline_count == 0 and v2ray_count == 0:
                    c.execute("UPDATE payments SET revoked = 1 WHERE user_id = ? AND status = 'paid'", (user_id,))
                    conn.commit()
        return RedirectResponse("/keys", status_code=303)

    # Затем V2Ray
    v2ray_key = key_repo.get_v2ray_key_brief(key_id)
    if v2ray_key:
        user_id, v2ray_uuid, server_id = v2ray_key
        if v2ray_uuid and server_id:
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                c.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (server_id,))
                server = c.fetchone()
                if server:
                    try:
                        log_admin_action(request, "V2RAY_DELETE_ATTEMPT", f"Attempting to delete user {v2ray_uuid} from server {server[0]}")
                        protocol_client = V2RayProtocol(server[0], server[1])
                        result = await protocol_client.delete_user(v2ray_uuid)
                        if result:
                            log_admin_action(request, "V2RAY_DELETE_SUCCESS", f"Successfully deleted user {v2ray_uuid} from server")
                        else:
                            log_admin_action(request, "V2RAY_DELETE_FAILED", f"Failed to delete user {v2ray_uuid} from server")
                    except Exception as e:
                        log_admin_action(request, "V2RAY_DELETE_ERROR", f"Failed to delete user {v2ray_uuid}: {str(e)}")
        
        key_repo.delete_v2ray_key_by_id(key_id)
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            if user_id:
                c.execute("SELECT COUNT(*) FROM keys WHERE user_id = ?", (user_id,))
                outline_count = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM v2ray_keys WHERE user_id = ?", (user_id,))
                v2ray_count = c.fetchone()[0]
                if outline_count == 0 and v2ray_count == 0:
                    c.execute("UPDATE payments SET revoked = 1 WHERE user_id = ? AND status = 'paid'", (user_id,))
                    conn.commit()
        return RedirectResponse("/keys", status_code=303)

    return RedirectResponse("/keys", status_code=303)

