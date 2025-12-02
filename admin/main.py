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
app = FastAPI(title="VeilBot Admin", version="2.3.3")

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

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

# GZip compression middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS middleware (locked down)
allowed_origins = settings.ADMIN_ALLOWED_ORIGINS or []
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
        # Улучшенный CSP с nonce для inline скриптов
        # ВАЖНО: Для полного удаления unsafe-inline нужно вынести все inline скрипты в отдельные файлы
        # или использовать nonce в шаблонах: <script nonce="{{ csp_nonce }}">...</script>
        "Content-Security-Policy": (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{csp_nonce}' 'unsafe-inline' cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' fonts.googleapis.com; "
            "font-src fonts.gstatic.com; "
            "img-src 'self' data:;"
        )
    }
    for header, value in security_headers.items():
        response.headers[header] = value
    return response

# Session middleware with secure configuration
SECRET_KEY = settings.SECRET_KEY or secrets.token_urlsafe(32)
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
        # Add cache headers for static files
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        etag_value = f'W/"{int(stat_result.st_mtime)}-{stat_result.st_size}"'
        response.headers["ETag"] = etag_value
        return response

app.mount("/static", CachedStaticFiles(directory=static_dir), name="static")

# Templates
templates_dir = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=templates_dir)

# Добавляем кастомные фильтры для шаблонов
import time
from datetime import datetime

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
    try:
        with open_connection(settings.DATABASE_PATH) as conn:
            conn.execute("SELECT 1")
        return JSONResponse({"status": "ok"})
    except Exception as exc:  # pragma: no cover - диагностический маршрут
        logging.exception("Health check failed: %s", exc)
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)


