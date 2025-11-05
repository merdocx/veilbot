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


@router.post("/cryptobot/webhook")
async def cryptobot_webhook(request: Request):
    """Webhook endpoint для CryptoBot"""
    body = await request.body()
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
        request.client.host if request.client else ""
    )
    status_code = 200
    
    try:
        data = json.loads(body)
        update_type = data.get("update_type", "")
        
        # Логируем webhook
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
                    ("cryptobot", str(update_type), json.dumps(data, ensure_ascii=False), "processing", 200, client_ip),
                )
                conn.commit()
        except Exception as le:
            logging.error(f"[CRYPTOBOT_WEBHOOK] Log persist failed: {le}")
        
        # Обрабатываем событие invoice_paid
        if update_type == "invoice_paid":
            payload = data.get("payload", {})
            invoice_id = payload.get("invoice_id")
            
            if not invoice_id:
                logging.error(f"[CRYPTOBOT_WEBHOOK] No invoice_id in payload: {data}")
                return JSONResponse({"status": "error", "reason": "no invoice_id"}, status_code=400)
            
            try:
                # Получаем платеж из БД
                from payments.config import get_payment_service
                payment_service = get_payment_service()
                
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute(
                        "SELECT user_id, tariff_id, country, protocol, email FROM payments WHERE payment_id = ? AND status = 'pending'",
                        (str(invoice_id),)
                    )
                    payment_row = c.fetchone()
                
                if not payment_row:
                    logging.warning(f"[CRYPTOBOT_WEBHOOK] Payment not found or already processed: {invoice_id}")
                    # Обновляем лог
                    try:
                        with sqlite3.connect(DB_PATH) as conn:
                            c = conn.cursor()
                            c.execute(
                                "UPDATE webhook_logs SET result = 'not_found' WHERE provider = 'cryptobot' AND event = ? AND created_at = (SELECT MAX(created_at) FROM webhook_logs WHERE provider = 'cryptobot')",
                                (str(update_type),)
                            )
                            conn.commit()
                    except Exception:
                        pass
                    return JSONResponse({"status": "ok", "processed": False})  # Возвращаем 200 чтобы CryptoBot не повторял
                
                user_id, tariff_id, country, protocol, email = payment_row
                
                # Обновляем статус платежа
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE payments SET status = 'paid', paid_at = strftime('%s','now') WHERE payment_id = ?",
                        (str(invoice_id),)
                    )
                    conn.commit()
                
                # Получаем данные тарифа
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute("SELECT id, name, duration_sec, price_rub, price_crypto_usd FROM tariffs WHERE id = ?", (tariff_id,))
                    tariff_row = c.fetchone()
                
                if not tariff_row:
                    logging.error(f"[CRYPTOBOT_WEBHOOK] Tariff not found: {tariff_id}")
                    return JSONResponse({"status": "error", "reason": "tariff_not_found"}, status_code=500)
                
                tariff = {
                    "id": tariff_row[0],
                    "name": tariff_row[1],
                    "duration_sec": tariff_row[2],
                    "price_rub": tariff_row[3],
                    "price_crypto_usd": tariff_row[4] if len(tariff_row) > 4 else None
                }
                
                # Создаем ключ для пользователя
                import importlib.util
                import time
                _root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                _bot_file = os.path.join(_root_dir, 'bot.py')
                spec = importlib.util.spec_from_file_location("bot_module", _bot_file)
                bot_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(bot_module)
                create_new_key_flow_with_protocol = bot_module.create_new_key_flow_with_protocol
                select_available_server_by_protocol = bot_module.select_available_server_by_protocol
                bot = bot_module.bot
                from utils import get_db_cursor
                from bot.utils.formatters import format_key_message_unified
                from app.repositories.server_repository import ServerRepository
                from app.repositories.key_repository import KeyRepository
                from vpn_protocols import ProtocolFactory
                
                with get_db_cursor(commit=True) as cursor:
                    # Выбираем сервер
                    server = select_available_server_by_protocol(cursor, country, protocol or "outline")
                    
                    if server:
                        await create_new_key_flow_with_protocol(
                            cursor, None, user_id, tariff, email, country, protocol or "outline"
                        )
                        
                        # Реферальный бонус
                        cursor.execute("SELECT referrer_id, bonus_issued FROM referrals WHERE referred_id = ?", (user_id,))
                        ref_row = cursor.fetchone()
                        if ref_row and ref_row[0] and not ref_row[1]:
                            referrer_id = ref_row[0]
                            now = int(time.time())
                            cursor.execute("SELECT id, expiry_at FROM keys WHERE user_id = ? AND expiry_at > ? ORDER BY expiry_at DESC LIMIT 1", (referrer_id, now))
                            key = cursor.fetchone()
                            bonus_duration = 30 * 24 * 3600
                            if key:
                                extend_existing_key = bot_module.extend_existing_key
                                extend_existing_key(cursor, key, bonus_duration)
                                await bot.send_message(referrer_id, "🎉 Ваш ключ продлён на месяц за приглашённого друга!")
                            else:
                                cursor.execute("SELECT * FROM tariffs WHERE duration_sec >= ? ORDER BY duration_sec ASC LIMIT 1", (bonus_duration,))
                                bonus_tariff = cursor.fetchone()
                                if bonus_tariff:
                                    bonus_tariff_dict = {"id": bonus_tariff[0], "name": bonus_tariff[1], "price_rub": bonus_tariff[4], "duration_sec": bonus_tariff[2]}
                                    await create_new_key_flow_with_protocol(cursor, None, referrer_id, bonus_tariff_dict, None, None, protocol or "outline")
                                    await bot.send_message(referrer_id, "🎉 Вам выдан бесплатный месяц за приглашённого друга!")
                            cursor.execute("UPDATE referrals SET bonus_issued = 1 WHERE referred_id = ?", (user_id,))
                
                # Обновляем лог как успешный
                try:
                    with sqlite3.connect(DB_PATH) as conn:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE webhook_logs SET result = 'ok' WHERE provider = 'cryptobot' AND event = ? AND created_at = (SELECT MAX(created_at) FROM webhook_logs WHERE provider = 'cryptobot')",
                            (str(update_type),)
                        )
                        conn.commit()
                except Exception:
                    pass
                
                logging.info(f"[CRYPTOBOT_WEBHOOK] Payment processed successfully: {invoice_id} for user {user_id}")
                return JSONResponse({"status": "ok"})
                
            except Exception as e:
                logging.error(f"[CRYPTOBOT_WEBHOOK] Error processing payment: {e}")
                # Обновляем лог как ошибку
                try:
                    with sqlite3.connect(DB_PATH) as conn:
                        c = conn.cursor()
                        c.execute(
                            "UPDATE webhook_logs SET result = 'error' WHERE provider = 'cryptobot' AND event = ? AND created_at = (SELECT MAX(created_at) FROM webhook_logs WHERE provider = 'cryptobot')",
                            (str(update_type),)
                        )
                        conn.commit()
                except Exception:
                    pass
                return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)
        else:
            # Неизвестное событие - просто логируем
            logging.info(f"[CRYPTOBOT_WEBHOOK] Unknown event type: {update_type}")
            return JSONResponse({"status": "ok", "processed": False})
            
    except Exception as e:
        status_code = 500
        logging.error(f"[CRYPTOBOT_WEBHOOK] Error: {e}")
        try:
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                c.execute(
                    "CREATE TABLE IF NOT EXISTS webhook_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, event TEXT, payload TEXT, result TEXT, status_code INTEGER, ip TEXT, created_at INTEGER)"
                )
                c.execute(
                    "INSERT INTO webhook_logs(provider, event, payload, result, status_code, ip, created_at) VALUES(?,?,?,?,?,?, strftime('%s','now'))",
                    ("cryptobot", "parse_error", body.decode(errors='ignore')[:2000], "error", 500, client_ip),
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

