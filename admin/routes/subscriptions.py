"""
API маршруты для подписок V2Ray
"""
from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import PlainTextResponse, RedirectResponse, JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
import logging
import sys
import os
import time
import math
from datetime import datetime
from typing import Optional, Dict, Any

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bot.services.subscription_service import SubscriptionService, validate_subscription_token
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.settings import settings
from app.infra.sqlite_utils import open_connection
from app.infra.foreign_keys import safe_foreign_keys_off
from vpn_protocols import V2RayProtocol
from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token
from ..dependencies.templates import templates
from scripts.sync_all_keys_with_servers import sync_all_keys_with_servers

logger = logging.getLogger(__name__)

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH
DB_PATH = DATABASE_PATH

# Rate limiter для подписок (60 запросов в минуту на токен)
# Используем токен как ключ для rate limiting
def get_token_for_rate_limit(request: Request):
    """Получить токен из path params для rate limiting"""
    token = request.path_params.get("token")
    if token:
        return token
    return get_remote_address(request)

limiter = Limiter(key_func=get_token_for_rate_limit)


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


def _clamp(value: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    return max(min_value, min(max_value, value))


def _format_timestamp(ts: int | None) -> str:
    """Форматировать timestamp в читаемый формат"""
    if not ts or ts == 0:
        return "—"
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, OSError):
        return "—"


def _format_expiry_remaining(expires_at: int | None, now_ts: int) -> dict:
    """Форматировать оставшееся время до истечения"""
    if not expires_at:
        return {"label": "—", "state": "unknown"}
    
    delta = expires_at - now_ts
    
    if delta > 0:
        days = delta // 86400
        hours = (delta % 86400) // 3600
        minutes = (delta % 3600) // 60
        
        parts = []
        if days > 0:
            parts.append(f"{days} д")
        if hours > 0:
            parts.append(f"{hours} ч")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes} мин")
        
        label = " ".join(parts[:2]) if parts else "< 1 мин"
        return {"label": f"Через {label}", "state": "active"}
    else:
        days_ago = abs(delta) // 86400
        hours_ago = (abs(delta) % 86400) // 3600
        
        parts = []
        if days_ago > 0:
            parts.append(f"{days_ago} д")
        if hours_ago > 0:
            parts.append(f"{hours_ago} ч")
        
        label = " ".join(parts[:2]) if parts else "< 1 ч"
        return {"label": f"Истёк {label} назад", "state": "expired"}


def _compute_subscription_stats(db_path: str, now_ts: int) -> Dict[str, int]:
    """Вычислить статистику подписок для обновления UI"""
    with open_connection(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM subscriptions")
        total = int(c.fetchone()[0])
        
        c.execute("SELECT COUNT(*) FROM subscriptions WHERE expires_at > ? AND is_active = 1", (now_ts,))
        active = int(c.fetchone()[0])
        
    expired = max(total - active, 0)
    return {
        "total": total,
        "active": active,
        "expired": expired,
    }


@router.get("/subscriptions")
async def subscriptions_page(request: Request, page: int = 1, limit: int = 50):
    """Страница списка подписок"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    try:
        log_admin_action(request, "SUBSCRIPTIONS_PAGE_ACCESS")
        
        subscription_repo = SubscriptionRepository(DB_PATH)
        user_repo = UserRepository(DB_PATH)
        total = subscription_repo.count_subscriptions()
        
        offset = (page - 1) * limit
        rows = subscription_repo.list_subscriptions(limit=limit, offset=offset)
        
        now_ts = int(time.time())
        
        # Формируем модели для отображения
        subscription_models = []
        active_count = 0
        expired_count = 0
        
        for row in rows:
            (
                sub_id, user_id, token, created_at, expires_at, tariff_id,
                is_active, last_updated_at, notified, tariff_name, keys_count, traffic_limit_mb
            ) = row
            
            # Обработка None значений
            created_at = created_at if created_at is not None else 0
            expires_at = expires_at if expires_at is not None else 0
            
            # Получаем email пользователя
            user_email = ""
            with open_connection(DB_PATH) as conn:
                cursor = conn.cursor()
                user_email = UserRepository._resolve_user_email(cursor, user_id)
            
            expiry_info = _format_expiry_remaining(expires_at if expires_at > 0 else None, now_ts)
            is_expired = expires_at > 0 and expires_at <= now_ts
            
            if is_expired:
                expired_count += 1
            else:
                active_count += 1
            
            # Вычисляем прогресс для прогресс-бара
            lifetime_total = max(expires_at - created_at, 0) if expires_at > 0 and created_at > 0 else 0
            elapsed = max(now_ts - created_at, 0) if created_at > 0 else 0
            lifetime_progress = 0.0
            if lifetime_total > 0:
                lifetime_progress = max(0.0, min(1.0, elapsed / lifetime_total))
            elif expires_at > 0 and now_ts >= expires_at:
                lifetime_progress = 1.0
            
            # Получаем список ключей подписки
            keys_list = subscription_repo.get_subscription_keys_list(sub_id)
            keys_info = []
            for key_row in keys_list:
                (
                    key_id,
                    protocol,
                    identifier,
                    email,
                    key_created_at,
                    key_expires_at,
                    server_name,
                    country,
                    traffic_limit_mb,
                    traffic_usage_bytes,
                ) = key_row
                keys_info.append({
                    "id": key_id,
                    "protocol": protocol,
                    "identifier": identifier[:8] + "..." if identifier else "—",
                    "email": email or "—",
                    "server": server_name or "—",
                    "country": country or "—",
                    "created_at": _format_timestamp(key_created_at),
                    "expires_at": _format_timestamp(key_expires_at),
                })
            
            # Вычисляем трафик подписки
            traffic_usage_bytes = subscription_repo.get_subscription_traffic_sum(sub_id)
            traffic_limit_bytes = subscription_repo.get_subscription_traffic_limit(sub_id)
            
            traffic_display = _format_bytes(traffic_usage_bytes) if traffic_usage_bytes is not None else "—"
            traffic_limit_display = _format_bytes(traffic_limit_bytes) if traffic_limit_bytes and traffic_limit_bytes > 0 else "—"
            
            usage_percent = None
            over_limit = False
            if traffic_usage_bytes is not None and traffic_limit_bytes and traffic_limit_bytes > 0:
                usage_percent = _clamp(traffic_usage_bytes / traffic_limit_bytes, 0.0, 1.0)
                over_limit = traffic_usage_bytes > traffic_limit_bytes
            
            traffic_info = {
                "display": traffic_display,
                "limit_display": traffic_limit_display,
                "limit_mb": traffic_limit_mb if (traffic_limit_mb is not None and traffic_limit_mb > 0) else None,
                "usage_percent": usage_percent,
                "over_limit": over_limit,
            }
            
            subscription_models.append({
                "id": sub_id,
                "user_id": user_id,
                "user_email": user_email or "—",
                "token": (token[:16] + "..." if len(token) > 16 else token) if token else "—",
                "token_full": token or "",
                "created_at": _format_timestamp(created_at) if created_at > 0 else "—",
                "created_at_ts": created_at,
                "expires_at": _format_timestamp(expires_at) if expires_at > 0 else "—",
                "expires_at_ts": expires_at,
                "expires_at_iso": datetime.fromtimestamp(expires_at).strftime("%Y-%m-%dT%H:%M") if expires_at > 0 else "",
                "expiry_remaining": expiry_info["label"],
                "expiry_state": expiry_info["state"],
                "lifetime_progress": lifetime_progress,
                "tariff_id": tariff_id,
                "tariff_name": tariff_name or "—",
                "is_active": bool(is_active),
                "status": "Активна" if (is_active and not is_expired) else "Истекла",
                "status_class": "status-active" if (is_active and not is_expired) else "status-expired",
                "keys_count": keys_count or 0,
                "subscription_keys": keys_info,
                "traffic": traffic_info,
                "traffic_limit_mb": traffic_limit_mb if (traffic_limit_mb is not None and traffic_limit_mb > 0) else None,
            })
        
        pages = (total + limit - 1) // limit if total > 0 else 1
        
        return templates.TemplateResponse("subscriptions.html", {
            "request": request,
            "subscriptions": subscription_models,
            "current_time": now_ts,
            "page": page,
            "limit": limit,
            "total": total,
            "active_count": active_count,
            "expired_count": expired_count,
            "pages": pages,
            "csrf_token": get_csrf_token(request),
        })
    except Exception as e:
        logger.error(f"Error in subscriptions_page: {e}", exc_info=True)
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Traceback: {error_details}")
        # Возвращаем простую страницу с ошибкой
        from fastapi.responses import HTMLResponse
        error_html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Ошибка</title></head>
        <body>
            <h1>Ошибка при загрузке подписок</h1>
            <p>{str(e)}</p>
            <p><a href="/subscriptions">Попробовать снова</a></p>
        </body>
        </html>
        """
        return HTMLResponse(content=error_html, status_code=500)


@router.post("/subscriptions/edit/{subscription_id}")
async def edit_subscription_route(request: Request, subscription_id: int):
    """Редактирование срока действия подписки и всех связанных ключей"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    form = await request.form()
    
    # Логируем все поля формы для отладки
    form_dict = dict(form)
    logger.info(f"Edit subscription {subscription_id}: form fields: {list(form_dict.keys())}")
    logger.info(f"Edit subscription {subscription_id}: all form values: {dict(form)}")
    
    new_expiry_str = form.get("new_expiry")
    traffic_limit_str = form.get("traffic_limit_mb")
    
    logger.info(f"Edit subscription {subscription_id}: new_expiry={new_expiry_str}, traffic_limit_mb={traffic_limit_str!r} (type: {type(traffic_limit_str)})")
    
    # Дополнительная проверка - может быть значение приходит под другим ключом
    if traffic_limit_str is None:
        # Проверяем все возможные варианты ключей
        for key in form_dict.keys():
            if 'traffic' in key.lower() or 'limit' in key.lower():
                logger.warning(f"Found similar key: {key} = {form_dict[key]}")
    
    if not new_expiry_str:
        if _is_json_request(request):
            return JSONResponse({"error": "new_expiry is required"}, status_code=400)
        return RedirectResponse(url="/subscriptions", status_code=303)
    
    try:
        # Парсим datetime-local format (YYYY-MM-DDTHH:mm) в timestamp
        dt = datetime.strptime(new_expiry_str, "%Y-%m-%dT%H:%M")
        new_expiry = int(dt.timestamp())
        
        # Парсим лимит трафика (логика как в keys.py)
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
                    return RedirectResponse(url="/subscriptions", status_code=303)
                if new_limit < 0:
                    if _is_json_request(request):
                        return JSONResponse({"error": "traffic_limit_mb must be >= 0"}, status_code=400)
                    return RedirectResponse(url="/subscriptions", status_code=303)
        
        subscription_repo = SubscriptionRepository(DB_PATH)
        subscription = subscription_repo.get_subscription_by_id(subscription_id)
        
        if not subscription:
            if _is_json_request(request):
                return JSONResponse({"error": "Subscription not found"}, status_code=404)
            return RedirectResponse(url="/subscriptions", status_code=303)
        
        # Обновляем срок подписки
        subscription_repo.extend_subscription(subscription_id, new_expiry)
        
        # Обновляем лимит трафика (только если значение было передано)
        if new_limit is not None:
            logger.info(f"About to update subscription {subscription_id} traffic_limit_mb: new_limit={new_limit}, type={type(new_limit)}")
            subscription_repo.update_subscription_traffic_limit(subscription_id, new_limit)
            logger.info(f"Updated subscription {subscription_id} traffic_limit_mb to {new_limit}")
            
            # Синхронно обновляем лимиты трафика всех ключей подписки
            updated_keys_traffic = subscription_repo.update_subscription_keys_traffic_limit(subscription_id, new_limit)
            logger.info(f"Updated traffic_limit_mb for {updated_keys_traffic} keys in subscription {subscription_id}")
        
        # Проверяем, что значение действительно сохранилось
        check_sub = subscription_repo.get_subscription_by_id(subscription_id)
        if check_sub:
            check_limit = check_sub[-1] if len(check_sub) > 11 else None
            logger.info(f"Verified subscription {subscription_id} traffic_limit_mb in DB: {check_limit}")
        
        # Обновляем срок всех связанных ключей
        updated_keys = subscription_repo.update_subscription_keys_expiry(subscription_id, new_expiry)
        
        log_action_msg = f"Subscription ID: {subscription_id}, new expiry: {new_expiry}, updated {updated_keys} keys"
        if new_limit is not None:
            log_action_msg += f", traffic_limit_mb: {new_limit}"
        log_admin_action(
            request,
            "EDIT_SUBSCRIPTION",
            log_action_msg
        )
        
        if _is_json_request(request):
            try:
                # Возвращаем обновленные данные подписки для обновления UI
                now_ts = int(time.time())
                subscription_updated = subscription_repo.get_subscription_by_id(subscription_id)
                
                if not subscription_updated:
                    logger.error(f"Subscription {subscription_id} not found after update")
                    return JSONResponse({
                        "message": f"Подписка обновлена. Обновлено ключей: {updated_keys}",
                        "subscription_id": subscription_id,
                        "error": "Subscription data not found",
                    })
                
                (
                    sub_id, user_id, token, created_at, expires_at, tariff_id,
                    is_active, last_updated_at, notified, tariff_name, keys_count, traffic_limit_mb
                ) = subscription_updated
                
                logger.info(f"Building response: traffic_limit_mb from DB = {traffic_limit_mb}")
                
                # Пересчитываем трафик подписки
                traffic_usage_bytes = subscription_repo.get_subscription_traffic_sum(sub_id)
                traffic_limit_bytes = subscription_repo.get_subscription_traffic_limit(sub_id)
                
                traffic_display = _format_bytes(traffic_usage_bytes) if traffic_usage_bytes is not None else "—"
                traffic_limit_display = _format_bytes(traffic_limit_bytes) if traffic_limit_bytes and traffic_limit_bytes > 0 else "—"
                
                usage_percent = None
                over_limit = False
                if traffic_usage_bytes is not None and traffic_limit_bytes and traffic_limit_bytes > 0:
                    try:
                        usage_percent = _clamp(traffic_usage_bytes / traffic_limit_bytes, 0.0, 1.0)
                        over_limit = traffic_usage_bytes > traffic_limit_bytes
                    except (TypeError, ValueError) as e:
                        logger.warning(f"Error calculating usage_percent: {e}")
                        usage_percent = None
                        over_limit = False
                
                traffic_info = {
                    "display": traffic_display,
                    "limit_display": traffic_limit_display,
                    "limit_mb": traffic_limit_mb if (traffic_limit_mb is not None and traffic_limit_mb > 0) else None,
                    "usage_percent": usage_percent,
                    "over_limit": over_limit,
                }
                
                expiry_info = _format_expiry_remaining(new_expiry, now_ts)
                is_expired = new_expiry <= now_ts
                
                # Вычисляем прогресс для прогресс-бара
                lifetime_total = max(new_expiry - created_at, 0) if new_expiry and created_at else 0
                elapsed = max(now_ts - created_at, 0) if created_at else 0
                lifetime_progress = 0.0
                if lifetime_total > 0:
                    lifetime_progress = max(0.0, min(1.0, elapsed / lifetime_total))
                elif new_expiry and now_ts >= new_expiry:
                    lifetime_progress = 1.0
                
                subscription_data = {
                    "id": sub_id,
                    "expires_at": _format_timestamp(new_expiry),
                    "expires_at_ts": new_expiry,
                    "expires_at_iso": datetime.fromtimestamp(new_expiry).strftime("%Y-%m-%dT%H:%M") if new_expiry else "",
                    "expiry_remaining": expiry_info["label"],
                    "expiry_state": expiry_info["state"],
                    "lifetime_progress": lifetime_progress,
                    "status": "Активна" if (is_active and not is_expired) else "Истекла",
                    "status_class": "status-active" if (is_active and not is_expired) else "status-expired",
                    "traffic": traffic_info,
                    "traffic_limit_mb": traffic_limit_mb if (traffic_limit_mb is not None and traffic_limit_mb > 0) else None,
                }
                
                logger.info(f"Returning subscription data: traffic_limit_mb = {subscription_data.get('traffic_limit_mb')}, traffic.limit_mb = {traffic_info.get('limit_mb')}")
                
                return JSONResponse({
                    "message": f"Подписка обновлена. Обновлено ключей: {updated_keys}",
                    "subscription_id": subscription_id,
                    "subscription": subscription_data,
                })
            except Exception as e:
                logger.error(f"Error building subscription response: {e}", exc_info=True)
                # Возвращаем хотя бы базовый ответ
                return JSONResponse({
                    "message": f"Подписка обновлена. Обновлено ключей: {updated_keys}",
                    "subscription_id": subscription_id,
                    "error": f"Error building response: {str(e)}",
                })
        
        return RedirectResponse(url="/subscriptions", status_code=303)
    
    except ValueError as e:
        logging.error(f"Error parsing expiry date: {e}")
        if _is_json_request(request):
            return JSONResponse({"error": "Invalid date format"}, status_code=400)
        return RedirectResponse(url="/subscriptions", status_code=303)
    except Exception as e:
        logging.error(f"Error editing subscription {subscription_id}: {e}", exc_info=True)
        if _is_json_request(request):
            return JSONResponse({"error": str(e)}, status_code=500)
        return RedirectResponse(url="/subscriptions", status_code=303)


async def _delete_subscription_internal(request: Request, subscription_id: int) -> Dict[str, Any]:
    """Внутренняя функция для удаления подписки"""
    subscription_repo = SubscriptionRepository(DB_PATH)
    subscription = subscription_repo.get_subscription_by_id(subscription_id)
    
    if not subscription:
        raise ValueError(f"Subscription {subscription_id} not found")
    
    logging.info(f"Deleting subscription {subscription_id}")
    
    # Получаем все ключи подписки для удаления
    subscription_keys = subscription_repo.get_subscription_keys_for_deletion(subscription_id)
    
    # Удаляем ключи через V2Ray API
    deleted_v2ray_count = 0
    for key_data in subscription_keys:
        if not isinstance(key_data, (tuple, list)) or len(key_data) < 3:
            logging.warning(f"Invalid key data structure: {key_data} for subscription {subscription_id}")
            continue
        
        v2ray_uuid, api_url, api_key = key_data[0], key_data[1], key_data[2]
        
        if v2ray_uuid and api_url and api_key:
            protocol_client = None
            try:
                log_admin_action(
                    request,
                    "V2RAY_DELETE_ATTEMPT",
                    f"Attempting to delete key {v2ray_uuid} from server {api_url} for subscription {subscription_id}"
                )
                protocol_client = V2RayProtocol(api_url, api_key)
                result = await protocol_client.delete_user(v2ray_uuid)
                if result:
                    deleted_v2ray_count += 1
                    log_admin_action(
                        request,
                        "V2RAY_DELETE_SUCCESS",
                        f"Successfully deleted key {v2ray_uuid} for subscription {subscription_id}"
                    )
                else:
                    log_admin_action(
                        request,
                        "V2RAY_DELETE_FAILED",
                        f"Failed to delete key {v2ray_uuid} for subscription {subscription_id}"
                    )
            except Exception as e:
                logging.error(f"Error deleting V2Ray key {v2ray_uuid}: {e}", exc_info=True)
                log_admin_action(
                    request,
                    "V2RAY_DELETE_ERROR",
                    f"Failed to delete key {v2ray_uuid} for subscription {subscription_id}: {str(e)}"
                )
            finally:
                if protocol_client:
                    try:
                        await protocol_client.close()
                    except Exception as close_error:
                        logging.warning(f"Error closing protocol client: {close_error}")
    
    # Удаляем ключи из БД
    deleted_keys_count = subscription_repo.delete_subscription_keys(subscription_id)
    
    # Инвалидируем кэш подписки перед удалением
    try:
        # subscription[2] - это subscription_token согласно структуре get_subscription_by_id
        # Структура: (id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified, tariff_name, keys_count, traffic_limit_mb)
        if len(subscription) > 2 and subscription[2]:
            token = subscription[2]
            from bot.services.subscription_service import invalidate_subscription_cache
            invalidate_subscription_cache(token)
            logging.info(f"Invalidated cache for subscription {subscription_id} with token {token[:8]}...")
    except (IndexError, TypeError) as e:
        logging.error(f"Error accessing subscription token for {subscription_id}: {e}, subscription: {subscription}", exc_info=True)
    except Exception as e:
        logging.error(f"Error invalidating subscription cache: {e}", exc_info=True)
        # Не прерываем выполнение, если не удалось инвалидировать кэш
    
    # Физически удаляем подписку из БД
    with open_connection(DB_PATH) as conn:
        c = conn.cursor()
        with safe_foreign_keys_off(c):
            c.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
        conn.commit()
    
    log_admin_action(
        request,
        "DELETE_SUBSCRIPTION",
        f"Subscription ID: {subscription_id}, deleted {deleted_keys_count} keys from DB, {deleted_v2ray_count} from servers, subscription removed from DB"
    )
    
    logging.info(f"Successfully deleted subscription {subscription_id} from database")
    return {
        "subscription_id": subscription_id,
        "deleted_keys_count": deleted_keys_count,
        "deleted_v2ray_count": deleted_v2ray_count,
    }


@router.get("/subscriptions/delete/{subscription_id}")
async def delete_subscription_route(request: Request, subscription_id: int):
    """Удаление подписки и всех связанных ключей (GET для обратной совместимости)"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    try:
        await _delete_subscription_internal(request, subscription_id)
    except ValueError as e:
        logging.warning(f"Subscription {subscription_id} not found for deletion: {e}")
        log_admin_action(request, "SUBSCRIPTION_DELETE_NOT_FOUND", f"Subscription {subscription_id} not found")
    except Exception as e:
        logging.error(f"Error deleting subscription {subscription_id}: {e}", exc_info=True)
        log_admin_action(request, "SUBSCRIPTION_DELETE_ERROR", f"Failed to delete subscription {subscription_id}: {str(e)}")
    
    return RedirectResponse("/subscriptions", status_code=303)


@router.delete("/api/subscriptions/{subscription_id}")
async def delete_subscription_api(request: Request, subscription_id: int):
    """API endpoint для удаления подписки"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        result = await _delete_subscription_internal(request, subscription_id)
        now_ts = int(time.time())
        stats = _compute_subscription_stats(DB_PATH, now_ts)
        return JSONResponse({
            "message": "Подписка удалена",
            "subscription_id": subscription_id,
            "stats": stats,
        })
    except ValueError:
        return JSONResponse({"error": "Subscription not found"}, status_code=404)
    except Exception as e:
        logging.error(f"Error deleting subscription {subscription_id}: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


def _is_json_request(request: Request) -> bool:
    """Проверить, является ли запрос JSON запросом"""
    accept = request.headers.get("accept", "")
    return "application/json" in accept or request.headers.get("content-type", "").startswith("application/json")


def _safe_header_value(value: str | None) -> str | None:
    """Возвращает значение заголовка, совместимое с ISO-8859-1.
    
    Если строка содержит не-ASCII символы, они будут отброшены. 
    Если после фильтрации строка пустая, возвращаем None.
    """
    if not value:
        return None
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        safe_value = value.encode("ascii", "ignore").decode("ascii").strip()
        return safe_value or None


@router.get("/api/subscription/{token}", response_class=PlainTextResponse)
@limiter.limit("60/minute")
async def get_subscription(request: Request, token: str):
    """
    Получить подписку V2Ray по токену
    
    Returns:
        Base64-кодированная строка с VLESS URL или ошибка
    """
    try:
        # Валидация токена
        if not validate_subscription_token(token):
            logger.warning(f"Invalid subscription token format: {token[:8]}...")
            raise HTTPException(status_code=400, detail="Invalid token format")

        # Инвалидируем кэш перед генерацией, чтобы всегда получать свежие данные
        # Это гарантирует, что названия серверов будут актуальными
        from bot.services.subscription_service import invalidate_subscription_cache, _subscription_cache
        invalidate_subscription_cache(token)
        # Также очищаем внутренний кэш для гарантии
        cache_key = f"subscription:{token}"
        _subscription_cache.delete(cache_key)

        # Генерация подписки
        service = SubscriptionService()
        package = await service.generate_subscription_package(token)
        content = package["content"] if package else None
        
        # Логируем для отладки
        if content:
            import base64
            from urllib.parse import unquote
            try:
                decoded = base64.b64decode(content).decode('utf-8')
                lines = decoded.split('\n')
                for line in lines[:2]:  # Проверяем первые 2 сервера
                    if '#' in line:
                        fragment = line.split('#')[-1]
                        decoded_name = unquote(fragment)
                        logger.info(f"Subscription {token[:8]}... contains server: {decoded_name}")
                    else:
                        logger.warning(f"Subscription {token[:8]}... server URL missing fragment!")
            except Exception as e:
                logger.error(f"Error checking subscription content: {e}")

        if content is None:
            logger.warning(f"Subscription not found or expired for token {token[:8]}...")
            raise HTTPException(status_code=404, detail="Subscription not found or expired")

        metadata = (package or {}).get("metadata", {}) if package else {}
        userinfo_header = None
        title_header = None
        if metadata:
            usage_bytes = metadata.get("traffic_usage_bytes") or 0
            limit_bytes = metadata.get("traffic_limit_bytes") or 0
            expires_ts = metadata.get("expires_at") or 0
            # Формат заголовка Subscription-Userinfo согласно спецификации v2ray
            # Стандартный формат: upload=<bytes>; download=<bytes>; total=<bytes>; expire=<timestamp>
            # Примечание: В приложении v2raytun отображение использованного трафика не работает (показывает 0),
            # хотя лимит трафика (total) отображается правильно. Это похоже на проблему/ограничение самого v2raytun.
            # В других приложениях (например, v2rayNG) все работает корректно.
            # Формат корректен по спецификации v2ray и оставлен как стандартный.
            userinfo_header = f"upload=0; download={usage_bytes}; total={limit_bytes}; expire={expires_ts}"
            # Устанавливаем Profile-Title с названием "Vee VPN", чтобы избежать использования названия сервера
            subscription_title = metadata.get("subscription_title") or "Vee VPN"
            # URL-кодируем название для безопасного использования в заголовке
            from urllib.parse import quote
            title_header = quote(subscription_title, safe='')

        response_headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
        if userinfo_header:
            response_headers["Subscription-Userinfo"] = userinfo_header
        if title_header:
            # Устанавливаем Profile-Title, чтобы приложение использовало его вместо названия сервера
            response_headers["Profile-Title"] = title_header

        return PlainTextResponse(
            content=content,
            headers=response_headers,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating subscription for token {token[:8]}...: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/subscriptions/sync-keys")
async def sync_keys_with_servers(request: Request):
    """Синхронизация всех ключей V2Ray с серверами"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        log_admin_action(request, "SYNC_KEYS_START", "Starting key synchronization with servers")
        
        # Запускаем синхронизацию
        result = await sync_all_keys_with_servers(dry_run=False, server_id=None)
        
        log_admin_action(
            request,
            "SYNC_KEYS_COMPLETE",
            f"Key synchronization completed: updated={result.get('updated', 0)}, "
            f"unchanged={result.get('unchanged', 0)}, failed={result.get('failed', 0)}"
        )
        
        return JSONResponse({
            "success": True,
            "message": "Синхронизация ключей завершена",
            "stats": {
                "total_keys": result.get("total_keys", 0),
                "updated": result.get("updated", 0),
                "unchanged": result.get("unchanged", 0),
                "failed": result.get("failed", 0),
                "servers_processed": result.get("servers_processed", 0),
            }
        })
    except ImportError as e:
        error_msg = f"Ошибка импорта модуля синхронизации: {str(e)}"
        logger.error(error_msg, exc_info=True)
        log_admin_action(request, "SYNC_KEYS_ERROR", error_msg)
        return JSONResponse({
            "success": False,
            "error": error_msg
        }, status_code=500)
    except Exception as e:
        error_msg = f"Ошибка синхронизации ключей: {str(e)}"
        logger.error(error_msg, exc_info=True)
        import traceback
        traceback_str = traceback.format_exc()
        logger.error(f"Traceback: {traceback_str}")
        log_admin_action(request, "SYNC_KEYS_ERROR", error_msg)
        return JSONResponse({
            "success": False,
            "error": error_msg,
            "details": str(e) if len(str(e)) < 200 else str(e)[:200] + "..."
        }, status_code=500)
