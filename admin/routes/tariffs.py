"""
Маршруты для управления тарифами
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.repositories.tariff_repository import TariffRepository
from app.settings import settings

from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token, validate_csrf_token
from ..dependencies.templates import templates
from .models import TariffForm

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH


@router.get("/tariffs")
async def tariffs_page(request: Request):
    """Страница списка тарифов"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    tariffs = TariffRepository(DATABASE_PATH).list_tariffs()
    return templates.TemplateResponse("tariffs.html", {
        "request": request, 
        "tariffs": tariffs,
        "csrf_token": get_csrf_token(request)
    })


@router.post("/add_tariff")
async def add_tariff(
    request: Request,
    name: str = Form(...),
    duration_sec: int = Form(...),
    price_rub: int = Form(...),
    csrf_token: str = Form(...)
):
    """Добавление нового тарифа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    # Проверка CSRF токена
    if not validate_csrf_token(request, csrf_token):
        log_admin_action(request, "CSRF_ATTACK", f"Invalid CSRF token for add_tariff")
        return templates.TemplateResponse("tariffs.html", {
            "request": request, 
            "error": "Invalid request. Please try again.",
            "tariffs": TariffRepository(DATABASE_PATH).list_tariffs(),
            "csrf_token": get_csrf_token(request)
        })
    
    try:
        # Валидация входных данных
        tariff_data = TariffForm(name=name, duration_sec=duration_sec, price_rub=price_rub)
        
        TariffRepository(DATABASE_PATH).add_tariff(
            tariff_data.name,
            tariff_data.duration_sec,
            tariff_data.price_rub
        )
        
        log_admin_action(
            request,
            "ADD_TARIFF",
            f"Name: {tariff_data.name}, Duration: {tariff_data.duration_sec}s, Price: {tariff_data.price_rub}₽"
        )
        
        return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)
    except ValueError as e:
        log_admin_action(request, "ADD_TARIFF_FAILED", f"Validation error: {str(e)}")
        return templates.TemplateResponse("tariffs.html", {
            "request": request, 
            "error": f"Validation error: {str(e)}",
            "tariffs": TariffRepository(DATABASE_PATH).list_tariffs(),
            "csrf_token": get_csrf_token(request)
        })
    except Exception as e:
        log_admin_action(request, "ADD_TARIFF_ERROR", f"Database error: {str(e)}")
        return templates.TemplateResponse("tariffs.html", {
            "request": request, 
            "error": "Database error occurred",
            "tariffs": TariffRepository(DATABASE_PATH).list_tariffs(),
            "csrf_token": get_csrf_token(request)
        })


@router.get("/delete_tariff/{tariff_id}")
async def delete_tariff(request: Request, tariff_id: int):
    """Удаление тарифа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    try:
        repo = TariffRepository(DATABASE_PATH)
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
    """Страница редактирования тарифа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    tariff = TariffRepository(DATABASE_PATH).get_tariff(tariff_id)
    
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
async def edit_tariff(
    request: Request,
    tariff_id: int,
    name: str = Form(...),
    duration_sec: int = Form(...),
    price_rub: int = Form(...)
):
    """Обновление тарифа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    TariffRepository(DATABASE_PATH).update_tariff(tariff_id, name, duration_sec, price_rub)
    log_admin_action(request, "EDIT_TARIFF", f"ID: {tariff_id}")
    
    return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)

