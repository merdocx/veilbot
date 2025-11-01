"""
Маршруты для работы с вебхуками и логами вебхуков
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.status import HTTP_303_SEE_OTHER
import sys
import os
import sqlite3
import json
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from payments.config import get_webhook_service
from app.settings import settings

from ..middleware.audit import log_admin_action
from ..dependencies.csrf import get_csrf_token, validate_csrf_token
from ..dependencies.templates import templates

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH
DB_PATH = DATABASE_PATH


@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    """Webhook endpoint для YooKassa"""
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

        # Сохранение лога вебхука (best-effort)
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
        # Пытаемся залогировать даже при ошибке парсинга
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


@router.get("/webhooks", response_class=HTMLResponse)
async def webhook_logs_page(
    request: Request,
    page: int = 1,
    limit: int = 50,
    provider: str | None = None,
    event: str | None = None,
    payment_id: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None
):
    """Страница логов вебхуков"""
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

    # Sorting whitelist - безопасная валидация
    sort_columns = {"created_at": "created_at", "status_code": "status_code", "event": "event"}
    order_col = sort_columns.get((sort_by or "created_at").lower(), "created_at")
    order_dir = "ASC" if (str(sort_order).upper() == "ASC") else "DESC"
    
    # Использование параметризованных запросов для безопасности
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # COUNT запрос - безопасный, так как where_sql построен из валидированных параметров
        count_query = "SELECT COUNT(*) FROM webhook_logs" + where_sql
        c.execute(count_query, params)
        total = c.fetchone()[0] or 0
        
        # SELECT запрос - безопасный, так как order_col валидирован через whitelist
        select_query = (
            "SELECT id, provider, event, payload, result, status_code, ip, created_at "
            f"FROM webhook_logs {where_sql} ORDER BY {order_col} {order_dir} LIMIT ? OFFSET ?"
        )
        params_page = params + [limit, offset]
        c.execute(select_query, params_page)
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
    """Повторная обработка вебхука"""
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
        
        # Идемпотентность: если тот же provider+payload уже обработан OK (не replay), пропускаем
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
            # best-effort: логируем результат replay как новую строку
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

