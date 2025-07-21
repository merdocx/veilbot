from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.templating import Jinja2Templates
from starlette.status import HTTP_303_SEE_OTHER
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel, validator
import os
import sys
import sqlite3
import time
import logging
import re
import secrets
from passlib.context import CryptContext
from dotenv import load_dotenv
from datetime import datetime
import json

# Load environment variables
load_dotenv()

# Add parent directory to Python path to import outline module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from outline import delete_key

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Configuration
DATABASE_PATH = os.path.join(os.path.dirname(__file__), "../vpn.db")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key")

router = APIRouter()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

# CSRF token management
def generate_csrf_token():
    """Generate a secure CSRF token"""
    return secrets.token_urlsafe(32)

def validate_csrf_token(request: Request, token: str):
    """Validate CSRF token from session"""
    session_token = request.session.get("csrf_token")
    if not session_token or not token or session_token != token:
        return False
    return True

def get_csrf_token(request: Request):
    """Get or generate CSRF token for session"""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = generate_csrf_token()
    return request.session["csrf_token"]

# Input validation models
class ServerForm(BaseModel):
    name: str
    api_url: str
    cert_sha256: str
    max_keys: int
    
    @validator('name')
    def validate_name(cls, v):
        if len(v.strip()) < 1:
            raise ValueError('Name cannot be empty')
        return v.strip()
    
    @validator('api_url')
    def validate_api_url(cls, v):
        if not re.match(r'^https?://[^\s/$.?#].[^\s]*$', v):
            raise ValueError('Invalid URL format')
        return v.strip()
    
    @validator('cert_sha256')
    def validate_cert_sha256(cls, v):
        if not re.match(r'^[A-Fa-f0-9:]+$', v):
            raise ValueError('Invalid certificate SHA256 format')
        return v.strip()
    
    @validator('max_keys')
    def validate_max_keys(cls, v):
        if v < 1:
            raise ValueError('Max keys must be at least 1')
        return v

class TariffForm(BaseModel):
    name: str
    duration_sec: int
    price_rub: int
    
    @validator('name')
    def validate_name(cls, v):
        if len(v.strip()) < 1:
            raise ValueError('Name cannot be empty')
        return v.strip()
    
    @validator('duration_sec')
    def validate_duration(cls, v):
        if v < 1:
            raise ValueError('Duration must be at least 1 second')
        return v
    
    @validator('price_rub')
    def validate_price(cls, v):
        if v < 0:
            raise ValueError('Price cannot be negative')
        return v

# Audit logging
def log_admin_action(request: Request, action: str, details: str = ""):
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    logging.info(f"Admin Action - IP: {client_ip}, Action: {action}, Details: {details}, User-Agent: {user_agent}")

# Authentication helper
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# Фильтр для форматирования timestamp
def timestamp_filter(ts):
    try:
        ts = int(ts)
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
    except Exception:
        return "—"

def datetime_local(ts):
    try:
        ts = int(ts)
        return time.strftime('%Y-%m-%dT%H:%M', time.localtime(ts))
    except Exception:
        return ""
templates.env.filters["timestamp"] = timestamp_filter
templates.env.filters["my_datetime_local"] = datetime_local

DB_PATH = os.path.join(os.path.dirname(__file__), "../vpn.db")

@router.get("/")
async def root(request: Request):
    """Root route that redirects to login page"""
    return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)

@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@limiter.limit("5/minute")
@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    # Log login attempt
    log_admin_action(request, "LOGIN_ATTEMPT", f"Username: {username}")
    
    # Check if admin password is configured
    if not ADMIN_PASSWORD_HASH:
        return templates.TemplateResponse("login.html", {
            "request": request, 
            "error": "Admin password not configured. Please set ADMIN_PASSWORD_HASH in environment variables."
        })
    
    # Verify credentials
    if username == ADMIN_USERNAME and verify_password(password, ADMIN_PASSWORD_HASH):
        request.session["admin_logged_in"] = True
        log_admin_action(request, "LOGIN_SUCCESS", f"Username: {username}")
        return RedirectResponse(url="/dashboard", status_code=HTTP_303_SEE_OTHER)
    
    # Log failed login
    log_admin_action(request, "LOGIN_FAILED", f"Username: {username}")
    return templates.TemplateResponse("login.html", {
        "request": request, 
        "error": "Неверный логин или пароль"
    })

@router.get("/logout")
async def logout(request: Request):
    if request.session.get("admin_logged_in"):
        log_admin_action(request, "LOGOUT")
    request.session.clear()
    return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)

@router.get("/dashboard")
async def dashboard(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")

    log_admin_action(request, "DASHBOARD_ACCESS")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    now = int(time.time())
    c.execute("SELECT COUNT(*) FROM keys WHERE expiry_at > ?", (now,))
    active_keys = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM tariffs")
    tariff_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM servers")
    server_count = c.fetchone()[0]

    conn.close()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_keys": active_keys,
        "tariff_count": tariff_count,
        "server_count": server_count,
    })

@router.get("/tariffs")
async def tariffs_page(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, duration_sec, price_rub FROM tariffs ORDER BY price_rub ASC")
    tariffs = c.fetchall()
    conn.close()
    return templates.TemplateResponse("tariffs.html", {
        "request": request, 
        "tariffs": tariffs,
        "csrf_token": get_csrf_token(request)
    })

@router.post("/add_tariff")
async def add_tariff(request: Request, name: str = Form(...), duration_sec: int = Form(...), price_rub: int = Form(...), csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    # Validate CSRF token
    if not validate_csrf_token(request, csrf_token):
        log_admin_action(request, "CSRF_ATTACK", f"Invalid CSRF token for add_tariff")
        return templates.TemplateResponse("tariffs.html", {
            "request": request, 
            "error": "Invalid request. Please try again."
        })
    
    try:
        # Validate input
        tariff_data = TariffForm(name=name, duration_sec=duration_sec, price_rub=price_rub)
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO tariffs (name, duration_sec, traffic_limit_mb, price_rub) VALUES (?, ?, ?, ?)", 
                  (tariff_data.name, tariff_data.duration_sec, 0, tariff_data.price_rub))
        conn.commit()
        conn.close()
        
        log_admin_action(request, "ADD_TARIFF", f"Name: {tariff_data.name}, Duration: {tariff_data.duration_sec}s, Price: {tariff_data.price_rub}₽")
        
        return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)
    except ValueError as e:
        log_admin_action(request, "ADD_TARIFF_FAILED", f"Validation error: {str(e)}")
        return templates.TemplateResponse("tariffs.html", {
            "request": request, 
            "error": f"Validation error: {str(e)}"
        })
    except Exception as e:
        log_admin_action(request, "ADD_TARIFF_ERROR", f"Database error: {str(e)}")
        return templates.TemplateResponse("tariffs.html", {
            "request": request, 
            "error": "Database error occurred"
        })

@router.get("/delete_tariff/{tariff_id}")
async def delete_tariff(request: Request, tariff_id: int):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Get tariff info for logging
        c.execute("SELECT name FROM tariffs WHERE id = ?", (tariff_id,))
        tariff = c.fetchone()
        if tariff:
            log_admin_action(request, "DELETE_TARIFF", f"ID: {tariff_id}, Name: {tariff[0]}")
        
        c.execute("DELETE FROM tariffs WHERE id = ?", (tariff_id,))
        conn.commit()
        conn.close()
        
        return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)
    except Exception as e:
        log_admin_action(request, "DELETE_TARIFF_ERROR", f"ID: {tariff_id}, Error: {str(e)}")
        return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)

@router.get("/tariffs/edit/{tariff_id}")
async def edit_tariff_page(request: Request, tariff_id: int):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, duration_sec, price_rub FROM tariffs WHERE id = ?", (tariff_id,))
    tariff = c.fetchone()
    conn.close()
    
    if not tariff:
        return RedirectResponse(url="/tariffs")
    
    return templates.TemplateResponse("edit_tariff.html", {
        "request": request, 
        "tariff": {
            "id": tariff[0],
            "name": tariff[1],
            "duration_sec": tariff[2],
            "price_rub": tariff[3]
        }
    })

@router.post("/tariffs/edit/{tariff_id}")
async def edit_tariff(request: Request, tariff_id: int, name: str = Form(...), duration_sec: int = Form(...), price_rub: int = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE tariffs SET name = ?, duration_sec = ?, price_rub = ? WHERE id = ?", 
              (name, duration_sec, price_rub, tariff_id))
    conn.commit()
    conn.close()
    
    return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)

@router.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    with sqlite3.connect(DATABASE_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name, api_url, cert_sha256, max_keys, active, country FROM servers")
        servers = c.fetchall()
        # Get issued keys count for each server
        server_ids = [s[0] for s in servers]
        if server_ids:
            q_marks = ','.join(['?'] * len(server_ids))
            c.execute(f"SELECT server_id, COUNT(*) FROM keys GROUP BY server_id HAVING server_id IN ({q_marks})", server_ids)
            key_counts = dict(c.fetchall())
        else:
            key_counts = {}
        # Combine server info with key count
        servers_with_counts = [s + (key_counts.get(s[0], 0),) for s in servers]
    return templates.TemplateResponse("servers.html", {
        "request": request, 
        "servers": servers_with_counts,
        "csrf_token": get_csrf_token(request)
    })

@router.post("/add_server")
async def add_server(request: Request, name: str = Form(...), api_url: str = Form(...), cert_sha256: str = Form(...), max_keys: int = Form(...), country: str = Form(""), csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    # Validate CSRF token
    if not validate_csrf_token(request, csrf_token):
        log_admin_action(request, "CSRF_ATTACK", f"Invalid CSRF token for add_server")
        return templates.TemplateResponse("servers.html", {
            "request": request, 
            "error": "Invalid request. Please try again."
        })
    
    try:
        # Validate input
        server_data = ServerForm(name=name, api_url=api_url, cert_sha256=cert_sha256, max_keys=max_keys)
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO servers (name, api_url, cert_sha256, max_keys, country) VALUES (?, ?, ?, ?, ?)", 
                  (server_data.name, server_data.api_url, server_data.cert_sha256, server_data.max_keys, country))
        conn.commit()
        conn.close()
        
        log_admin_action(request, "ADD_SERVER", f"Name: {server_data.name}, URL: {server_data.api_url}, Country: {country}")
        
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except ValueError as e:
        log_admin_action(request, "ADD_SERVER_FAILED", f"Validation error: {str(e)}")
        return templates.TemplateResponse("servers.html", {
            "request": request, 
            "error": f"Validation error: {str(e)}"
        })
    except Exception as e:
        log_admin_action(request, "ADD_SERVER_ERROR", f"Database error: {str(e)}")
        return templates.TemplateResponse("servers.html", {
            "request": request, 
            "error": "Database error occurred"
        })

@router.get("/delete_server/{server_id}", response_class=HTMLResponse)
async def delete_server(request: Request, server_id: int):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Get server info for logging
        c.execute("SELECT name FROM servers WHERE id = ?", (server_id,))
        server = c.fetchone()
        if server:
            log_admin_action(request, "DELETE_SERVER", f"ID: {server_id}, Name: {server[0]}")
        
        c.execute("DELETE FROM servers WHERE id = ?", (server_id,))
        conn.commit()
        conn.close()
        
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except Exception as e:
        log_admin_action(request, "DELETE_SERVER_ERROR", f"ID: {server_id}, Error: {str(e)}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)

@router.get("/servers/edit/{server_id}")
async def edit_server_page(request: Request, server_id: int):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    with sqlite3.connect(DATABASE_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name, api_url, cert_sha256, max_keys, active, country FROM servers WHERE id = ?", (server_id,))
        server = c.fetchone()
    
    if not server:
        return RedirectResponse(url="/servers")
    
    return templates.TemplateResponse("edit_server.html", {
        "request": request, 
        "server": {
            "id": server[0],
            "name": server[1],
            "api_url": server[2],
            "cert_sha256": server[3],
            "max_keys": server[4],
            "active": server[5],
            "country": server[6]
        }
    })

@router.post("/servers/edit/{server_id}")
async def edit_server(request: Request, server_id: int):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    form = await request.form()
    name = form.get("name")
    api_url = form.get("api_url")
    cert_sha256 = form.get("cert_sha256")
    max_keys = form.get("max_keys")
    active = 1 if form.get("active") in ("on", "1", 1, True) else 0
    country = form.get("country")
    debug_msg = f"[DEBUG] Received country value: {country}\n"
    print(debug_msg)
    with open("/root/veilbot/country_debug.log", "a") as f:
        f.write(debug_msg)
    with sqlite3.connect(DATABASE_PATH) as conn:
        c = conn.cursor()
        debug_msg2 = f"[DEBUG] Saving country value: {country} for server_id: {server_id}\n"
        print(debug_msg2)
        with open("/root/veilbot/country_debug.log", "a") as f:
            f.write(debug_msg2)
        c.execute("UPDATE servers SET name = ?, api_url = ?, cert_sha256 = ?, max_keys = ?, active = ?, country = ? WHERE id = ?",
                  (name, api_url, cert_sha256, max_keys, active, country, server_id))
        conn.commit()
    
    return RedirectResponse(url="/servers", status_code=303)

@router.get("/keys")
async def keys_page(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")

    # Debug logging
    log_admin_action(request, "KEYS_PAGE_ACCESS", f"DB_PATH: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Показываем все ключи, привязанные к серверам, включая email и тариф
    c.execute("""
        SELECT k.id, k.key_id, k.access_url, k.created_at, k.expiry_at, s.name, k.email, t.name as tariff_name
        FROM keys k
        JOIN servers s ON k.server_id = s.id
        LEFT JOIN tariffs t ON k.tariff_id = t.id
        ORDER BY k.created_at DESC
    """)
    keys = c.fetchall()
    
    # Debug logging
    keys_with_email = [k for k in keys if k[6]]
    log_admin_action(request, "KEYS_QUERY_RESULT", f"Total keys: {len(keys)}, Keys with email: {len(keys_with_email)}")
    
    conn.close()

    return templates.TemplateResponse("keys.html", {
        "request": request, 
        "keys": keys,
        "current_time": int(time.time())
    })

@router.get("/keys/delete/{key_id}")
async def delete_key_route(request: Request, key_id: int):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Получаем user_id удаляемого ключа
    c.execute("SELECT user_id FROM keys WHERE id = ?", (key_id,))
    row = c.fetchone()
    user_id = row[0] if row else None
    # Удаляем ключ
    c.execute("DELETE FROM keys WHERE id = ?", (key_id,))
    # Проверяем, остались ли у пользователя ещё активные ключи
    if user_id:
        c.execute("SELECT COUNT(*) FROM keys WHERE user_id = ?", (user_id,))
        count = c.fetchone()[0]
        if count == 0:
            c.execute("UPDATE payments SET revoked = 1 WHERE user_id = ? AND status = 'paid'", (user_id,))
    conn.commit()
    conn.close()

    return RedirectResponse("/keys", status_code=303)

@router.get("/cleanup")
async def cleanup_page(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    return templates.TemplateResponse("cleanup.html", {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "expired_count": 0,
        "deleted_from_outline": 0,
        "deleted_count": 0,
        "errors": []
    })

@router.post("/cleanup")
async def cleanup(request: Request, csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    # Validate CSRF token
    if not validate_csrf_token(request, csrf_token):
        log_admin_action(request, "CSRF_ATTACK", f"Invalid CSRF token for cleanup")
        return templates.TemplateResponse("cleanup.html", {
            "request": request, 
            "error": "Invalid request. Please try again."
        })
    
    log_admin_action(request, "CLEANUP_STARTED")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        now = int(time.time())
        
        # Get expired keys
        c.execute("SELECT id, key_id, server_id FROM keys WHERE expiry_at <= ?", (now,))
        expired_keys = c.fetchall()
        
        deleted_count = 0
        deleted_from_outline = 0
        errors = []
        
        for key_id, outline_key_id, server_id in expired_keys:
            try:
                # Delete from Outline server if key exists
                if outline_key_id and server_id:
                    c.execute("SELECT api_url, cert_sha256 FROM servers WHERE id = ?", (server_id,))
                    server = c.fetchone()
                    if server:
                        try:
                            delete_key(server[0], server[1], outline_key_id)
                            deleted_from_outline += 1
                        except Exception as e:
                            errors.append(f"Failed to delete key {outline_key_id} from Outline: {str(e)}")
                
                # Delete from database
                c.execute("DELETE FROM keys WHERE id = ?", (key_id,))
                deleted_count += 1
                
            except Exception as e:
                errors.append(f"Failed to delete key {key_id}: {str(e)}")
        
        conn.commit()
        conn.close()
        
        log_admin_action(request, "CLEANUP_COMPLETED", f"Deleted: {deleted_count}, Outline deleted: {deleted_from_outline}, Errors: {len(errors)}")
        
        return templates.TemplateResponse("cleanup.html", {
            "request": request,
            "csrf_token": get_csrf_token(request),
            "expired_count": len(expired_keys),
            "deleted_from_outline": deleted_from_outline,
            "deleted_count": deleted_count,
            "errors": errors
        })
        
    except Exception as e:
        log_admin_action(request, "CLEANUP_ERROR", f"Error: {str(e)}")
        return templates.TemplateResponse("cleanup.html", {
            "request": request,
            "csrf_token": get_csrf_token(request),
            "error": f"Cleanup failed: {str(e)}"
        })

@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    with sqlite3.connect(DATABASE_PATH) as conn:
        c = conn.cursor()
        # Получаем всех пользователей, которые когда-либо получали ключ
        c.execute("SELECT DISTINCT user_id FROM keys ORDER BY user_id")
        users = c.fetchall()
        user_list = []
        for (user_id,) in users:
            # Считаем число рефералов для каждого пользователя
            c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
            referral_count = c.fetchone()[0]
            user_list.append({"user_id": user_id, "referral_count": referral_count})
    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": user_list
    })

@router.post("/api/keys/{key_id}/expiry")
async def api_update_key_expiry(request: Request, key_id: int):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    data = await request.json()
    expiry_str = data.get("expiry_at")
    try:
        dt = datetime.strptime(str(expiry_str), '%Y-%m-%dT%H:%M')
        new_expiry_ts = int(dt.timestamp())
    except Exception:
        return JSONResponse({"error": "Некорректный формат даты"}, status_code=400)
    with sqlite3.connect(DATABASE_PATH) as conn:
        c = conn.cursor()
        c.execute("UPDATE keys SET expiry_at = ? WHERE id = ?", (new_expiry_ts, key_id))
        conn.commit()
    return JSONResponse({"success": True})

@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    body = await request.body()
    try:
        data = json.loads(body)
        payment_id = data["object"]["id"]
        status_ = data["object"].get("status")
        if status_ == "succeeded":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE payments SET status = 'paid' WHERE payment_id = ?", (payment_id,))
            conn.commit()
            conn.close()
            logging.info(f"[YOOKASSA_WEBHOOK] Payment {payment_id} marked as paid via webhook.")
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logging.error(f"[YOOKASSA_WEBHOOK] Error: {e}")
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)



