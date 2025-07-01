from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from admin_routes import router as admin_router
import os
import logging
import secrets

# Setup logging
logging.basicConfig(
    filename='admin_audit.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="VeilBot Admin", version="1.0.0")

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this properly for production
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
    }
    for header, value in security_headers.items():
        response.headers[header] = value
    return response

# Session middleware with secure configuration
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
app.add_middleware(
    SessionMiddleware, 
    secret_key=SECRET_KEY,
    max_age=3600,  # 1 hour
    same_site="lax"
)

# Static files
static_dir = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Templates
templates_dir = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=templates_dir)

# Routers
app.include_router(admin_router)
