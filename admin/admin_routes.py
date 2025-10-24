from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
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
from datetime import datetime
import json
from payments.config import get_webhook_service, get_payment_service
from payments.repositories.payment_repository import PaymentRepository
from payments.models.payment import PaymentFilter
from payments.models.enums import PaymentStatus, PaymentProvider
from bot import bot, format_key_message_unified
from app.repositories.tariff_repository import TariffRepository
from app.repositories.server_repository import ServerRepository
from app.repositories.key_repository import KeyRepository
from app.repositories.user_repository import UserRepository
from app.repositories.tariff_repository import TariffRepository
from app.repositories.server_repository import ServerRepository
from app.repositories.key_repository import KeyRepository


# Add parent directory to Python path to import outline module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from outline import delete_key
from vpn_protocols import ProtocolFactory
import aiohttp
import ssl

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Configuration (unified)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.settings import settings

DATABASE_PATH = settings.DATABASE_PATH
ADMIN_USERNAME = settings.ADMIN_USERNAME
ADMIN_PASSWORD_HASH = settings.ADMIN_PASSWORD_HASH or ""
SECRET_KEY = settings.SECRET_KEY or "super-secret-key"

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
    cert_sha256: str = ""
    max_keys: int
    protocol: str = "outline"
    domain: str = ""
    api_key: str = ""
    v2ray_path: str = "/v2ray"
    
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
        if v and not re.match(r'^[A-Fa-f0-9:]+$', v):
            raise ValueError('Invalid certificate SHA256 format')
        return v.strip() if v else ""
    
    @validator('max_keys')
    def validate_max_keys(cls, v):
        if v < 1:
            raise ValueError('Max keys must be at least 1')
        return v
    
    @validator('protocol')
    def validate_protocol(cls, v):
        if v not in ['outline', 'v2ray']:
            raise ValueError('Protocol must be either outline or v2ray')
        return v
    
    @validator('domain')
    def validate_domain(cls, v):
        # Обработка случаев, когда v может быть "None" или None
        if v in [None, "None", ""]:
            return ""
        if not re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError('Invalid domain format')
        return v.strip()
    
    @validator('api_key')
    def validate_api_key(cls, v, values):
        # API key обязателен только для V2Ray серверов
        protocol = values.get('protocol', 'outline')
        if protocol == 'v2ray' and (not v or not v.strip() or v == "None"):
            raise ValueError("API key is required for V2Ray servers")
        # Обработка случаев, когда v может быть "None"
        if v in [None, "None", ""]:
            return ""
        return v.strip()
    
    @validator('v2ray_path')
    def validate_v2ray_path(cls, v):
        # Обработка случаев, когда v может быть "None" или None
        if v in [None, "None", ""]:
            return "/v2ray"
        if not v.startswith('/'):
            raise ValueError('V2Ray path must start with /')
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
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception as e:
        logging.error(f"Invalid admin password hash format: {e}")
        return False

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
                    for key in keys:
                        if key.get('key_uuid') == key_uuid:
                            monthly_traffic_data = key.get('monthly_traffic', {})
                            total_bytes = monthly_traffic_data.get('total_bytes', 0)
                            if total_bytes == 0:
                                return "0 GB"
                            traffic_gb = total_bytes / (1024 * 1024 * 1024)
                            return f"{traffic_gb:.2f} GB"
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
                
                # Find the specific key by UUID
                for key in keys:
                    if key.get('key_uuid') == key_uuid:
                        monthly_traffic_data = key.get('monthly_traffic', {})
                        total_bytes = monthly_traffic_data.get('total_bytes', 0)
                        
                        # Convert to GB and format
                        if total_bytes == 0:
                            return "0 GB"
                        
                        traffic_gb = total_bytes / (1024 * 1024 * 1024)
                        return f"{traffic_gb:.2f} GB"
                
                # Key not found in monthly data, fallback to total traffic
                traffic_history = await v2ray.get_traffic_history()
                
                if traffic_history and traffic_history.get('data'):
                    data = traffic_history.get('data', {})
                    keys = data.get('keys', [])
                    
                    # Find the specific key by UUID
                    for key in keys:
                        if key.get('key_uuid') == key_uuid:
                            total_traffic = key.get('total_traffic', {})
                            total_bytes = total_traffic.get('total_bytes', 0)
                            
                            if total_bytes == 0:
                                return "0 GB"
                            
                            traffic_gb = total_bytes / (1024 * 1024 * 1024)
                            return f"{traffic_gb:.2f} GB"
                
                # Key not found
                return "0 GB"
            else:
                # Fallback to total traffic if monthly data not available
                traffic_history = await v2ray.get_traffic_history()
                
                if traffic_history and traffic_history.get('data'):
                    data = traffic_history.get('data', {})
                    keys = data.get('keys', [])
                    
                    # Find the specific key by UUID
                    for key in keys:
                        if key.get('key_uuid') == key_uuid:
                            total_traffic = key.get('total_traffic', {})
                            total_bytes = total_traffic.get('total_bytes', 0)
                            
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
        logging.error(f"Error getting monthly traffic for key {key_uuid}: {e}")
        return "Error"
    finally:
        # Закрываем сессию V2Ray
        if v2ray:
            await v2ray.close()

def format_bytes(bytes_value):
    """Format bytes to human readable format"""
    if bytes_value == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    
    while bytes_value >= 1024 and unit_index < len(units) - 1:
        bytes_value /= 1024
        unit_index += 1
    
    return f"{bytes_value:.2f} {units[unit_index]}"
def format_duration_filter(seconds):
    """Фильтр для форматирования длительности в человекочитаемый вид с правильным склонением"""
    if seconds is None or seconds < 0:
        return "истек"
    
    if seconds < 60:
        return f"{int(seconds)} сек"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        return f"{minutes} мин"
    elif seconds < 86400:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        if minutes > 0:
            return f"{hours}ч {minutes}мин"
        else:
            return f"{hours}ч"
    else:
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int(((seconds % 86400) % 3600) // 60)
        
        # Если больше года, показываем годы, месяцы, дни, часы, минуты
        if days >= 365:
            years = days // 365
            remaining_days = days % 365
            months = remaining_days // 30
            remaining_days = remaining_days % 30
            
            # Правильное склонение для лет
            if years == 1:
                years_str = "год"
            elif years in [2, 3, 4]:
                years_str = "года"
            else:
                years_str = "лет"
            
            # Правильное склонение для месяцев
            if months == 1:
                months_str = "месяц"
            elif months in [2, 3, 4]:
                months_str = "месяца"
            else:
                months_str = "месяцев"
            
            # Правильное склонение для дней
            if remaining_days == 1:
                days_str = "день"
            elif remaining_days in [2, 3, 4]:
                days_str = "дня"
            else:
                days_str = "дней"
            
            # Правильное склонение для часов
            if hours == 1:
                hours_str = "час"
            elif hours in [2, 3, 4]:
                hours_str = "часа"
            else:
                hours_str = "часов"
            
            # Правильное склонение для минут
            if minutes == 1:
                minutes_str = "минута"
            elif minutes in [2, 3, 4]:
                minutes_str = "минуты"
            else:
                minutes_str = "минут"
            
            # Формируем результат
            result_parts = []
            if years > 0:
                result_parts.append(f"{years} {years_str}")
            if months > 0:
                result_parts.append(f"{months} {months_str}")
            if remaining_days > 0:
                result_parts.append(f"{remaining_days} {days_str}")
            if hours > 0:
                result_parts.append(f"{hours} {hours_str}")
            if minutes > 0:
                result_parts.append(f"{minutes} {minutes_str}")
            
            return ", ".join(result_parts)
        
        # Если больше месяца, показываем месяцы, дни, часы, минуты
        elif days >= 30:
            months = days // 30
            remaining_days = days % 30
            
            # Правильное склонение для месяцев
            if months == 1:
                months_str = "месяц"
            elif months in [2, 3, 4]:
                months_str = "месяца"
            else:
                months_str = "месяцев"
            
            # Правильное склонение для дней
            if remaining_days == 1:
                days_str = "день"
            elif remaining_days in [2, 3, 4]:
                days_str = "дня"
            else:
                days_str = "дней"
            
            # Правильное склонение для часов
            if hours == 1:
                hours_str = "час"
            elif hours in [2, 3, 4]:
                hours_str = "часа"
            else:
                hours_str = "часов"
            
            # Правильное склонение для минут
            if minutes == 1:
                minutes_str = "минута"
            elif minutes in [2, 3, 4]:
                minutes_str = "минуты"
            else:
                minutes_str = "минут"
            
            # Формируем результат
            result_parts = []
            if months > 0:
                result_parts.append(f"{months} {months_str}")
            if remaining_days > 0:
                result_parts.append(f"{remaining_days} {days_str}")
            if hours > 0:
                result_parts.append(f"{hours} {hours_str}")
            if minutes > 0:
                result_parts.append(f"{minutes} {minutes_str}")
            
            return ", ".join(result_parts)
        
        # Если больше недели, показываем недели, дни, часы, минуты
        elif days >= 7:
            weeks = days // 7
            remaining_days = days % 7
            
            # Правильное склонение для недель
            if weeks == 1:
                weeks_str = "неделя"
            elif weeks in [2, 3, 4]:
                weeks_str = "недели"
            else:
                weeks_str = "недель"
            
            # Правильное склонение для дней
            if remaining_days == 1:
                days_str = "день"
            elif remaining_days in [2, 3, 4]:
                days_str = "дня"
            else:
                days_str = "дней"
            
            # Правильное склонение для часов
            if hours == 1:
                hours_str = "час"
            elif hours in [2, 3, 4]:
                hours_str = "часа"
            else:
                hours_str = "часов"
            
            # Правильное склонение для минут
            if minutes == 1:
                minutes_str = "минута"
            elif minutes in [2, 3, 4]:
                minutes_str = "минуты"
            else:
                minutes_str = "минут"
            
            # Формируем результат
            result_parts = []
            if weeks > 0:
                result_parts.append(f"{weeks} {weeks_str}")
            if remaining_days > 0:
                result_parts.append(f"{remaining_days} {days_str}")
            if hours > 0:
                result_parts.append(f"{hours} {hours_str}")
            if minutes > 0:
                result_parts.append(f"{minutes} {minutes_str}")
            
            return ", ".join(result_parts)
        
        # Обычные дни - показываем дни, часы, минуты
        else:
            # Правильное склонение для дней
            if days == 1:
                days_str = "день"
            elif days in [2, 3, 4]:
                days_str = "дня"
            else:
                days_str = "дней"
            
            # Правильное склонение для часов
            if hours == 1:
                hours_str = "час"
            elif hours in [2, 3, 4]:
                hours_str = "часа"
            else:
                hours_str = "часов"
            
            # Правильное склонение для минут
            if minutes == 1:
                minutes_str = "минута"
            elif minutes in [2, 3, 4]:
                minutes_str = "минуты"
            else:
                minutes_str = "минут"
            
            # Формируем результат
            result_parts = []
            if days > 0:
                result_parts.append(f"{days} {days_str}")
            if hours > 0:
                result_parts.append(f"{hours} {hours_str}")
            if minutes > 0:
                result_parts.append(f"{minutes} {minutes_str}")
            
            return ", ".join(result_parts)

templates.env.filters["timestamp"] = timestamp_filter
templates.env.filters["my_datetime_local"] = datetime_local
templates.env.filters["format_duration"] = format_duration_filter

# Helper to format amounts (kopecks to RUB with 2 decimals)
def format_rub(amount_kopecks: int) -> str:
    try:
        rub = (int(amount_kopecks) or 0) / 100.0
        return f"{rub:,.2f} ₽".replace(",", " ")
    except Exception:
        return "0.00 ₽"
templates.env.filters["rub"] = format_rub

# Status text filter (handles enum or plain string)
def status_text(value):
    try:
        if hasattr(value, "value"):
            return value.value
        return str(value or "")
    except Exception:
        return ""
templates.env.filters["status_text"] = status_text

DB_PATH = DATABASE_PATH

def _mask_sensitive(obj):
    try:
        if isinstance(obj, dict):
            masked = {}
            for k, v in obj.items():
                kl = str(k).lower()
                if kl in {"email", "phone", "card", "pan", "number"}:
                    masked[k] = "***"
                else:
                    masked[k] = _mask_sensitive(v)
            return masked
        if isinstance(obj, list):
            return [_mask_sensitive(v) for v in obj]
        return obj
    except Exception:
        return obj

def pretty_json(value: str) -> str:
    try:
        data = json.loads(value)
        data = _mask_sensitive(data)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return value
templates.env.filters["pretty_json"] = pretty_json

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

    # Try to get cached dashboard data
    from app.infra.cache import traffic_cache
    cache_key = "dashboard_stats"
    cached_stats = traffic_cache.get(cache_key)
    
    if cached_stats:
        active_keys, tariff_count, server_count = cached_stats
    else:
        # Calculate fresh stats
        now = int(time.time())
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM keys WHERE expiry_at > ?", (now,))
        outline_keys = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM v2ray_keys WHERE expiry_at > ?", (now,))
        v2ray_keys = c.fetchone()[0]
        active_keys = outline_keys + v2ray_keys
        c.execute("SELECT COUNT(*) FROM tariffs")
        tariff_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM servers")
        server_count = c.fetchone()[0]
        
        # Cache for 60 seconds
        traffic_cache.set(cache_key, (active_keys, tariff_count, server_count), ttl=60)

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
    tariffs = TariffRepository(DB_PATH).list_tariffs()
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
        
        TariffRepository(DB_PATH).add_tariff(tariff_data.name, tariff_data.duration_sec, tariff_data.price_rub)
        
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
        repo = TariffRepository(DB_PATH)
        t = repo.get_tariff(tariff_id)
        if t:
            log_admin_action(request, "DELETE_TARIFF", f"ID: {tariff_id}, Name: {t[1]}")
        repo.delete_tariff(tariff_id)
        return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)
    except Exception as e:
        log_admin_action(request, "DELETE_TARIFF_ERROR", f"ID: {tariff_id}, Error: {str(e)}")
        return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)

@router.get("/tariffs/edit/{tariff_id}")
async def edit_tariff_page(request: Request, tariff_id: int):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    tariff = TariffRepository(DB_PATH).get_tariff(tariff_id)
    
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
    
    TariffRepository(DB_PATH).update_tariff(tariff_id, name, duration_sec, price_rub)
    
    return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)

@router.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request):
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
    
    return templates.TemplateResponse("servers.html", {
        "request": request, 
        "servers": servers_with_counts,
        "csrf_token": get_csrf_token(request)
    })

@router.post("/add_server")
async def add_server(request: Request, name: str = Form(...), api_url: str = Form(...), cert_sha256: str = Form(""), max_keys: int = Form(...), country: str = Form(""), protocol: str = Form("outline"), domain: str = Form(""), api_key: str = Form(""), v2ray_path: str = Form("/v2ray"), csrf_token: str = Form(...)):
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
        
        ServerRepository(DATABASE_PATH).add_server(
            name=server_data.name,
            api_url=server_data.api_url,
            cert_sha256=server_data.cert_sha256,
            max_keys=server_data.max_keys,
            country=country,
            protocol=server_data.protocol,
            domain=server_data.domain,
            api_key=server_data.api_key,
            v2ray_path=server_data.v2ray_path,
        )
        
        log_admin_action(request, "ADD_SERVER", f"Name: {server_data.name}, URL: {server_data.api_url}, Protocol: {server_data.protocol}, Country: {country}")
        
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
        repo = ServerRepository(DATABASE_PATH)
        server = repo.get_server(server_id)
        if server:
            log_admin_action(request, "DELETE_SERVER", f"ID: {server_id}, Name: {server[1]}")
        repo.delete_server(server_id)
        
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)
    except Exception as e:
        log_admin_action(request, "DELETE_SERVER_ERROR", f"ID: {server_id}, Error: {str(e)}")
        return RedirectResponse(url="/servers", status_code=HTTP_303_SEE_OTHER)

@router.get("/servers/edit/{server_id}")
async def edit_server_page(request: Request, server_id: int):
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
            "cert_sha256": server[3],
            "max_keys": server[4],
            "active": server[5],
            "country": server[6],
            "protocol": server[7] if len(server) > 7 else "outline",
            "domain": server[8] if len(server) > 8 else "",
            "api_key": server[9] if len(server) > 9 else "",
            "v2ray_path": server[10] if len(server) > 10 else "/v2ray"
        },
        "csrf_token": get_csrf_token(request)
    })

@router.post("/servers/edit/{server_id}")
async def edit_server(request: Request, server_id: int):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    form = await request.form()
    name = form.get("name")
    api_url = form.get("api_url")
    cert_sha256 = form.get("cert_sha256", "")
    max_keys = form.get("max_keys")
    active = 1 if form.get("active") in ("on", "1", 1, True) else 0
    country = form.get("country", "")
    protocol = form.get("protocol", "outline")
    domain = form.get("domain", "")
    api_key = form.get("api_key", "")
    v2ray_path = form.get("v2ray_path", "/v2ray")
    
    # Обработка случаев, когда поля приходят как строки "None"
    if domain == "None":
        domain = ""
    if api_key == "None":
        api_key = ""
    if v2ray_path == "None":
        v2ray_path = "/v2ray"
    
    try:
        # Validate input
        server_data = ServerForm(
            name=name, 
            api_url=api_url, 
            cert_sha256=cert_sha256, 
            max_keys=int(max_keys),
            protocol=protocol,
            domain=domain,
            api_key=api_key,
            v2ray_path=v2ray_path
        )
        
        ServerRepository(DATABASE_PATH).update_server(
            server_id=server_id,
            name=server_data.name,
            api_url=server_data.api_url,
            cert_sha256=server_data.cert_sha256,
            max_keys=server_data.max_keys,
            active=active,
            country=country,
            protocol=server_data.protocol,
            domain=server_data.domain,
            api_key=server_data.api_key,
            v2ray_path=server_data.v2ray_path,
        )
        
        log_admin_action(request, "EDIT_SERVER", f"ID: {server_id}, Name: {server_data.name}, Protocol: {server_data.protocol}")
        
    except ValueError as e:
        log_admin_action(request, "EDIT_SERVER_FAILED", f"ID: {server_id}, Validation error: {str(e)}")
    except Exception as e:
        log_admin_action(request, "EDIT_SERVER_ERROR", f"ID: {server_id}, Database error: {str(e)}")
    
    return RedirectResponse(url="/servers", status_code=303)

@router.get("/keys")
async def keys_page(request: Request, page: int = 1, limit: int = 50, email: str | None = None, tariff_id: int | None = None, protocol: str | None = None, server_id: int | None = None, sort_by: str | None = None, sort_order: str | None = None, export: str | None = None, cursor: str | None = None):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")

    # Debug logging
    log_admin_action(request, "KEYS_PAGE_ACCESS", f"DB_PATH: {DB_PATH}")

    key_repo = KeyRepository(DB_PATH)
    total = key_repo.count_keys_unified(email=email, tariff_id=tariff_id, protocol=protocol, server_id=server_id)
    # Force sorting by purchase date (created_at) descending by default
    sort_by_eff = 'created_at'
    sort_order_eff = 'DESC'
    rows = key_repo.list_keys_unified(
        email=email,
        tariff_id=tariff_id,
        protocol=protocol,
        server_id=server_id,
        sort_by=sort_by_eff,
        sort_order=sort_order_eff,
        limit=limit,
        offset=(page-1)*limit if not cursor else 0,
        cursor=cursor,
    )
    
    # Получаем данные о трафике для V2Ray ключей
    keys_with_traffic = []
    for key in rows:
        if len(key) > 8 and key[8] == 'v2ray':
            try:
                # Use api_url and api_key from columns (added in repository)
                # Columns layout: [..., protocol(8), traffic_limit(9), api_url(10), api_key(11)]
                api_url = key[10] if len(key) > 10 else ''
                api_key = key[11] if len(key) > 11 else ''
                server_config = {'api_url': api_url or '', 'api_key': api_key or ''}
                # Get server_id from key data (assuming it's in position 5 or we need to fetch it)
                server_id = None
                if len(key) > 11:  # Check if we have server_id in the key data
                    # We need to get server_id from the database
                    with sqlite3.connect(DB_PATH) as conn:
                        c = conn.cursor()
                        c.execute("SELECT server_id FROM v2ray_keys WHERE id = ?", (key[0],))
                        result = c.fetchone()
                        if result:
                            server_id = result[0]
                
                monthly_traffic = await get_key_monthly_traffic(key[1], 'v2ray', server_config, server_id)
                key_with_traffic = list(key) + [monthly_traffic]
                keys_with_traffic.append(key_with_traffic)
            except Exception as e:
                logging.error(f"Error getting traffic for V2Ray key {key[1]}: {e}")
                key_with_traffic = list(key) + ["Error"]
                keys_with_traffic.append(key_with_traffic)
        else:
            key_with_traffic = list(key) + ["N/A"]
            keys_with_traffic.append(key_with_traffic)
    
    log_admin_action(request, "KEYS_QUERY_RESULT", f"Total keys: {len(keys_with_traffic)}")

    # CSV Export
    if export and str(export).lower() in ("csv", "true", "1"):
        try:
            import csv
            from io import StringIO
            now_ts = int(time.time())
            buffer = StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["id","protocol","key","tariff","email","server","created_at","expiry_at","status","traffic"])
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
        "email": email or '',
        "user_id": '',
        "server": '',
        "protocol": protocol or '',
        "filters": {"email": email or '', "tariff_id": tariff_id or '', "protocol": protocol or '', "server_id": server_id or ''},
        "sort": {"by": sort_by_eff, "order": sort_order_eff},
        "csrf_token": get_csrf_token(request),
    })

@router.get("/keys/delete/{key_id}")
async def delete_key_route(request: Request, key_id: int):
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
                        log_admin_action(request, "OUTLINE_DELETE_FAILED", f"Failed to delete key {outline_key_id} from server - function returned False")
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
                        from vpn_protocols import V2RayProtocol
                        log_admin_action(request, "V2RAY_DELETE_ATTEMPT", f"Attempting to delete user {v2ray_uuid} from server {server[0]}")
                        protocol_client = V2RayProtocol(server[0], server[1])
                        result = await protocol_client.delete_user(v2ray_uuid)
                        if result:
                            log_admin_action(request, "V2RAY_DELETE_SUCCESS", f"Successfully deleted user {v2ray_uuid} from server")
                        else:
                            log_admin_action(request, "V2RAY_DELETE_FAILED", f"Failed to delete user {v2ray_uuid} from server - function returned False")
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
        return RedirectResponse("/keys", status_code=303)

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
        key_repo = KeyRepository(DB_PATH)
        now = int(time.time())
        expired_outline_keys = key_repo.get_expired_outline_keys(now)
        expired_v2ray_keys = key_repo.get_expired_v2ray_keys(now)
        
        deleted_count = 0
        deleted_from_outline = 0
        deleted_from_v2ray = 0
        errors = []
        
        # Process expired Outline keys
        for key_id, outline_key_id, server_id in expired_outline_keys:
            try:
                if outline_key_id and server_id:
                    with sqlite3.connect(DB_PATH) as conn:
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
        
        # Process expired V2Ray keys
        for key_id, v2ray_uuid, server_id in expired_v2ray_keys:
            try:
                if v2ray_uuid and server_id:
                    with sqlite3.connect(DB_PATH) as conn:
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
        
        conn.commit()
        conn.close()
        
        log_admin_action(request, "CLEANUP_COMPLETED", f"Deleted: {deleted_count}, Outline deleted: {deleted_from_outline}, V2Ray deleted: {deleted_from_v2ray}, Errors: {len(errors)}")
        
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
            "error": f"Cleanup failed: {str(e)}"
        })

@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, page: int = 1, limit: int = 50, q: str | None = None):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    repo = UserRepository(DATABASE_PATH)
    offset = (max(page, 1) - 1) * limit
    total = repo.count_users(query=q)
    rows = repo.list_users(query=q, limit=limit, offset=offset)
    user_list = [{"user_id": uid, "referral_count": ref_cnt} for (uid, ref_cnt) in rows]
    # Calculate additional stats
    active_users = total  # Assuming all users are active for now
    referral_count = sum(user["referral_count"] for user in user_list)
    pages = (total + limit - 1) // limit
    
    # Add mock data for missing fields
    for user in user_list:
        user["last_activity"] = None
        user["is_active"] = True
    
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
async def user_detail_page(request: Request, user_id: int, page: int = 1, limit: int = 50):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    repo = UserRepository(DATABASE_PATH)
    overview = repo.get_user_overview(user_id)
    total = repo.count_user_keys(user_id)
    rows = repo.list_user_keys(user_id, limit=limit, offset=(max(page,1)-1)*limit)
    keys = []
    for k in rows:
        keys.append(list(k) + ["N/A"])  # traffic placeholder
    # Payments list (latest 100)
    pay_repo = PaymentRepository(DB_PATH)
    from payments.models.payment import PaymentFilter
    payments = await pay_repo.filter(PaymentFilter(user_id=user_id, limit=100, offset=0), sort_by="created_at", sort_order="DESC")
    return templates.TemplateResponse("user_detail.html", {
        "request": request,
        "user": overview,
        "keys": keys,
        "payments": payments,
        "page": page,
        "limit": limit,
        "total": total,
        "csrf_token": get_csrf_token(request),
        "current_time": int(time.time()),
    })

@router.post("/users/keys/{key_id}/resend")
async def resend_key_message(request: Request, key_id: int, csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    user_id = None
    access_url = None
    protocol = None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, access_url FROM keys WHERE id = ?", (key_id,))
            row = c.fetchone()
            if row:
                user_id = row[0]
                access_url = row[1]
                protocol = 'outline'
            else:
                c.execute("SELECT user_id, v2ray_uuid, server_id FROM v2ray_keys WHERE id = ?", (key_id,))
                r = c.fetchone()
                if r:
                    user_id = r[0]
                    v2_uuid = r[1]
                    server_id = r[2]
                    c.execute("SELECT domain, COALESCE(v2ray_path,'/v2ray') FROM servers WHERE id = ?", (server_id,))
                    s = c.fetchone()
                    domain = s[0] if s else ''
                    v2path = s[1] if s else '/v2ray'
                    access_url = f"vless://{v2_uuid}@{domain}:443?path={v2path}&security=tls&type=ws#VeilBot-V2Ray"
                    protocol = 'v2ray'

        if user_id and access_url and protocol:
            try:
                await bot.send_message(user_id, format_key_message_unified(access_url, protocol), disable_web_page_preview=True, parse_mode="Markdown")
                return JSONResponse({"success": True, "user_id": user_id})
            except Exception as e:
                return JSONResponse({"success": False, "error": str(e)}, status_code=500)
        return JSONResponse({"success": False, "error": "Key not found"}, status_code=404)
    finally:
        if user_id:
            # Redirect back to user page in browsers
            try:
                return RedirectResponse(url=f"/users/{user_id}", status_code=303)
            except Exception:
                pass

@router.post("/users/keys/{key_id}/extend")
async def extend_key_days(request: Request, key_id: int, days: int = Form(30), csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    kr = KeyRepository(DB_PATH)
    user_id = None
    try:
        now_ts = int(time.time())
        extend_sec = max(1, int(days)) * 86400

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            # Outline first
            c.execute("SELECT user_id, expiry_at FROM keys WHERE id = ?", (key_id,))
            row = c.fetchone()
            if row:
                user_id = row[0]
                cur_exp = int(row[1] or 0)
                base = cur_exp if cur_exp > now_ts else now_ts
                new_exp = base + extend_sec
                kr.update_outline_key_expiry(key_id, new_exp)
            else:
                c.execute("SELECT user_id, expiry_at FROM v2ray_keys WHERE id = ?", (key_id,))
                r = c.fetchone()
                if r:
                    user_id = r[0]
                    cur_exp = int(r[1] or 0)
                    base = cur_exp if cur_exp > now_ts else now_ts
                    new_exp = base + extend_sec
                    kr.update_v2ray_key_expiry(key_id, new_exp)
                else:
                    return JSONResponse({"error": "Key not found"}, status_code=404)

        if user_id:
            return RedirectResponse(url=f"/users/{user_id}", status_code=303)
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

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
    
    key_repo = KeyRepository(DB_PATH)
    if key_repo.outline_key_exists(key_id):
        key_repo.update_outline_key_expiry(key_id, new_expiry_ts)
    elif key_repo.v2ray_key_exists(key_id):
        key_repo.update_v2ray_key_expiry(key_id, new_expiry_ts)
    else:
        return JSONResponse({"error": "Ключ не найден"}, status_code=404)
    
    return JSONResponse({"success": True})

@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    body = await request.body()
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
        request.client.host if request.client else ""
    )
    status_code = 200
    try:
        data = json.loads(body)
        event = data.get("event") or data.get("object", {}).get("status") or ""
        service = get_webhook_service()
        processed = await service.handle_yookassa_webhook(data)

        # Persist webhook log (best-effort)
        try:
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                c.execute(
                    """
                    CREATE TABLE IF NOT EXISTS webhook_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        provider TEXT,
                        event TEXT,
                        payload TEXT,
                        result TEXT,
                        status_code INTEGER,
                        ip TEXT,
                        created_at INTEGER
                    )
                    """
                )
                c.execute(
                    "INSERT INTO webhook_logs(provider, event, payload, result, status_code, ip, created_at) VALUES(?,?,?,?,?,?, strftime('%s','now'))",
                    ("yookassa", str(event), json.dumps(data, ensure_ascii=False), "ok" if processed else "error", 200 if processed else 400, client_ip),
                )
            conn.commit()
        except Exception as le:
            logging.error(f"[YOOKASSA_WEBHOOK] Log persist failed: {le}")

        if processed:
            return JSONResponse({"status": "ok"})
        status_code = 400
        return JSONResponse({"status": "error", "processed": False}, status_code=400)
    except Exception as e:
        status_code = 500
        logging.error(f"[YOOKASSA_WEBHOOK] Error: {e}")
        # Try to log even on parse error
        try:
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                c.execute(
                    "CREATE TABLE IF NOT EXISTS webhook_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, event TEXT, payload TEXT, result TEXT, status_code INTEGER, ip TEXT, created_at INTEGER)"
                )
                c.execute(
                    "INSERT INTO webhook_logs(provider, event, payload, result, status_code, ip, created_at) VALUES(?,?,?,?,?,?, strftime('%s','now'))",
                    ("yookassa", "parse_error", body.decode(errors='ignore')[:2000], "error", 500, client_ip),
                )
                conn.commit()
        except Exception:
            pass
        return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)




@router.get("/payments", response_class=HTMLResponse)
async def payments_page(
    request: Request,
    page: int = 1,
    limit: int = 50,
    sort_by: str | None = None,
    sort_order: str | None = None,
    preset: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
    tariff_id: int | None = None,
    provider: str | None = None,
    protocol: str | None = None,
    country: str | None = None,
    payment_id: str | None = None,
    email: str | None = None,
    created_after: str | None = None,   # YYYY-MM-DD
    created_before: str | None = None,  # YYYY-MM-DD
):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")

    repo = PaymentRepository(DB_PATH)

    # If payment_id provided, shortcut to single payment
    single_payment = None
    if payment_id:
        single_payment = await repo.get_by_payment_id(payment_id)

    payments = []
    total = 0

    if single_payment is not None:
        payments = [single_payment]
        total = 1
    else:
        # Build filter
        parsed_status = None
        if status:
            try:
                parsed_status = PaymentStatus(status)
            except Exception:
                parsed_status = None

        parsed_provider = None
        if provider:
            try:
                parsed_provider = PaymentProvider(provider)
            except Exception:
                parsed_provider = None

        ca_dt = None
        cb_dt = None
        try:
            if created_after:
                ca_dt = datetime.strptime(created_after, "%Y-%m-%d")
            if created_before:
                # include whole day end
                cb = datetime.strptime(created_before, "%Y-%m-%d")
                cb_dt = cb.replace(hour=23, minute=59, second=59)
        except Exception:
            ca_dt = None
            cb_dt = None

        filter_obj = PaymentFilter(
            user_id=user_id,
            tariff_id=tariff_id,
            status=parsed_status,
            provider=parsed_provider,
            country=country,
            protocol=protocol,
            limit=limit,
            offset=(page - 1) * limit,
            created_after=ca_dt,
            created_before=cb_dt,
        )

        # Presets
        if preset == 'paid_no_keys':
            # Используем хелпер репозитория
            payments = await repo.get_paid_payments_without_keys()
            total = len(payments)
        else:
            # errors preset → не оплаченные и не ожидающие
            if preset == 'errors':
                filter_obj.is_paid = False
                filter_obj.is_pending = False
            if preset == 'pending':
                filter_obj.is_pending = True
            payments = await repo.filter(filter_obj, sort_by=sort_by or "created_at", sort_order=sort_order or "DESC")
            total = await repo.count_filtered(filter_obj)

        # optional email filter in-memory
        if email:
            payments = [p for p in payments if (p.email or "").lower() == email.lower()]

    # quick stats for header
    paid_count = len([p for p in payments if (getattr(p, 'status', None) and str(p.status) == 'paid') or (hasattr(p, 'is_paid') and p.is_paid())])
    pending_count = len([p for p in payments if (getattr(p, 'status', None) and str(p.status) == 'pending') or (hasattr(p, 'is_pending') and p.is_pending())])
    failed_count = len([p for p in payments if (getattr(p, 'status', None) and str(p.status) in ['failed','cancelled','expired']) or (hasattr(p, 'is_failed') and p.is_failed())])

    # Stats for charts (last 14 days)
    daily_stats = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT date(created_at,'unixepoch') AS d,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status='paid' THEN 1 ELSE 0 END) AS paid_count,
                       SUM(CASE WHEN status='paid' THEN amount ELSE 0 END) AS paid_amount
                FROM payments
                WHERE created_at >= strftime('%s','now','-14 days')
                GROUP BY d
                ORDER BY d ASC
                """
            )
            for d, total_c, paid_c, paid_amt in c.fetchall():
                daily_stats.append({"date": d, "total": total_c or 0, "paid": paid_c or 0, "amount": (paid_amt or 0) / 100.0})
    except Exception:
        daily_stats = []

    # Calculate pages
    pages = (total + limit - 1) // limit
    
    return templates.TemplateResponse(
        "payments.html",
        {
            "request": request,
            "payments": payments,
            "page": page,
            "limit": limit,
            "total": total,
            "pages": pages,
            "sort": {"by": (sort_by or "created_at"), "order": (sort_order or "DESC")},
            "preset": preset or "",
            "daily_stats": daily_stats,
            "paid_count": paid_count,
            "pending_count": pending_count,
            "failed_count": failed_count,
            "email": email or "",
            "user_id": user_id or "",
            "payment_id": payment_id or "",
            "status": status or "",
            "filters": {
                "status": status or "",
                "user_id": user_id or "",
                "tariff_id": tariff_id or "",
                "provider": provider or "",
                "protocol": protocol or "",
                "country": country or "",
                "payment_id": payment_id or "",
                "email": email or "",
                "created_after": created_after or "",
                "created_before": created_before or "",
            },
            "csrf_token": get_csrf_token(request),
        },
    )


@router.get("/payments/{pid}", response_class=HTMLResponse)
async def payment_detail(request: Request, pid: str):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")

    repo = PaymentRepository(DB_PATH)
    payment = None
    # Try numeric id first
    try:
        payment = await repo.get_by_id(int(pid))
    except Exception:
        payment = None
    if payment is None:
        payment = await repo.get_by_payment_id(pid)

    if not payment:
        return templates.TemplateResponse(
            "payment_detail.html",
            {"request": request, "error": "Платеж не найден"},
        )

    # Try to get provider raw info
    provider_info = None
    try:
        ps = get_payment_service()
        provider_info = await ps.yookassa_service.get_payment_info(payment.payment_id)
    except Exception:
        provider_info = None

    return templates.TemplateResponse(
        "payment_detail.html",
        {
            "request": request,
            "payment": payment,
            "provider_info": provider_info,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/payments/reconcile")
async def payments_reconcile(request: Request, csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    ps = get_payment_service()
    try:
        pending_processed = await ps.process_pending_payments()
        issued_processed = await ps.process_paid_payments_without_keys()

        # Аудит в админ-лог
        try:
            log_admin_action(
                request,
                "RECONCILE_RUN",
                f"pending={pending_processed}, issued={issued_processed}"
            )
        except Exception:
            pass

        # Уведомление администратору в Telegram
        try:
            admin_id = settings.ADMIN_ID if hasattr(settings, 'ADMIN_ID') else None
            if admin_id:
                msg = (
                    f"🧾 Реконсиляция выполнена\n"
                    f"• Pending обработано: {pending_processed}\n"
                    f"• Выдано ключей: {issued_processed}\n"
                )
                await bot.send_message(admin_id, msg)
        except Exception:
            pass

        return JSONResponse({
            "success": True,
            "pending_processed": pending_processed,
            "issued_processed": issued_processed
        })
    except Exception as e:
        logging.error(f"Reconciliation error: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@router.post("/payments/{pid}/recheck")
async def payment_recheck(request: Request, pid: str, csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    ps = get_payment_service()
    try:
        # Try to process as success (will validate with provider)
        success = await ps.process_payment_success(pid)
        info = await ps.yookassa_service.get_payment_info(pid)
        return JSONResponse({"success": success, "info": info or {}})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/payments/{pid}/refund")
async def payment_refund(
    request: Request,
    pid: str,
    amount: int | None = Form(None),
    reason: str = Form("Возврат"),
    csrf_token: str = Form(...),
):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    repo = PaymentRepository(DB_PATH)
    payment = await repo.get_by_payment_id(pid)
    if not payment:
        return JSONResponse({"error": "Платеж не найден"}, status_code=404)

    refund_amount = amount if amount is not None else int(payment.amount)

    ps = get_payment_service()
    ok = await ps.refund_payment(pid, refund_amount, reason)
    return JSONResponse({"success": ok})


@router.post("/payments/{pid}/retry")
async def payment_retry(request: Request, pid: str, csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    ps = get_payment_service()
    try:
        # For now retry means re-running success flow
        success = await ps.process_payment_success(pid)
        return JSONResponse({"success": success})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.get("/webhooks", response_class=HTMLResponse)
async def webhook_logs_page(request: Request, page: int = 1, limit: int = 50, provider: str | None = None, event: str | None = None, payment_id: str | None = None, sort_by: str | None = None, sort_order: str | None = None):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")

    offset = (max(page, 1) - 1) * limit
    where = []
    params = []
    if provider:
        where.append("provider = ?")
        params.append(provider)
    if event:
        where.append("event = ?")
        params.append(event)
    if payment_id:
        where.append("payload LIKE ?")
        params.append(f"%{payment_id}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # Sorting whitelist
    sort_columns = {"created_at": "created_at", "status_code": "status_code", "event": "event"}
    order_col = sort_columns.get((sort_by or "created_at").lower(), "created_at")
    order_dir = "ASC" if (str(sort_order).upper() == "ASC") else "DESC"

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(f"SELECT COUNT(*) FROM webhook_logs {where_sql}", params)
        total = c.fetchone()[0] or 0
        params_page = params + [limit, offset]
        c.execute(
            f"SELECT id, provider, event, payload, result, status_code, ip, created_at FROM webhook_logs {where_sql} ORDER BY {order_col} {order_dir} LIMIT ? OFFSET ?",
            params_page,
        )
        rows = c.fetchall()

    logs = [
        {
            "id": r[0],
            "provider": r[1],
            "event": r[2],
            "payload": r[3],
            "result": r[4],
            "status_code": r[5],
            "ip": r[6],
            "ts": r[7],
        }
        for r in rows
    ]

    status_msg = request.query_params.get("status") if hasattr(request, 'query_params') else ""
    return templates.TemplateResponse(
        "webhooks.html",
        {
            "request": request,
            "logs": logs,
            "page": page,
            "limit": limit,
            "total": total,
            "filters": {"provider": provider or "", "event": event or ""},
            "sort": {"by": (sort_by or "created_at"), "order": (sort_order or "DESC")},
            "payment_id": payment_id or "",
            "csrf_token": get_csrf_token(request),
            "status": status_msg or "",
        },
    )

@router.post("/webhooks/{log_id}/replay")
async def replay_webhook(request: Request, log_id: int, csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT provider, payload FROM webhook_logs WHERE id = ?", (log_id,))
            row = c.fetchone()
        if not row:
            return JSONResponse({"error": "Log not found"}, status_code=404)
        provider, payload = row
        try:
            data = json.loads(payload)
        except Exception:
            return JSONResponse({"error": "Invalid payload JSON"}, status_code=400)
        # Idempotency: if same provider+payload was already processed OK (not a replay), skip
        already_ok = 0
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT COUNT(1) FROM webhook_logs WHERE provider = ? AND payload = ? AND result = 'ok' AND event != 'replay'",
                (provider or "", payload),
            )
            r = c.fetchone()
            already_ok = int(r[0] or 0)
        service = get_webhook_service()
        ok = False
        status_code = 200
        try:
            if already_ok:
                ok = True
                status_code = 200
            elif (provider or "").lower() == "yookassa":
                ok = await service.handle_yookassa_webhook(data)
                status_code = 200 if ok else 400
            else:
                status_code = 400
        finally:
            # best-effort: log replay result as a new row
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute(
                        "INSERT INTO webhook_logs(provider, event, payload, result, status_code, ip, created_at) VALUES(?,?,?,?,?,?, strftime('%s','now'))",
                        (provider or "", "replay", json.dumps(data, ensure_ascii=False), "ok" if ok else "error", status_code, "admin-replay"),
                    )
                    conn.commit()
            except Exception:
                pass
        log_admin_action(request, "WEBHOOK_REPLAY", f"id={log_id}, provider={provider}, ok={ok}")
        return RedirectResponse(url=f"/webhooks?status={'ok' if ok else 'error'}", status_code=303)
    except Exception as e:
        logging.error(f"Replay webhook error: {e}")
        return RedirectResponse(url=f"/webhooks?status=error", status_code=303)


@router.post("/payments/{pid}/issue")
async def payment_issue(request: Request, pid: str, csrf_token: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    ps = get_payment_service()
    try:
        ok = await ps.issue_key_for_payment(pid)
        return JSONResponse({"success": ok})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)