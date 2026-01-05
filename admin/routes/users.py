"""
Маршруты для управления пользователями
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.status import HTTP_303_SEE_OTHER
import sys
import os
import time

_root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _root_dir)
from app.repositories.user_repository import UserRepository
from app.repositories.key_repository import KeyRepository
from app.settings import settings
from app.infra.sqlite_utils import open_connection
from bot.core import get_bot_instance
from bot.utils.formatters import format_key_message_unified
from bot.utils.messaging import safe_send_message

# Lazy import: получаем bot instance только когда он нужен
def get_bot():
    """Получить экземпляр бота (lazy import для избежания ошибок при старте админки)"""
    try:
        return get_bot_instance()
    except RuntimeError:
        # Бот еще не запущен - возвращаем None
        return None

from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token
from ..dependencies.templates import templates

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH
DB_PATH = DATABASE_PATH


def _is_user_active(user_id: int, overview: dict) -> bool:
    """Проверяет, является ли пользователь активным (имеет ключи, связанные с активными подписками)"""
    import time
    now = int(time.time())
    with open_connection(DB_PATH) as conn:
        c = conn.cursor()
        # Проверяем, есть ли ключи с активными подписками
        c.execute("""
            SELECT COUNT(*) FROM (
                SELECT k.id
                FROM keys k
                JOIN subscriptions s ON k.subscription_id = s.id
                WHERE k.user_id = ? AND s.expires_at > ? AND s.is_active = 1
                UNION
                SELECT k.id
                FROM v2ray_keys k
                JOIN subscriptions s ON k.subscription_id = s.id
                WHERE k.user_id = ? AND s.expires_at > ? AND s.is_active = 1
            )
        """, (user_id, now, user_id, now))
        count = c.fetchone()[0]
        return count > 0


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, page: int = 1, limit: int = 50, q: str | None = None):
    """Страница списка пользователей"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    repo = UserRepository(DB_PATH)
    offset = (max(page, 1) - 1) * limit
    total = repo.count_users(query=q)
    rows = repo.list_users(query=q, limit=limit, offset=offset)
    user_list = []
    for row in rows:
        uid = row[0]
        ref_cnt = row[1] if len(row) > 1 else 0
        is_vip = bool(row[2] if len(row) > 2 else 0)
        ref_cnt = ref_cnt or 0
        overview = repo.get_user_overview(uid)
        last_activity = overview.get("last_activity") or None
        if last_activity == 0:
            last_activity = None
        user_list.append({
            "user_id": uid,
            "email": overview.get("email") or "",
            "referral_count": ref_cnt,
            "last_activity": last_activity,
            "is_active": _is_user_active(uid, overview),
            "is_vip": is_vip,
        })
    
    # Дополнительная статистика
    # Считаем активных пользователей для всей базы, а не только для текущей страницы
    active_users = repo.count_active_users() if not q else sum(1 for user in user_list if user["is_active"])
    # Считаем общее количество рефералов для всей базы, а не только для текущей страницы
    referral_count = repo.count_total_referrals() if not q else sum(user["referral_count"] for user in user_list)
    pages = (total + limit - 1) // limit
    
    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": user_list,
        "page": page,
        "limit": limit,
        "total": total,
        "active_users": active_users,
        "referral_count": referral_count,
        "pages": pages,
        "q": q or "",
    })


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: int, page: int = 1, limit: int = 50):
    """Детальная страница пользователя"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    repo = UserRepository(DB_PATH)
    overview = repo.get_user_overview(user_id)
    total = repo.count_user_keys(user_id)
    rows = repo.list_user_keys(user_id, limit=limit, offset=(max(page, 1) - 1) * limit)
    keys = []
    for k in rows:
        keys.append(list(k) + ["N/A"])  # traffic placeholder
    
    # Список платежей (последние 100)
    from payments.repositories.payment_repository import PaymentRepository
    from payments.models.payment import PaymentFilter
    pay_repo = PaymentRepository(DB_PATH)
    payments = await pay_repo.filter(
        PaymentFilter(user_id=user_id, limit=100, offset=0),
        sort_by="created_at",
        sort_order="DESC"
    )
    referrals = repo.list_referrals(user_id)
    
    now_ts = int(time.time())
    return templates.TemplateResponse("user_detail.html", {
        "request": request,
        "user": overview,
        "keys": keys,
        "payments": payments,
        "referrals": referrals,
        "page": page,
        "limit": limit,
        "total": total,
        "csrf_token": get_csrf_token(request),
        "current_time": now_ts,
    })


@router.post("/users/keys/{key_id}/resend")
async def resend_key(request: Request, key_id: int, csrf_token: str = Form(...)):
    """Повторная отправка ключа пользователю"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    # Проверка CSRF
    from ..dependencies.csrf import validate_csrf_token
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)
    
    user_id = None
    access_url = None
    protocol = None
    try:
        with open_connection(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, access_url FROM keys WHERE id = ?", (key_id,))
            row = c.fetchone()
            if row:
                user_id = row[0]
                access_url = row[1]
                protocol = 'outline'
            else:
                c.execute("SELECT user_id, v2ray_uuid, server_id, client_config FROM v2ray_keys WHERE id = ?", (key_id,))
                r = c.fetchone()
                if r:
                    user_id = r[0]
                    v2_uuid = r[1]
                    server_id = r[2]
                    stored_config = r[3] or ""
                    protocol = 'v2ray'
                    if stored_config:
                        access_url = stored_config.strip()
                    else:
                        c.execute("SELECT domain, COALESCE(v2ray_path,'/v2ray') FROM servers WHERE id = ?", (server_id,))
                        s = c.fetchone()
                        domain = (s[0] or '').strip() if s else ''
                        v2path = s[1] if s else '/v2ray'
                        if domain:
                            access_url = f"vless://{v2_uuid}@{domain}:443?path={v2path}&security=tls&type=ws#VeilBot-V2Ray"
                        else:
                            access_url = ""
        
        if user_id and access_url and protocol:
            try:
                bot = get_bot()
                if bot:
                    await safe_send_message(
                        bot,
                        user_id,
                        format_key_message_unified(access_url, protocol),
                        disable_web_page_preview=True,
                        parse_mode="Markdown"
                    )
                    log_admin_action(request, "RESEND_KEY", f"Key ID: {key_id}, User: {user_id}")
                    return JSONResponse({"success": True, "user_id": user_id})
                else:
                    return JSONResponse({"success": False, "error": "Bot not available"}, status_code=503)
            except Exception as e:
                return JSONResponse({"success": False, "error": str(e)}, status_code=500)
        return JSONResponse({"success": False, "error": "Key not found"}, status_code=404)
    finally:
        if user_id:
            try:
                return RedirectResponse(url=f"/users/{user_id}", status_code=303)
            except Exception:
                pass


@router.post("/users/keys/{key_id}/extend")
async def extend_key(
    request: Request,
    key_id: int,
    days: int = Form(30),
    csrf_token: str = Form(...)
):
    """Продление срока действия ключа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    # Проверка CSRF
    from ..dependencies.csrf import validate_csrf_token
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)
    
    kr = KeyRepository(DB_PATH)
    user_id = None
    try:
        now_ts = int(time.time())
        extend_sec = max(1, int(days)) * 86400
        
        with open_connection(DB_PATH) as conn:
            c = conn.cursor()
            # Проверяем Outline ключ
            # Получаем subscription_id для обновления подписки
            c.execute("SELECT user_id, subscription_id FROM keys WHERE id = ?", (key_id,))
            row = c.fetchone()
            if row:
                user_id = row[0]
                subscription_id = row[1]
                if subscription_id:
                    # Получаем текущий срок подписки
                    c.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (subscription_id,))
                    sub_row = c.fetchone()
                    old_expiry = sub_row[0] if sub_row and sub_row[0] else now_ts
                    new_expiry = max(now_ts, old_expiry) + extend_sec
                    # Обновляем подписку
                    from app.repositories.subscription_repository import SubscriptionRepository
                    sub_repo = SubscriptionRepository(DB_PATH)
                    sub_repo.extend_subscription(subscription_id, new_expiry)
                else:
                    raise ValueError(f"Key {key_id} does not have subscription_id")
                conn.commit()
                log_admin_action(request, "EXTEND_KEY", f"Outline Key ID: {key_id}, Days: {days}")
                return JSONResponse({"success": True, "new_expiry": new_expiry})
            else:
                # Проверяем V2Ray ключ
                c.execute("SELECT user_id, subscription_id FROM v2ray_keys WHERE id = ?", (key_id,))
                row = c.fetchone()
                if row:
                    user_id = row[0]
                    subscription_id = row[1]
                    if subscription_id:
                        # Получаем текущий срок подписки
                        c.execute("SELECT expires_at FROM subscriptions WHERE id = ?", (subscription_id,))
                        sub_row = c.fetchone()
                        old_expiry = sub_row[0] if sub_row and sub_row[0] else now_ts
                        new_expiry = max(now_ts, old_expiry) + extend_sec
                        # Обновляем подписку
                        from app.repositories.subscription_repository import SubscriptionRepository
                        sub_repo = SubscriptionRepository(DB_PATH)
                        sub_repo.extend_subscription(subscription_id, new_expiry)
                    else:
                        raise ValueError(f"V2Ray key {key_id} does not have subscription_id")
                    conn.commit()
                    log_admin_action(request, "EXTEND_KEY", f"V2Ray Key ID: {key_id}, Days: {days}")
                    return JSONResponse({"success": True, "new_expiry": new_expiry})
        
        return JSONResponse({"success": False, "error": "Key not found"}, status_code=404)
    except Exception as e:
        log_admin_action(request, "EXTEND_KEY_ERROR", f"Key ID: {key_id}, Error: {str(e)}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        if user_id:
            try:
                return RedirectResponse(url=f"/users/{user_id}", status_code=303)
            except Exception:
                pass


@router.post("/api/keys/{key_id}/expiry")
async def update_key_expiry(request: Request, key_id: int):
    """Обновление срока действия ключа через API"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    
    try:
        from fastapi import Body
        data = await request.json()
        expiry_timestamp = data.get("expiry_timestamp")
        
        if not expiry_timestamp:
            return JSONResponse({"error": "expiry_timestamp required"}, status_code=400)
        
        with open_connection(DB_PATH) as conn:
            c = conn.cursor()
            # Пытаемся обновить в таблице keys
            # Получаем subscription_id и обновляем подписку
            c.execute("SELECT subscription_id FROM keys WHERE id = ?", (key_id,))
            row = c.fetchone()
            if row and row[0]:
                from app.repositories.subscription_repository import SubscriptionRepository
                sub_repo = SubscriptionRepository(DB_PATH)
                sub_repo.extend_subscription(row[0], expiry_timestamp)
            else:
                # Пытаемся в таблице v2ray_keys
                c.execute("SELECT subscription_id FROM v2ray_keys WHERE id = ?", (key_id,))
                row = c.fetchone()
                if row and row[0]:
                    from app.repositories.subscription_repository import SubscriptionRepository
                    sub_repo = SubscriptionRepository(DB_PATH)
                    sub_repo.extend_subscription(row[0], expiry_timestamp)
                else:
                    raise ValueError(f"Key {key_id} not found or has no subscription_id")
            conn.commit()
        
        log_admin_action(request, "UPDATE_KEY_EXPIRY", f"Key ID: {key_id}, Expiry: {expiry_timestamp}")
        return JSONResponse({"success": True})
    except Exception as e:
        log_admin_action(request, "UPDATE_KEY_EXPIRY_ERROR", f"Key ID: {key_id}, Error: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=500)
