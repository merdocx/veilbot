"""
Маршруты для управления ключами VPN
"""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse, Response
import sys
import os
import time
import logging
import asyncio
import math
import re
from datetime import datetime
from io import StringIO
import csv
from typing import Any, Dict, Optional
from starlette import status

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.repositories.key_repository import KeyRepository
from vpn_protocols import ProtocolFactory
from outline import delete_key
from vpn_protocols import V2RayProtocol
import aiohttp
from app.infra.sqlite_utils import open_connection
from app.settings import settings

from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token
from ..dependencies.templates import templates

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH
DB_PATH = DATABASE_PATH


def _format_bytes(num_bytes: Optional[float]) -> str:
    """Format raw bytes into a human-readable string."""
    if num_bytes is None:
        return "—"
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    idx = min(int(math.log(num_bytes, 1024)), len(units) - 1)
    normalized = num_bytes / (1024 ** idx)
    return f"{normalized:.2f} {units[idx]}"


def _parse_traffic_value(raw_value: Optional[str]) -> Dict[str, Any]:
    if not raw_value:
        return {
            "display": "—",
            "bytes": None,
            "unit": None,
            "state": "unknown",
        }

    normalized = raw_value.strip()
    if normalized in {"N/A", "Error"}:
        state = "error" if normalized == "Error" else "na"
        return {
            "display": normalized,
            "bytes": None,
            "unit": None,
            "state": state,
        }

    match = re.match(r"^(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>[KMGT]B)$", normalized, re.IGNORECASE)
    if not match:
        # Attempt to parse MB specific case (legacy "0 GB")
        match = re.match(r"^(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>[KMGT]?B)$", normalized, re.IGNORECASE)
    if not match:
        return {
            "display": normalized,
            "bytes": None,
            "unit": None,
            "state": "raw",
        }

    value = float(match.group("value").replace(",", "."))
    unit = match.group("unit").upper()
    power_map = {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4, "PB": 5}
    power = power_map.get(unit, 0)
    num_bytes = value * (1024 ** power)
    return {
        "display": f"{value:.2f} {unit}",
        "bytes": num_bytes,
        "unit": unit,
        "state": "ok",
    }


def _format_relative(delta_seconds: Optional[int]) -> str:
    if delta_seconds is None:
        return "—"
    if abs(delta_seconds) < 60:
        return "прямо сейчас" if delta_seconds >= 0 else "менее минуты назад"

    future = delta_seconds > 0
    seconds = abs(delta_seconds)
    units = (
        (86400, "д"),
        (3600, "ч"),
        (60, "мин"),
    )
    parts = []
    for unit_seconds, suffix in units:
        if seconds >= unit_seconds:
            qty = seconds // unit_seconds
            parts.append(f"{int(qty)} {suffix}")
            seconds -= qty * unit_seconds
        if len(parts) == 2:
            break

    label = " ".join(parts) if parts else "меньше часа"
    return f"через {label}" if future else f"{label} назад"


def _clamp(value: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    return max(min_value, min(max_value, value))


def _format_duration_components(total_seconds: int) -> Dict[str, int]:
    seconds = abs(total_seconds)
    units = {
        "years": 365 * 24 * 3600,
        "months": 30 * 24 * 3600,
        "days": 24 * 3600,
        "hours": 3600,
        "minutes": 60,
    }
    result = {"years": 0, "months": 0, "days": 0, "hours": 0, "minutes": 0}
    for name, unit_seconds in units.items():
        count, seconds = divmod(seconds, unit_seconds)
        result[name] = int(count)
    return result


def _format_expiry_remaining(expiry_at: int, now_ts: int) -> Dict[str, str]:
    if not expiry_at:
        return {
            "label": "—",
            "state": "unknown",
        }

    delta = expiry_at - now_ts
    components = _format_duration_components(delta)

    parts = []
    labels = {
        "years": "г",
        "months": "мес",
        "days": "д",
        "hours": "ч",
        "minutes": "мин",
    }

    for key, suffix in labels.items():
        value = components[key]
        if value:
            parts.append(f"{value} {suffix}")

    if not parts:
        parts.append("< 1 мин")

    label = " ".join(parts[:3])

    if delta > 0:
        return {
            "label": f"Через {label}",
            "state": "upcoming",
        }
    if delta < 0:
        return {
            "label": f"Просрочен {label} назад",
            "state": "expired",
        }

    return {
        "label": "Истекает прямо сейчас",
        "state": "now",
    }


def _build_key_view_model(row: list[Any] | tuple[Any, ...], now_ts: int) -> Dict[str, Any]:
    row_len = len(row)
    # Поддерживаем как новые расширенные строки, так и старый формат из тестов/фикстур
    is_extended = row_len >= 17

    def get(idx: int | None, default: Any = None) -> Any:
        if idx is None:
            return default
        if idx < 0:
            idx_mod = row_len + idx
            return row[idx_mod] if 0 <= idx_mod < row_len else default
        return row[idx] if row_len > idx else default

    protocol_idx = 9 if is_extended else 8
    tariff_idx = 8 if is_extended else 7
    user_id_idx = 7 if is_extended else None
    limit_idx = 10 if is_extended else 9
    usage_idx = 13 if row_len > 13 else None
    over_limit_idx = 14 if row_len > 14 else None
    notified_idx = 15 if row_len > 15 else None
    traffic_raw_idx = 16 if row_len > 16 else (12 if row_len > 12 else None)

    traffic_raw = get(traffic_raw_idx)
    traffic_info = _parse_traffic_value(traffic_raw)

    # Извлекаем числовой ID из строки вида "206_outline" или "206_v2ray"
    key_id_str = str(row[0])
    if '_' in key_id_str:
        key_id = int(key_id_str.split('_')[0])
    else:
        key_id = int(key_id_str)
    created_at = int(row[3] or 0)
    expiry_at = int(row[4] or 0)
    lifetime_total = max(expiry_at - created_at, 0)
    elapsed = max(now_ts - created_at, 0)
    progress = _clamp((elapsed / lifetime_total) if lifetime_total else (1 if expiry_at and now_ts >= expiry_at else 0))

    is_active = bool(expiry_at and expiry_at > now_ts)
    status_label = "Активен" if is_active else "Истёк"
    status_icon = "check_circle" if is_active else "cancel"
    protocol = (get(protocol_idx, '') or '').lower()
    protocol_meta = {
        "outline": {"label": "Outline", "icon": "lock", "class": "protocol-badge--outline"},
        "v2ray": {"label": "V2Ray", "icon": "security", "class": "protocol-badge--v2ray"},
    }
    protocol_info = protocol_meta.get(protocol, {"label": protocol or "—", "icon": "help_outline", "class": "protocol-badge--neutral"})

    raw_limit_value = get(limit_idx, 0)
    try:
        traffic_limit_mb = int(raw_limit_value)
    except (TypeError, ValueError):
        traffic_limit_mb = 0
    traffic_limit_bytes = None
    if traffic_limit_mb:
        traffic_limit_bytes = float(traffic_limit_mb) * 1024 * 1024

    traffic_usage_bytes_db = get(usage_idx, None)
    if traffic_usage_bytes_db is not None:
        try:
            traffic_info["bytes"] = float(traffic_usage_bytes_db)
            traffic_info["display"] = _format_bytes(traffic_usage_bytes_db)
            traffic_info["state"] = "calculated"
        except (TypeError, ValueError):
            parsed_usage = _parse_traffic_value(str(traffic_usage_bytes_db))
            if parsed_usage["bytes"] is not None:
                traffic_info.update(parsed_usage)

    over_limit_at = get(over_limit_idx)
    notified_value = get(notified_idx)
    over_limit_notified = bool(notified_value) if notified_value is not None else False

    usage_percent = None
    over_limit_deadline = over_limit_at + 86400 if over_limit_at else None

    over_limit = False
    over_limit_display = None
    over_limit_state = None
    if traffic_info["bytes"] is not None and traffic_limit_bytes:
        usage_percent = _clamp(traffic_info["bytes"] / traffic_limit_bytes, 0.0, 1.0)
        over_limit = traffic_info["bytes"] > traffic_limit_bytes
        if over_limit and over_limit_deadline:
            deadline_info = _format_expiry_remaining(over_limit_deadline, now_ts)
            over_limit_display = deadline_info["label"]
            over_limit_state = deadline_info["state"]

    expiry_remaining = _format_expiry_remaining(expiry_at, now_ts)

    # Формируем ID с протоколом для отображения (чтобы различать ключи с одинаковым ID из разных таблиц)
    display_id = f"{key_id}_{protocol}" if protocol else str(key_id)

    return {
        "id": display_id,
        "numeric_id": key_id,  # Сохраняем числовой ID для операций
        "uuid": row[1],
        "access_url": row[2],
        "email": row[6] or '',
        "telegram_id": str(get(user_id_idx)) if get(user_id_idx) else '',
        "server": row[5] or '',
        "tariff": get(tariff_idx) or '',
        "protocol": protocol,
        "protocol_label": protocol_info["label"],
        "protocol_icon": protocol_info["icon"],
        "protocol_badge_class": protocol_info["class"],
        "created_at": created_at,
        "created_display": created_at and datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M") or "—",
        "created_relative": _format_relative(now_ts - created_at),
        "expiry_at": expiry_at,
        "expiry_display": expiry_at and datetime.fromtimestamp(expiry_at).strftime("%d.%m.%Y %H:%M") or "—",
        "expiry_relative": _format_relative(expiry_at - now_ts if expiry_at else None),
        "expiry_iso": expiry_at and datetime.fromtimestamp(expiry_at).strftime("%Y-%m-%dT%H:%M") or "",
        "expiry_remaining": expiry_remaining["label"],
        "expiry_remaining_state": expiry_remaining["state"],
        "status": "active" if is_active else "expired",
        "status_label": status_label,
        "status_icon": status_icon,
        "status_class": "status-icon--active" if is_active else "status-icon--expired",
        "lifetime_progress": progress,
        "traffic": {
            "raw": traffic_raw or "—",
            "display": traffic_info["display"],
            "bytes": traffic_info["bytes"],
            "usage_percent": usage_percent,
            "limit_bytes": traffic_limit_bytes,
            "limit_mb": traffic_limit_mb,
            "limit_display": _format_bytes(traffic_limit_bytes) if traffic_limit_bytes else "—",
            "state": traffic_info["state"],
            "usage_bytes_db": traffic_usage_bytes_db,
            "over_limit": over_limit,
            "over_limit_at": over_limit_at,
            "over_limit_deadline": over_limit_deadline,
            "over_limit_deadline_display": over_limit_display,
            "over_limit_deadline_state": over_limit_state,
            "over_limit_notified": over_limit_notified,
        },
    }


def _compute_key_stats(db_path: str, now_ts: int) -> Dict[str, int]:
    with open_connection(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM keys")
        outline_total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM v2ray_keys")
        v2ray_total = c.fetchone()[0]
        total = int(outline_total) + int(v2ray_total)

        c.execute("SELECT COUNT(*) FROM keys WHERE expiry_at > ?", (now_ts,))
        outline_active = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM v2ray_keys WHERE expiry_at > ?", (now_ts,))
        v2ray_active = c.fetchone()[0]
        active = int(outline_active) + int(v2ray_active)

    expired = max(total - active, 0)
    return {
        "total": total,
        "active": active,
        "expired": expired,
        "v2ray": int(v2ray_total),
    }


def _is_json_request(request: Request) -> bool:
    accept_header = request.headers.get("accept", "")
    requested_with = request.headers.get("x-requested-with", "")
    return "application/json" in accept_header.lower() or requested_with.lower() == "xmlhttprequest"


def _append_default_traffic(row: tuple[Any, ...] | list[Any]) -> list[Any]:
    extended = list(row)
    # Гарантируем наличие новых колонок (traffic_usage_bytes, traffic_over_limit_at, traffic_over_limit_notified)
    while len(extended) < 16:
        extended.append(None)
    # Последняя колонка предназначена для строкового представления трафика
    if len(extended) == 16:
        extended.append('—')
    return extended


async def _update_key_expiry_internal(
    request: Request,
    key_id: int,
    new_expiry: int,
    key_repo: KeyRepository,
    traffic_limit_mb: int | None = None,
) -> Dict[str, Any]:
    outline_key = key_repo.get_outline_key_brief(key_id)
    if outline_key:
        key_repo.update_outline_key_expiry(key_id, new_expiry, traffic_limit_mb)
        details = f"Outline key {key_id}, new expiry: {new_expiry}"
        if traffic_limit_mb is not None:
            details += f", traffic_limit_mb: {traffic_limit_mb}"
        log_admin_action(request, "EDIT_KEY", details)
        return {"protocol": "outline"}

    v2ray_key = key_repo.get_v2ray_key_brief(key_id)
    if v2ray_key:
        key_repo.update_v2ray_key_expiry(key_id, new_expiry, traffic_limit_mb)
        details = f"V2Ray key {key_id}, new expiry: {new_expiry}"
        if traffic_limit_mb is not None:
            details += f", traffic_limit_mb: {traffic_limit_mb}"
        log_admin_action(request, "EDIT_KEY", details)
        return {"protocol": "v2ray"}

    raise ValueError("Key not found")


async def _delete_key_internal(request: Request, key_id: int | str, key_repo: KeyRepository) -> Dict[str, Any]:
    # Парсим ID если он в формате "206_outline" или "206_v2ray"
    if isinstance(key_id, str) and '_' in key_id:
        parts = key_id.split('_')
        key_id = int(parts[0])
        protocol = parts[1] if len(parts) > 1 else None
    else:
        protocol = None
    
    outline_key = key_repo.get_outline_key_brief(key_id)
    if outline_key:
        user_id, outline_key_id, server_id = outline_key
        if outline_key_id and server_id:
            with open_connection(DB_PATH) as conn:
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
                        logging.error(f"Error deleting Outline key {outline_key_id}: {e}", exc_info=True)
                        log_admin_action(request, "OUTLINE_DELETE_ERROR", f"Failed to delete key {outline_key_id}: {str(e)}")

        try:
            key_repo.delete_outline_key_by_id(key_id)
            with open_connection(DB_PATH) as conn:
                c = conn.cursor()
                if user_id:
                    c.execute("SELECT COUNT(*) FROM keys WHERE user_id = ?", (user_id,))
                    outline_count = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM v2ray_keys WHERE user_id = ?", (user_id,))
                    v2ray_count = c.fetchone()[0]
                    if outline_count == 0 and v2ray_count == 0:
                        c.execute("UPDATE payments SET revoked = 1 WHERE user_id = ? AND status = 'paid'", (user_id,))
                        conn.commit()
        except Exception as e:
            logging.error(f"Error deleting Outline key from database: {e}", exc_info=True)
            log_admin_action(request, "OUTLINE_KEY_DELETE_DB_ERROR", f"Failed to delete key {key_id} from database: {str(e)}")

        return {"protocol": "outline"}

    v2ray_key = key_repo.get_v2ray_key_brief(key_id)
    if v2ray_key:
        user_id, v2ray_uuid, server_id = v2ray_key
        protocol_client = None
        if v2ray_uuid and server_id:
            with open_connection(DB_PATH) as conn:
                c = conn.cursor()
                c.execute("SELECT api_url, api_key FROM servers WHERE id = ?", (server_id,))
                server = c.fetchone()
                if server and server[0] and server[1]:
                    try:
                        log_admin_action(request, "V2RAY_DELETE_ATTEMPT", f"Attempting to delete user {v2ray_uuid} from server {server[0]}")
                        protocol_client = V2RayProtocol(server[0], server[1])
                        result = await protocol_client.delete_user(v2ray_uuid)
                        if result:
                            log_admin_action(request, "V2RAY_DELETE_SUCCESS", f"Successfully deleted user {v2ray_uuid} from server")
                        else:
                            log_admin_action(request, "V2RAY_DELETE_FAILED", f"Failed to delete user {v2ray_uuid} from server")
                    except Exception as e:
                        logging.error(f"Error deleting V2Ray key {v2ray_uuid}: {e}", exc_info=True)
                        log_admin_action(request, "V2RAY_DELETE_ERROR", f"Failed to delete user {v2ray_uuid}: {str(e)}")
                    finally:
                        if protocol_client:
                            try:
                                await protocol_client.close()
                            except Exception as close_error:
                                logging.warning(f"Error closing V2Ray protocol client: {close_error}")
                elif server:
                    logging.warning(f"Server {server_id} found but api_url or api_key is missing")
                    log_admin_action(request, "V2RAY_DELETE_SERVER_CONFIG_ERROR", f"Server {server_id} configuration incomplete")

        try:
            key_repo.delete_v2ray_key_by_id(key_id)
            with open_connection(DB_PATH) as conn:
                c = conn.cursor()
                if user_id:
                    c.execute("SELECT COUNT(*) FROM keys WHERE user_id = ?", (user_id,))
                    outline_count = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM v2ray_keys WHERE user_id = ?", (user_id,))
                    v2ray_count = c.fetchone()[0]
                    if outline_count == 0 and v2ray_count == 0:
                        c.execute("UPDATE payments SET revoked = 1 WHERE user_id = ? AND status = 'paid'", (user_id,))
                        conn.commit()
        except Exception as e:
            logging.error(f"Error deleting key from database: {e}", exc_info=True)
            log_admin_action(request, "KEY_DELETE_DB_ERROR", f"Failed to delete key {key_id} from database: {str(e)}")

        return {"protocol": "v2ray"}

    raise ValueError("Key not found")


def _load_key_view_model(key_repo: KeyRepository, key_id: int | str, now_ts: int) -> Optional[Dict[str, Any]]:
    # Парсим ID если он в формате "206_outline" или "206_v2ray"
    if isinstance(key_id, str) and '_' in key_id:
        parts = key_id.split('_')
        numeric_id = int(parts[0])
        protocol_hint = parts[1] if len(parts) > 1 else None
    else:
        protocol_hint = None
        numeric_id = int(key_id)
    
    row = key_repo.get_key_unified_by_id(numeric_id)
    if not row:
        return None
    
    # Если был указан протокол, проверяем что он совпадает
    if protocol_hint:
        returned_id = str(row[0])
        returned_protocol = row[9] if len(row) > 9 else None
        expected_suffix = f"_{protocol_hint}"
        
        if not returned_id.endswith(expected_suffix) or returned_protocol != protocol_hint:
            # Если протокол не совпадает, ищем ключ напрямую в нужной таблице
            from app.infra.sqlite_utils import open_connection
            from app.settings import settings as app_settings
            
            with open_connection(app_settings.DATABASE_PATH) as conn:
                c = conn.cursor()
                if protocol_hint == 'outline':
                    c.execute("""
                        SELECT k.id || '_outline' as id, k.key_id, k.access_url, k.created_at, k.expiry_at,
                               IFNULL(s.name,''), k.email, k.user_id, IFNULL(t.name,''), 'outline' as protocol,
                               COALESCE(k.traffic_limit_mb, 0), '' as api_url, '' as api_key,
                               0 AS traffic_usage_bytes, NULL AS traffic_over_limit_at, 0 AS traffic_over_limit_notified
                        FROM keys k
                        LEFT JOIN servers s ON k.server_id = s.id
                        LEFT JOIN tariffs t ON k.tariff_id = t.id
                        WHERE k.id = ?
                    """, (numeric_id,))
                elif protocol_hint == 'v2ray':
                    c.execute("""
                        SELECT k.id || '_v2ray' as id, k.v2ray_uuid as key_id,
                               COALESCE(k.client_config, '') as access_url,
                               k.created_at, k.expiry_at,
                               IFNULL(s.name,''), k.email, k.user_id, IFNULL(t.name,''), 'v2ray' as protocol,
                               COALESCE(k.traffic_limit_mb, 0), IFNULL(s.api_url,''), IFNULL(s.api_key,''),
                               COALESCE(k.traffic_usage_bytes, 0), NULL AS traffic_over_limit_at,
                               0 AS traffic_over_limit_notified
                        FROM v2ray_keys k
                        LEFT JOIN servers s ON k.server_id = s.id
                        LEFT JOIN tariffs t ON k.tariff_id = t.id
                        WHERE k.id = ?
                    """, (numeric_id,))
                else:
                    c = None
                
                if c:
                    row = c.fetchone()
                    if not row:
                        return None
    
    normalized_row = _append_default_traffic(row)
    return _build_key_view_model(normalized_row, now_ts)


async def get_key_monthly_traffic(key_uuid: str, protocol: str, server_config: dict, server_id: int = None) -> str:
    """Get monthly traffic for a specific key in GB"""
    v2ray = None
    try:
        if protocol == 'v2ray':
            # ИСПРАВЛЕНИЕ: Не используем кеш, т.к. он содержит данные из get_monthly_traffic(),
            # а нам нужны данные из get_traffic_stats() с interface_traffic.total_bytes
            
            # Create V2Ray protocol instance
            v2ray = ProtocolFactory.create_protocol('v2ray', server_config)
            
            try:
                # ИСПРАВЛЕНИЕ: Используем GET /api/keys/{key_id}/traffic/history
                # Сначала получаем key_id (id из API) по UUID
                key_info_data = await v2ray.get_key_info(key_uuid)
                api_key_id = key_info_data.get('id') or key_info_data.get('uuid')
                
                if not api_key_id:
                    logging.warning(f"[TRAFFIC] No key_id found for UUID {key_uuid}")
                    return "0 GB"
                
                # Получаем traffic/history для этого ключа
                history = await v2ray.get_key_traffic_history(str(api_key_id))
                
                if history and history.get('data'):
                    data = history.get('data', {})
                    total_traffic = data.get('total_traffic', {})
                    
                    if total_traffic and isinstance(total_traffic, dict):
                        total_bytes = total_traffic.get('total_bytes', 0)
                        
                        if total_bytes > 0:
                            logging.info(f"[TRAFFIC] Found traffic (traffic/history) for {key_uuid}: {total_bytes} bytes ({total_bytes/(1024**4):.2f} TB)")
                            
                            # Форматируем в TB если > 1TB, иначе в GB
                            if total_bytes >= (1024 ** 4):  # >= 1TB
                                traffic_tb = total_bytes / (1024 ** 4)
                                return f"{traffic_tb:.2f} TB"
                            else:
                                traffic_gb = total_bytes / (1024 * 1024 * 1024)
                                return f"{traffic_gb:.2f} GB"
                
                return "0 GB"
            finally:
                await v2ray.close()
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
    
    # Сортировка по дате создания по умолчанию (всегда)
    # Игнорируем параметры sort_by и sort_order из URL, всегда используем created_at
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
    # ИСПРАВЛЕНИЕ: Сохраняем исходный порядок ключей из БД
    keys_with_traffic = []
    
    # ИСПРАВЛЕНИЕ: Сохраняем исходный порядок из rows для правильной сортировки
    # Создаем словарь для хранения всех ключей в исходном порядке
    all_keys_dict = {}
    
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
    # ИСПРАВЛЕНИЕ: Сохраняем исходный порядок ключей
    v2ray_tasks = []
    v2ray_keys_data = {}
    keys_order = []  # Сохраняем порядок всех ключей
    
    for key in rows:
        key_id = key[0]
        keys_order.append(key_id)  # Сохраняем порядок
        
        if len(key) > 8 and key[8] == 'v2ray':
            v2ray_keys_data[key_id] = key
            v2ray_tasks.append(fetch_v2ray_config(key))
            all_keys_dict[key_id] = {'type': 'v2ray', 'data': list(key) + ["—"]}
        else:
            # Outline keys - сохраняем данные
            all_keys_dict[key_id] = {'type': 'outline', 'data': list(key) + ["N/A"]}
    
    # Параллельно загружаем все конфигурации
    if v2ray_tasks:
        config_results = await asyncio.gather(*v2ray_tasks, return_exceptions=True)
        
        processed_configs = {}
        
        for result in config_results:
            if isinstance(result, Exception):
                logging.error(f"Error in parallel V2Ray fetch: {result}")
                continue
            
            key_id, real_config, _ = result
            key = v2ray_keys_data[key_id]
            key_list = list(key)
            key_list[2] = real_config  # Update access_url
            processed_configs[key_id] = key_list

        for key_id, key_list in processed_configs.items():
            if len(key_list) > 8 and key_list[8] == 'v2ray':
                key_with_placeholder = list(key_list) + ["—"]
                all_keys_dict[key_id] = {'type': 'v2ray', 'data': key_with_placeholder}
    
    # ИСПРАВЛЕНИЕ: Восстанавливаем исходный порядок из БД
    for key_id in keys_order:
        key_info = all_keys_dict.get(key_id)
        if key_info:
            keys_with_traffic.append(key_info['data'])
    
    now_ts = int(time.time())
    log_admin_action(request, "KEYS_QUERY_RESULT", f"Total keys: {len(keys_with_traffic)}")

    # CSV Export requires raw data before pagination trim
    if export and str(export).lower() in ("csv", "true", "1"):
        try:
            buffer = StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["id", "protocol", "key", "tariff", "email", "server", "created_at", "expiry_at", "status", "traffic"])
            for key_row in keys_with_traffic:
                view = _build_key_view_model(key_row, now_ts)
                writer.writerow([
                    view["id"],
                    view["protocol"],
                    view["access_url"],
                    view["tariff"],
                    view["email"],
                    view["server"],
                    view["created_at"],
                    view["expiry_at"],
                    view["status"],
                    view["traffic"]["display"],
                ])
            content = buffer.getvalue()
            return Response(content, media_type="text/csv", headers={
                "Content-Disposition": "attachment; filename=keys_export.csv"
            })
        except Exception as e:
            logging.error(f"CSV export error: {e}")

    pages = (total + limit - 1) // limit

    # Генерируем cursor для следующей страницы
    next_cursor = None
    if len(rows) > limit:
        from app.infra.pagination import KeysetPagination
        last_row = rows[-1]
        if len(last_row) >= 4:
            created_at_cursor = last_row[3] or 0
            key_id_cursor = last_row[0]
            next_cursor = KeysetPagination.encode_cursor(created_at_cursor, key_id_cursor)
        keys_with_traffic = keys_with_traffic[:limit]

    key_models = [_build_key_view_model(key_row, now_ts) for key_row in keys_with_traffic]
    active_count = sum(1 for key in key_models if key["status"] == "active")
    expired_count = total - active_count
    v2ray_count = sum(1 for key in key_models if key["protocol"] == "v2ray")

    return templates.TemplateResponse("keys.html", {
        "request": request,
        "keys": key_models,
        "current_time": now_ts,
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
        "stats_overview": _compute_key_stats(DB_PATH, now_ts),
    })


@router.get("/keys/edit/{key_id}")
async def keys_edit_page(request: Request, key_id: str):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    # Парсим ID если он в формате "206_outline" или "206_v2ray"
    if '_' in key_id:
        parts = key_id.split('_')
        numeric_id = int(parts[0])
    else:
        numeric_id = int(key_id)

    key_repo = KeyRepository(DB_PATH)
    now_ts = int(time.time())
    try:
        key_view = _load_key_view_model(key_repo, numeric_id, now_ts)
    except Exception as error:
        logging.error(f"[ADMIN][KEYS] Failed to load key {key_id}: {error}", exc_info=True)
        key_view = None

    if not key_view:
        log_admin_action(request, "KEY_EDIT_NOT_FOUND", f"Tried to open edit page for missing key {key_id}")
        return RedirectResponse("/keys", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        "keys_edit.html",
        {
            "request": request,
            "key": key_view,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.get("/api/keys/{key_id}/traffic")
async def get_key_traffic_api(request: Request, key_id: int):
    """API endpoint для ленивой загрузки трафика ключа"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        key_repo = KeyRepository(DB_PATH)
        now_ts = int(time.time())
        key_view = _load_key_view_model(key_repo, key_id, now_ts)
        if not key_view:
            return JSONResponse({"error": "Key not found"}, status_code=404)

        traffic_info = key_view.get("traffic", {})
        display_value = traffic_info.get("display", "—")
        protocol = key_view.get("protocol", "outline")

        return JSONResponse({
            "traffic": display_value,
            "protocol": protocol,
            "key": key_view,
            "key_id": key_id,
        })
    except Exception as e:
        logging.error(f"Error getting traffic for key {key_id}: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/keys/edit/{key_id}")
async def edit_key_route(request: Request, key_id: str):
    # Парсим ID если он в формате "206_outline" или "206_v2ray"
    if '_' in key_id:
        parts = key_id.split('_')
        numeric_id = int(parts[0])
    else:
        numeric_id = int(key_id)
    """Редактирование срока действия ключа"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    form = await request.form()
    new_expiry_str = form.get("new_expiry")
    traffic_limit_str = form.get("traffic_limit_mb")
 
    if not new_expiry_str:
        if _is_json_request(request):
            return JSONResponse({"error": "new_expiry is required"}, status_code=400)
        return RedirectResponse(url="/keys", status_code=303)
 
    try:
        # Парсим datetime-local format (YYYY-MM-DDTHH:mm) в timestamp
        dt = datetime.strptime(new_expiry_str, "%Y-%m-%dT%H:%M")
        new_expiry = int(dt.timestamp())

        new_limit: int | None = None
        if traffic_limit_str is not None:
            trimmed = traffic_limit_str.strip()
            if trimmed == "":
                new_limit = 0
            else:
                try:
                    new_limit = int(trimmed)
                except ValueError:
                    if _is_json_request(request):
                        return JSONResponse({"error": "traffic_limit_mb must be an integer"}, status_code=400)
                    return RedirectResponse(url="/keys", status_code=303)
                if new_limit < 0:
                    if _is_json_request(request):
                        return JSONResponse({"error": "traffic_limit_mb must be >= 0"}, status_code=400)
                    return RedirectResponse(url="/keys", status_code=303)
 
        key_repo = KeyRepository(DB_PATH)
        try:
            await _update_key_expiry_internal(request, numeric_id, new_expiry, key_repo, new_limit)
        except ValueError:
            if _is_json_request(request):
                return JSONResponse({"error": "Key not found"}, status_code=404)
            return RedirectResponse(url="/keys", status_code=303)
 
        if _is_json_request(request):
            now_ts = int(time.time())
            key_view = _load_key_view_model(key_repo, key_id, now_ts)
            stats = _compute_key_stats(DB_PATH, now_ts)
            return JSONResponse({
                "message": "Параметры ключа обновлены",
                "key": key_view,
                "stats": stats,
            })
 
        return RedirectResponse(url="/keys", status_code=303)

    except Exception as e:
        logging.error(f"Error editing key {key_id}: {e}")
        if _is_json_request(request):
            return JSONResponse({"error": str(e)}, status_code=500)
        return RedirectResponse(url="/keys", status_code=303)


@router.get("/keys/delete/{key_id}")
async def delete_key_route(request: Request, key_id: str):
    """Удаление ключа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")

    try:
        key_repo = KeyRepository(DB_PATH)
        await _delete_key_internal(request, key_id, key_repo)
    except ValueError:
        logging.warning(f"Key {key_id} not found for deletion")
        log_admin_action(request, "KEY_DELETE_NOT_FOUND", f"Key {key_id} not found")
    except Exception as e:
        logging.error(f"Error deleting key {key_id}: {e}", exc_info=True)
        log_admin_action(request, "KEY_DELETE_ERROR", f"Failed to delete key {key_id}: {str(e)}")
    return RedirectResponse("/keys", status_code=303)


@router.delete("/api/keys/{key_id}")
async def delete_key_api(request: Request, key_id: str):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        key_repo = KeyRepository(DB_PATH)
        result = await _delete_key_internal(request, key_id, key_repo)
        now_ts = int(time.time())
        stats = _compute_key_stats(DB_PATH, now_ts)
        return JSONResponse({
            "message": "Ключ удалён",
            "key_id": key_id,
            "protocol": result.get("protocol"),
            "stats": stats,
        })
    except ValueError:
        return JSONResponse({"error": "Key not found"}, status_code=404)
    except Exception as e:
        logging.error(f"Error deleting key {key_id}: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)

