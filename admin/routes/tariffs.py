"""
Маршруты для управления тарифами
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
import sys
import os
import logging

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
async def tariffs_page(request: Request, q: str | None = None):
    """Страница списка тарифов"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    search_query = q.strip() if q and q.strip() else None
    tariffs = TariffRepository(DATABASE_PATH).list_tariffs(search_query=search_query)
    return templates.TemplateResponse("tariffs.html", {
        "request": request, 
        "tariffs": tariffs,
        "search_query": search_query or "",
        "csrf_token": get_csrf_token(request)
    })


@router.post("/add_tariff")
async def add_tariff(
    request: Request,
    name: str = Form(...),
    duration_sec: int = Form(...),
    traffic_limit_mb: int = Form(0),
    price_rub: int = Form(...),
    price_crypto_usd: float = Form(None),
    enable_yookassa: int = Form(1),
    enable_platega: int = Form(1),
    enable_cryptobot: int = Form(1),
    csrf_token: str = Form(...),
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
        price_crypto = float(price_crypto_usd) if price_crypto_usd and price_crypto_usd != "" else None
        tariff_data = TariffForm(name=name, duration_sec=duration_sec, price_rub=price_rub)

        # Нормализуем флаги оплаты: 0/1
        ey = 1 if enable_yookassa else 0
        ep = 1 if enable_platega else 0
        ec = 1 if enable_cryptobot else 0

        TariffRepository(DATABASE_PATH).add_tariff(
            tariff_data.name,
            tariff_data.duration_sec,
            tariff_data.price_rub,
            traffic_limit_mb=traffic_limit_mb,
            price_crypto_usd=price_crypto,
            enable_yookassa=ey,
            enable_platega=ep,
            enable_cryptobot=ec,
        )
        
        # Инвалидируем кэш меню бота
        try:
            from bot.keyboards import invalidate_menu_cache
            invalidate_menu_cache()
        except Exception as e:
            logging.warning(f"Failed to invalidate menu cache: {e}")
        
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
        
        # Инвалидируем кэш меню бота
        try:
            from bot.keyboards import invalidate_menu_cache
            invalidate_menu_cache()
        except Exception as e:
            logging.warning(f"Failed to invalidate menu cache: {e}")
        
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
            "price_rub": tariff[3],
            "traffic_limit_mb": tariff[4] if len(tariff) > 4 else 0,
            "price_crypto_usd": tariff[5] if len(tariff) > 5 else None,
            "enable_yookassa": tariff[6] if len(tariff) > 6 else 1,
            "enable_platega": tariff[7] if len(tariff) > 7 else 1,
            "enable_cryptobot": tariff[8] if len(tariff) > 8 else 1,
            "is_archived": tariff[9] if len(tariff) > 9 else 0,
        }
    })


@router.post("/tariffs/edit/{tariff_id}")
async def edit_tariff(
    request: Request,
    tariff_id: int,
    name: str = Form(...),
    duration_sec: int = Form(...),
    traffic_limit_mb: int = Form(0),
    price_rub: int = Form(...),
    price_crypto_usd: float = Form(None),
    enable_yookassa: int = Form(1),
    enable_platega: int = Form(1),
    enable_cryptobot: int = Form(1),
):
    """Обновление тарифа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")
    
    # Получаем значения чекбоксов напрямую из формы
    # Чекбоксы отправляются только если отмечены, поэтому проверяем наличие поля
    form_data = await request.form()
    is_archived_checked = form_data.get("is_archived") == "1"
    enable_yookassa_checked = form_data.get("enable_yookassa") == "1"
    enable_platega_checked = form_data.get("enable_platega") == "1"
    enable_cryptobot_checked = form_data.get("enable_cryptobot") == "1"
    
    price_crypto = float(price_crypto_usd) if price_crypto_usd and price_crypto_usd != "" else None

    ey = 1 if enable_yookassa_checked else 0
    ep = 1 if enable_platega_checked else 0
    ec = 1 if enable_cryptobot_checked else 0
    ia = 1 if is_archived_checked else 0

    # ВАЖНО: Всегда передаем is_archived, даже если 0, чтобы обновить значение в БД
    TariffRepository(DATABASE_PATH).update_tariff(
        tariff_id,
        name,
        duration_sec,
        price_rub,
        traffic_limit_mb=traffic_limit_mb,
        price_crypto_usd=price_crypto,
        enable_yookassa=ey,
        enable_platega=ep,
        enable_cryptobot=ec,
        is_archived=ia,  # Всегда передаем значение (0 или 1)
    )
    
    # Инвалидируем кэш меню бота
    try:
        from bot.keyboards import invalidate_menu_cache
        invalidate_menu_cache()
    except Exception as e:
        logging.warning(f"Failed to invalidate menu cache: {e}")
    
    log_admin_action(request, "EDIT_TARIFF", f"ID: {tariff_id}")
    
    return RedirectResponse(url="/tariffs", status_code=HTTP_303_SEE_OTHER)

