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

# Ensure project root on sys.path BEFORE importing top-level packages
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.logging_config import setup_logging
from app.settings import settings
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    filename='admin_audit.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="VeilBot Admin", version="2.1.0")

# Logging setup
setup_logging("INFO")

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
    response = await call_next(request)
    security_headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()"
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
    
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        # Add cache headers for static files
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        response.headers["ETag"] = f'"{hash(str(response.file_path))}"'
        return response

app.mount("/static", CachedStaticFiles(directory=static_dir), name="static")

# Templates
templates_dir = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=templates_dir)

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

# Глобальная обработка ошибок
from admin.middleware.error_handler import (
    global_exception_handler,
    http_exception_handler,
    validation_exception_handler
)
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException

app.add_exception_handler(Exception, global_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)


