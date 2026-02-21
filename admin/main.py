from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
import os
import logging
import secrets
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Ensure project root on sys.path BEFORE importing top-level packages
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from app.logging_config import setup_logging, _SecretMaskingFilter
from app.settings import settings
from app.infra.sqlite_utils import open_connection
from dotenv import load_dotenv


def _init_log_dir() -> str:
    """Ensure log directory exists, fall back to local path if system dir is unavailable."""
    preferred = os.getenv("VEILBOT_LOG_DIR", "/var/log/veilbot")
    try:
        os.makedirs(preferred, exist_ok=True)
        return preferred
    except PermissionError:
        fallback = os.path.join(BASE_DIR, "logs")
        os.makedirs(fallback, exist_ok=True)
        sys.stderr.write(
            f"[admin/main] Warning: cannot write to {preferred}, using {fallback} instead\n"
        )
        return fallback


# Setup logging
LOG_DIR = _init_log_dir()
app = FastAPI(title="VeilBot Admin", version="2.4.38")

# Logging setup
setup_logging("INFO")

try:
    audit_log_path = os.path.join(LOG_DIR, "admin_audit.log")
    audit_handler = RotatingFileHandler(audit_log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    audit_handler.setLevel(logging.INFO)
    audit_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    audit_handler.addFilter(_SecretMaskingFilter())
    logging.getLogger().addHandler(audit_handler)
except Exception:
    pass

# Load environment from project root .env explicitly (systemd cwd is admin/)
try:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(project_root, ".env"))
except Exception:
    pass

# Validate configuration at startup (log-only, do not crash admin UI)
startup_validation = settings.validate_startup()
for err in startup_validation["errors"]:
    logging.error(f"Config error: {err}")
for warn in startup_validation["warnings"]:
    logging.warning(f"Config warning: {warn}")

# КРИТИЧНО: Предупреждение о SECRET_KEY
if not settings.SECRET_KEY:
    logging.warning(
        "⚠️  SECRET_KEY not set! A random key will be generated on each restart. "
        "This will invalidate all user sessions on server restart. "
        "For production, set SECRET_KEY in .env file. "
        "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

# GZip compression middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS middleware (locked down)
# Для статических файлов разрешаем доступ из любого источника
allowed_origins = settings.ADMIN_ALLOWED_ORIGINS or []

@app.middleware("http")
async def cors_for_static(request: Request, call_next):
    """CORS middleware для статических файлов - разрешаем доступ из любого источника"""
    if request.url.path.startswith('/static/'):
        response = await call_next(request)
        # Разрешаем CORS для статических файлов
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        # Убираем ограничения для статических файлов (если они есть)
        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]
        if "Content-Security-Policy" in response.headers:
            del response.headers["Content-Security-Policy"]
        return response
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    # НЕ применяем CSP к статическим файлам (CSS, JS, изображения)
    # Они должны загружаться без ограничений CSP для правильной работы
    # Проверяем ДО обработки запроса, чтобы статические файлы не блокировались
    if request.url.path.startswith('/static/'):
        response = await call_next(request)
        # Для статических файлов применяем только базовые заголовки безопасности
        # НЕ применяем CSP и другие ограничивающие заголовки
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Явно разрешаем доступ к статическим файлам
        response.status_code = 200
        return response
    
    # Генерируем nonce для CSP ДО обработки запроса, чтобы он был доступен в шаблонах
    if not hasattr(request.state, 'csp_nonce'):
        request.state.csp_nonce = secrets.token_urlsafe(16)
    
    response = await call_next(request)
    
    csp_nonce = getattr(request.state, 'csp_nonce', secrets.token_urlsafe(16))
    
    security_headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        # Улучшенный CSP: удален 'unsafe-inline' из script-src для защиты от XSS
        # Все скрипты вынесены в отдельные файлы, nonce доступен для будущего использования
        # Для style-src 'unsafe-inline' оставлен, так как inline стили менее критичны для безопасности
        "Content-Security-Policy": (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{csp_nonce}' cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' fonts.googleapis.com; "
            "font-src fonts.gstatic.com; "
            "img-src 'self' data:;"
        )
    }
    for header, value in security_headers.items():
        response.headers[header] = value
    return response

# Session middleware with secure configuration
# КРИТИЧНО: Используем SECRET_KEY из настроек, если не задан - генерируем случайный
# (но это не рекомендуется для production - сессии будут инвалидироваться при перезапуске)
SECRET_KEY = settings.SECRET_KEY or secrets.token_urlsafe(32)
if not settings.SECRET_KEY:
    # Дополнительное логирование для явности (если не было предупреждения выше)
    logging.warning(
        f"Using randomly generated SECRET_KEY (will change on restart). "
        f"Set SECRET_KEY in .env to avoid session invalidation."
    )
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=3600,  # 1 hour
    same_site="strict",
    https_only=settings.SESSION_SECURE,
)

# Static files with caching headers
static_dir = os.path.join(BASE_DIR, "static")

class CachedStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    def file_response(self, path, stat_result, scope):
        response = super().file_response(path, stat_result, scope)
        # Для CSS файлов используем более короткое кэширование для быстрого обновления
        if path.endswith('.css'):
            response.headers["Cache-Control"] = "public, max-age=3600"  # 1 час
        else:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        etag_value = f'W/"{int(stat_result.st_mtime)}-{stat_result.st_size}"'
        response.headers["ETag"] = etag_value
        return response

app.mount("/static", CachedStaticFiles(directory=static_dir), name="static")

# Templates
templates_dir = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=templates_dir)

# Устанавливаем глобальную переменную для версионирования статических файлов
# ДОЛЖНО быть после создания объекта templates
_STARTUP_TIME = int(time.time())
templates.env.globals['static_version'] = _STARTUP_TIME
logging.info(f"Initialized templates with static_version={_STARTUP_TIME}")

# Добавляем кастомные фильтры для шаблонов

def timestamp_filter(value):
    """Конвертирует timestamp в читаемую дату"""
    if not value:
        return "—"
    try:
        if isinstance(value, str):
            value = float(value)
        dt = datetime.fromtimestamp(float(value))
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError, OSError):
        return "—"

def my_datetime_local_filter(value):
    """Конвертирует timestamp в формат datetime-local (YYYY-MM-DDTHH:mm)"""
    if not value:
        return ""
    try:
        if isinstance(value, str):
            value = float(value)
        dt = datetime.fromtimestamp(float(value))
        return dt.strftime("%Y-%m-%dT%H:%M")
    except (ValueError, TypeError, OSError):
        return ""

def rub_filter(value):
    """Конвертирует сумму в копейках в рубли с форматом"""
    if value is None:
        return "0.00 ₽"
    try:
        if isinstance(value, str):
            value = float(value)
        rubles = value / 100.0
        return f"{rubles:,.2f} ₽".replace(",", " ")
    except (ValueError, TypeError):
        return f"{value} ₽"

templates.env.filters['timestamp'] = timestamp_filter
templates.env.filters['my_datetime_local'] = my_datetime_local_filter
templates.env.filters['rub'] = rub_filter

# Переменная static_version уже установлена выше при создании templates

# Routers - используем новые модульные роутеры
from admin.routes import (
    auth_router,
    dashboard_router,
    tariffs_router,
    servers_router,
    users_router,
    keys_router,
    payments_router,
    webhooks_router,
    cleanup_router,
    subscriptions_router,
    tools_router,
)

# Подключаем все модульные роутеры
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(tariffs_router)
app.include_router(servers_router)
app.include_router(users_router)
app.include_router(keys_router)
app.include_router(payments_router)
app.include_router(webhooks_router)
app.include_router(cleanup_router)
app.include_router(subscriptions_router)
app.include_router(tools_router)

# Глобальная обработка ошибок
from admin.middleware.error_handler import (
    global_exception_handler,
    http_exception_handler,
    validation_exception_handler
)
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException

app.add_exception_handler(Exception, global_exception_handler)


@app.get("/healthz", tags=["health"])
async def health_check():
    """
    Расширенный health check endpoint для проверки всех критичных сервисов.
    
    Проверяет:
    - База данных (основная проверка)
    - Доступность VPN серверов (Outline, V2Ray) - опционально
    - Метрики производительности (response time)
    """
    import time
    import aiohttp
    from app.repositories.server_repository import ServerRepository
    
    start_time = time.time()
    health_status = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "checks": {},
        "metrics": {}
    }
    overall_healthy = True
    
    # Проверка БД (критично)
    try:
        db_start = time.time()
        with open_connection(settings.DATABASE_PATH) as conn:
            conn.execute("SELECT 1")
        db_time = (time.time() - db_start) * 1000  # в миллисекундах
        health_status["checks"]["database"] = {
            "status": "ok",
            "response_time_ms": round(db_time, 2)
        }
    except Exception as exc:
        logging.exception("Database health check failed: %s", exc)
        health_status["checks"]["database"] = {
            "status": "error",
            "error": str(exc)
        }
        overall_healthy = False
    
    # Проверка VPN серверов (опционально, не блокируем health check если они недоступны)
    try:
        server_repo = ServerRepository()
        servers = server_repo.list_servers()
        active_servers = [s for s in servers if s[5] == 1]  # active = 1
        
        servers_status = {
            "total": len(servers),
            "active": len(active_servers),
            "checked": 0,
            "available": 0,
            "unavailable": 0
        }
        
        # Проверяем только первые 3 активных сервера для быстроты (timeout 5 секунд)
        servers_to_check = active_servers[:3]
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            for server in servers_to_check:
                server_id, name, api_url, cert_sha256, max_keys, active, country, protocol, domain, api_key, v2ray_path, access_level = server
                protocol = (protocol or "outline").lower()
                
                try:
                    check_start = time.time()
                    if protocol == "v2ray" and api_url:
                        # Проверяем V2Ray API
                        async with session.get(f"{api_url}/", ssl=False) as resp:
                            if resp.status == 200:
                                servers_status["available"] += 1
                            else:
                                servers_status["unavailable"] += 1
                    elif protocol == "outline" and api_url:
                        # Проверяем Outline API
                        async with session.get(f"{api_url}/access-keys", ssl=False) as resp:
                            if resp.status == 200:
                                servers_status["available"] += 1
                            else:
                                servers_status["unavailable"] += 1
                    else:
                        servers_status["unavailable"] += 1
                    servers_status["checked"] += 1
                except Exception as e:
                    servers_status["unavailable"] += 1
                    servers_status["checked"] += 1
                    logging.debug(f"Server {name} (ID: {server_id}) health check failed: {e}")
        
        health_status["checks"]["vpn_servers"] = servers_status
        
    except Exception as exc:
        logging.warning("VPN servers health check failed (non-critical): %s", exc)
        health_status["checks"]["vpn_servers"] = {
            "status": "error",
            "error": str(exc),
            "note": "Non-critical check"
        }
    
    # Метрики производительности
    total_time = (time.time() - start_time) * 1000
    health_status["metrics"] = {
        "total_response_time_ms": round(total_time, 2),
        "database_response_time_ms": health_status["checks"].get("database", {}).get("response_time_ms", 0)
    }
    
    # Определяем финальный статус
    if not overall_healthy:
        health_status["status"] = "error"
        return JSONResponse(health_status, status_code=503)
    
    return JSONResponse(health_status)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)


