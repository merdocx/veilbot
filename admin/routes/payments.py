"""
Маршруты для управления платежами
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
import sys
import os
import sqlite3
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from payments.repositories.payment_repository import PaymentRepository
from payments.models.payment import PaymentFilter
from payments.models.enums import PaymentStatus, PaymentProvider
from payments.config import get_payment_service
from bot import bot
from app.settings import settings

from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token, validate_csrf_token
from ..dependencies.templates import templates

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH
DB_PATH = DATABASE_PATH


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
    """Страница списка платежей"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")

    repo = PaymentRepository(DB_PATH)

    # Если payment_id предоставлен, переходим к одиночному платежу
    single_payment = None
    if payment_id:
        single_payment = await repo.get_by_payment_id(payment_id)

    payments = []
    total = 0

    if single_payment is not None:
        payments = [single_payment]
        total = 1
    else:
        # Построение фильтра
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
                # включаем весь день до конца
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

        # опциональный фильтр по email в памяти
        if email:
            payments = [p for p in payments if (p.email or "").lower() == email.lower()]

    # Быстрая статистика для заголовка
    paid_count = len([p for p in payments if (getattr(p, 'status', None) and str(p.status) == 'paid') or (hasattr(p, 'is_paid') and p.is_paid())])
    pending_count = len([p for p in payments if (getattr(p, 'status', None) and str(p.status) == 'pending') or (hasattr(p, 'is_pending') and p.is_pending())])
    failed_count = len([p for p in payments if (getattr(p, 'status', None) and str(p.status) in ['failed','cancelled','expired']) or (hasattr(p, 'is_failed') and p.is_failed())])

    # Статистика для графиков (последние 14 дней)
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

    # Вычисляем страницы
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
    """Страница детальной информации о платеже"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/login")

    repo = PaymentRepository(DB_PATH)
    payment = None
    
    # Пробуем числовой id сначала
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

    # Пытаемся получить сырую информацию от провайдера
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
    """Реконсиляция платежей (обработка pending и выдача ключей)"""
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
    """Повторная проверка платежа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    ps = get_payment_service()
    try:
        # Пытаемся обработать как успешный (будет проверено с провайдером)
        success = await ps.process_payment_success(pid)
        info = await ps.yookassa_service.get_payment_info(pid)
        log_admin_action(request, "PAYMENT_RECHECK", f"Payment ID: {pid}, Success: {success}")
        return JSONResponse({"success": success, "info": info or {}})
    except Exception as e:
        log_admin_action(request, "PAYMENT_RECHECK_ERROR", f"Payment ID: {pid}, Error: {str(e)}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/payments/{pid}/refund")
async def payment_refund(
    request: Request,
    pid: str,
    amount: int | None = Form(None),
    reason: str = Form("Возврат"),
    csrf_token: str = Form(...),
):
    """Возврат платежа"""
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
    log_admin_action(request, "PAYMENT_REFUND", f"Payment ID: {pid}, Amount: {refund_amount}, Reason: {reason}, Success: {ok}")
    return JSONResponse({"success": ok})


@router.post("/payments/{pid}/retry")
async def payment_retry(request: Request, pid: str, csrf_token: str = Form(...)):
    """Повторная попытка обработки платежа"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    ps = get_payment_service()
    try:
        # Для retry значит повторный запуск success flow
        success = await ps.process_payment_success(pid)
        log_admin_action(request, "PAYMENT_RETRY", f"Payment ID: {pid}, Success: {success}")
        return JSONResponse({"success": success})
    except Exception as e:
        log_admin_action(request, "PAYMENT_RETRY_ERROR", f"Payment ID: {pid}, Error: {str(e)}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@router.post("/payments/{pid}/issue")
async def payment_issue(request: Request, pid: str, csrf_token: str = Form(...)):
    """Принудительная выдача ключа для платежа"""
    if not request.session.get("admin_logged_in"):
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    
    if not validate_csrf_token(request, csrf_token):
        return JSONResponse({"error": "Invalid CSRF"}, status_code=400)

    ps = get_payment_service()
    try:
        ok = await ps.issue_key_for_payment(pid)
        log_admin_action(request, "PAYMENT_ISSUE", f"Payment ID: {pid}, Success: {ok}")
        return JSONResponse({"success": ok})
    except Exception as e:
        log_admin_action(request, "PAYMENT_ISSUE_ERROR", f"Payment ID: {pid}, Error: {str(e)}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

