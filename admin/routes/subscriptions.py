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
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bot.services.subscription_service import SubscriptionService, validate_subscription_token
from app.repositories.subscription_repository import SubscriptionRepository
from app.settings import settings
from app.infra.sqlite_utils import open_connection
from vpn_protocols import V2RayProtocol
from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token
from ..dependencies.templates import templates

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


@router.get("/subscriptions")
async def subscriptions_page(request: Request, page: int = 1, limit: int = 50):
    """Страница списка подписок"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    log_admin_action(request, "SUBSCRIPTIONS_PAGE_ACCESS")
    
    subscription_repo = SubscriptionRepository(DB_PATH)
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
            is_active, last_updated_at, notified, tariff_name, keys_count
        ) = row
        
        # Обработка None значений
        created_at = created_at if created_at is not None else 0
        expires_at = expires_at if expires_at is not None else 0
        
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
                key_id, v2ray_uuid, email, key_created_at, key_expires_at,
                server_name, country, traffic_limit_mb, traffic_usage_bytes
            ) = key_row
            keys_info.append({
                "id": key_id,
                "uuid": v2ray_uuid[:8] + "..." if v2ray_uuid else "—",
                "email": email or "—",
                "server": server_name or "—",
                "country": country or "—",
                "created_at": _format_timestamp(key_created_at),
                "expires_at": _format_timestamp(key_expires_at),
            })
        
        subscription_models.append({
            "id": sub_id,
            "user_id": user_id,
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


@router.post("/subscriptions/edit/{subscription_id}")
async def edit_subscription_route(request: Request, subscription_id: int):
    """Редактирование срока действия подписки и всех связанных ключей"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    form = await request.form()
    new_expiry_str = form.get("new_expiry")
    
    if not new_expiry_str:
        if _is_json_request(request):
            return JSONResponse({"error": "new_expiry is required"}, status_code=400)
        return RedirectResponse(url="/subscriptions", status_code=303)
    
    try:
        # Парсим datetime-local format (YYYY-MM-DDTHH:mm) в timestamp
        dt = datetime.strptime(new_expiry_str, "%Y-%m-%dT%H:%M")
        new_expiry = int(dt.timestamp())
        
        subscription_repo = SubscriptionRepository(DB_PATH)
        subscription = subscription_repo.get_subscription_by_id(subscription_id)
        
        if not subscription:
            if _is_json_request(request):
                return JSONResponse({"error": "Subscription not found"}, status_code=404)
            return RedirectResponse(url="/subscriptions", status_code=303)
        
        # Обновляем срок подписки
        subscription_repo.extend_subscription(subscription_id, new_expiry)
        
        # Обновляем срок всех связанных ключей
        updated_keys = subscription_repo.update_subscription_keys_expiry(subscription_id, new_expiry)
        
        log_admin_action(
            request,
            "EDIT_SUBSCRIPTION",
            f"Subscription ID: {subscription_id}, new expiry: {new_expiry}, updated {updated_keys} keys"
        )
        
        if _is_json_request(request):
            # Возвращаем обновленные данные подписки для обновления UI
            now_ts = int(time.time())
            (
                sub_id, user_id, token, created_at, expires_at, tariff_id,
                is_active, last_updated_at, notified, tariff_name, keys_count
            ) = subscription_repo.get_subscription_by_id(subscription_id)
            
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
            }
            
            return JSONResponse({
                "message": f"Подписка обновлена. Обновлено ключей: {updated_keys}",
                "subscription_id": subscription_id,
                "subscription": subscription_data,
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


async def _delete_subscription_internal(request: Request, subscription_id: int):
    """Внутренняя функция для удаления подписки"""
    subscription_repo = SubscriptionRepository(DB_PATH)
    subscription = subscription_repo.get_subscription_by_id(subscription_id)
    
    if not subscription:
        raise ValueError(f"Subscription {subscription_id} not found")
    
    # Получаем все ключи подписки для удаления
    subscription_keys = subscription_repo.get_subscription_keys_for_deletion(subscription_id)
    
    # Удаляем ключи через V2Ray API
    deleted_v2ray_count = 0
    for v2ray_uuid, api_url, api_key in subscription_keys:
        if v2ray_uuid and api_url and api_key:
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
                await protocol_client.close()
            except Exception as e:
                logging.error(f"Error deleting V2Ray key {v2ray_uuid}: {e}", exc_info=True)
                log_admin_action(
                    request,
                    "V2RAY_DELETE_ERROR",
                    f"Failed to delete key {v2ray_uuid} for subscription {subscription_id}: {str(e)}"
                )
    
    # Удаляем ключи из БД
    deleted_keys_count = subscription_repo.delete_subscription_keys(subscription_id)
    
    # Деактивируем подписку
    subscription_repo.deactivate_subscription(subscription_id)
    
    # Инвалидируем кэш подписки
    token = subscription[2]
    from bot.services.subscription_service import invalidate_subscription_cache
    invalidate_subscription_cache(token)
    
    log_admin_action(
        request,
        "DELETE_SUBSCRIPTION",
        f"Subscription ID: {subscription_id}, deleted {deleted_keys_count} keys from DB, {deleted_v2ray_count} from servers"
    )
    
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
    except ValueError:
        logging.warning(f"Subscription {subscription_id} not found for deletion")
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
        
        # Вычисляем статистику для обновления UI
        subscription_repo = SubscriptionRepository(DB_PATH)
        total = subscription_repo.count_subscriptions()
        active_count = 0
        expired_count = 0
        
        # Простой подсчет активных/истекших (можно оптимизировать)
        rows = subscription_repo.list_subscriptions(limit=1000, offset=0)
        for row in rows:
            expires_at = row[4]
            is_active = row[6]
            is_expired = expires_at and expires_at <= now_ts
            if is_expired:
                expired_count += 1
            else:
                active_count += 1
        
        stats = {
            "total": total,
            "active": active_count,
            "expired": expired_count,
        }
        
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
        content = await service.generate_subscription_content(token)
        
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

        return PlainTextResponse(
            content=content,
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating subscription for token {token[:8]}...: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
