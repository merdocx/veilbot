"""
Маршруты для аутентификации администратора
"""
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.status import HTTP_303_SEE_OTHER
from slowapi import Limiter
from slowapi.util import get_remote_address
from passlib.context import CryptContext
import sys
import os

# Импорты из родительского каталога
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.settings import settings

from ..middleware.audit import log_admin_action
from ..middleware.bruteforce import BruteforceProtection
from ..dependencies.csrf import get_csrf_token
from ..dependencies.templates import templates

router = APIRouter()
bf_protection = BruteforceProtection()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

# Конфигурация
ADMIN_USERNAME = settings.ADMIN_USERNAME
ADMIN_PASSWORD_HASH = settings.ADMIN_PASSWORD_HASH or ""


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


@router.get("/")
async def root(request: Request):
    """Root route that redirects to login page"""
    return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)


@router.get("/login")
async def login_page(request: Request):
    """Страница входа в админку"""
    return templates.TemplateResponse("login.html", {"request": request})


@limiter.limit("5/minute")
@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Обработка входа администратора"""
    # Проверка защиты от брутфорса
    try:
        bf_protection.check_bruteforce(request)
    except HTTPException as e:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": e.detail
        })
    
    # Логирование попытки входа
    log_admin_action(request, "LOGIN_ATTEMPT", f"Username: {username}")
    
    # Проверка наличия пароля в конфигурации
    if not ADMIN_PASSWORD_HASH:
        return templates.TemplateResponse("login.html", {
            "request": request, 
            "error": "Admin password not configured. Please set ADMIN_PASSWORD_HASH in environment variables."
        })
    
    client_ip = bf_protection.get_client_ip(request)
    
    # Проверка учетных данных
    if username == ADMIN_USERNAME and verify_password(password, ADMIN_PASSWORD_HASH):
        # Успешный вход - очищаем попытки
        bf_protection.record_successful_login(client_ip)
        
        request.session["admin_logged_in"] = True
        request.session["admin_username"] = username
        log_admin_action(request, "LOGIN_SUCCESS", f"Username: {username}")
        return RedirectResponse(url="/dashboard", status_code=HTTP_303_SEE_OTHER)
    
    # Неудачный вход - записываем попытку
    bf_protection.record_failed_login(client_ip)
    log_admin_action(request, "LOGIN_FAILED", f"Username: {username}, IP: {client_ip}")
    
    # Проверяем снова, не заблокирован ли IP после неудачной попытки
    try:
        bf_protection.check_bruteforce(request)
    except HTTPException as e:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": e.detail
        })
    
    return templates.TemplateResponse("login.html", {
        "request": request, 
        "error": "Неверный логин или пароль"
    })


@router.get("/logout")
async def logout(request: Request):
    """Выход из админки"""
    if request.session.get("admin_logged_in"):
        log_admin_action(request, "LOGOUT")
    request.session.clear()
    return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)

