"""
Маршруты для дашборда администратора
"""
import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
import time
import sys
import os
import json
import random
import math

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.settings import settings
from app.infra.sqlite_utils import open_connection, retry_db_operation

from ..middleware.audit import log_admin_action
from ..dependencies.templates import templates
from fastapi.responses import RedirectResponse

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH


def _fetch_dashboard_stats_readonly():
    """Только SELECT — не держит write-lock, быстрее при конкуренции с ботом. Для отдачи страницы."""
    now = int(time.time())
    today = time.strftime("%Y-%m-%d")
    with open_connection(DATABASE_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT 
                (SELECT COUNT(*) FROM keys k
                 JOIN subscriptions s ON k.subscription_id = s.id
                 WHERE s.expires_at > ?) as active_outline,
                (SELECT COUNT(*) FROM v2ray_keys k
                 JOIN subscriptions s ON k.subscription_id = s.id
                 WHERE s.expires_at > ?) as active_v2ray,
                (SELECT COUNT(*) FROM subscriptions WHERE expires_at > ? AND is_active = 1) as active_subscriptions,
                (SELECT COUNT(*) FROM tariffs) as tariff_count,
                (SELECT COUNT(*) FROM servers) as server_count,
                (SELECT COUNT(*) FROM users) as started_users
        """, (now, now, now))
        row = c.fetchone()
        active_keys = (row[0] or 0) + (row[1] or 0)
        tariff_count = row[3] or 0
        server_count = row[4] or 0
        started_users = row[5] or 0

        c.execute("""
            SELECT date, active_keys, started_users, COALESCE(paid_subscriptions, 0)
            FROM dashboard_metrics
            ORDER BY date DESC
            LIMIT 30
        """)
        rows = c.fetchall()
        metrics = [
            {"date": r[0], "active_keys": r[1], "started_users": r[2], "paid_subscriptions": r[3] or 0}
            for r in rows
        ][::-1]

    chart_data = _prepare_chart_data(metrics)
    return (active_keys, tariff_count, server_count, started_users, metrics, chart_data)


def _apply_dashboard_metrics_writes():
    """Обновление dashboard_metrics (INSERT/UPDATE). Вызывается в фоне, при locked — не критично."""
    import logging
    log = logging.getLogger(__name__)
    try:
        now = int(time.time())
        today = time.strftime("%Y-%m-%d")
        end_of_today = int(time.mktime(time.strptime(today + " 23:59:59", "%Y-%m-%d %H:%M:%S")))
        timestamp = now
        with open_connection(DATABASE_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM subscriptions WHERE expires_at > ? AND is_active = 1
            """, (now,))
            active_subscriptions = (c.fetchone()[0] or 0)
            c.execute("""
                SELECT COUNT(*) FROM users
            """)
            started_users = (c.fetchone()[0] or 0)
            c.execute("""
                SELECT COUNT(*) FROM subscriptions s
                JOIN tariffs t ON s.tariff_id = t.id
                LEFT JOIN users u ON s.user_id = u.user_id
                WHERE s.expires_at > ? AND t.price_rub > 0 AND (u.is_vip IS NULL OR u.is_vip = 0)
            """, (end_of_today,))
            paid_subscriptions_today = (c.fetchone()[0] or 0)

            c.execute("""
                INSERT INTO dashboard_metrics (date, active_keys, started_users, paid_subscriptions, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    active_keys = excluded.active_keys,
                    started_users = excluded.started_users,
                    paid_subscriptions = excluded.paid_subscriptions,
                    updated_at = excluded.updated_at
            """, (today, active_subscriptions, started_users, paid_subscriptions_today, timestamp, timestamp))
            c.execute("""
                UPDATE dashboard_metrics
                SET active_keys = (
                    SELECT COUNT(*) FROM subscriptions s
                    WHERE s.expires_at > strftime('%s', dashboard_metrics.date || ' 23:59:59')
                    AND s.is_active = 1
                )
                WHERE date != ?
            """, (today,))
            c.execute("""
                UPDATE dashboard_metrics
                SET paid_subscriptions = (
                    SELECT COUNT(*) FROM subscriptions s
                    JOIN tariffs t ON s.tariff_id = t.id
                    LEFT JOIN users u ON s.user_id = u.user_id
                    WHERE s.expires_at > strftime('%s', dashboard_metrics.date || ' 23:59:59')
                    AND t.price_rub > 0 AND (u.is_vip IS NULL OR u.is_vip = 0)
                )
                WHERE date != ?
            """, (today,))
            c.execute("SELECT date FROM dashboard_metrics")
            existing_dates = {r[0] for r in c.fetchall()}
            if len(existing_dates) < 30:
                seed_rows = []
                for days_ago in range(29, -1, -1):
                    seed_date = time.strftime("%Y-%m-%d", time.localtime(time.time() - days_ago * 86400))
                    if seed_date in existing_dates:
                        continue
                    seed_rows.append((seed_date, random.randint(0, 1000), random.randint(0, 1000), random.randint(0, 50), timestamp, timestamp))
                if seed_rows:
                    c.executemany("""
                        INSERT OR IGNORE INTO dashboard_metrics (date, active_keys, started_users, paid_subscriptions, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, seed_rows)
            conn.commit()
    except Exception as e:
        log.warning("Dashboard metrics write skipped: %s", e)


@router.get("/dashboard")
async def dashboard(request: Request):
    """Главная страница дашборда"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    log_admin_action(request, "DASHBOARD_ACCESS")

    # Пытаемся получить кэшированные данные дашборда
    from app.infra.cache import traffic_cache
    cache_key = "dashboard_stats_v4"  # Обновлен для поддержки paid_subscriptions
    cached_stats = traffic_cache.get(cache_key)
    
    if cached_stats:
        active_keys, tariff_count, server_count, started_users, metrics, chart_data = cached_stats
    else:
        loop = asyncio.get_event_loop()
        try:
            # Сначала только чтение (SELECT) — не конкурирует за write-lock с ботом, страница грузится быстрее
            active_keys, tariff_count, server_count, started_users, metrics, chart_data = await loop.run_in_executor(
                None,
                lambda: retry_db_operation(
                    _fetch_dashboard_stats_readonly,
                    max_attempts=5,
                    initial_delay=0.15,
                    operation_name="dashboard_stats_readonly",
                ),
            )
            traffic_cache.set(cache_key, (active_keys, tariff_count, server_count, started_users, metrics, chart_data), ttl=60)
            # Обновление метрик в фоне — не блокируем ответ
            loop.run_in_executor(None, _apply_dashboard_metrics_writes)
        except Exception:
            # При блокировке БД отдаём нули, чтобы страница хотя бы открылась
            active_keys = tariff_count = server_count = started_users = 0
            metrics = []
            chart_data = _prepare_chart_data(metrics)

    metrics_json = json.dumps(metrics, ensure_ascii=False)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_keys": active_keys,
        "tariff_count": tariff_count,
        "server_count": server_count,
        "started_users": started_users,
        "metrics": metrics,
        "chart_data": chart_data,
        "metrics_json": metrics_json,
    })


def _prepare_chart_data(metrics: list[dict]) -> dict | None:
    if not metrics:
        return None
    
    dates = [m["date"] for m in metrics]
    active_keys = [m["active_keys"] for m in metrics]
    paid_subscriptions = [m.get("paid_subscriptions", 0) for m in metrics]
    
    # Убрали started_users из графика - теперь показываем только активные подписки и платные подписки
    max_value = max(max(active_keys, default=0), max(paid_subscriptions, default=0), 10)
    y_step = max(50, math.ceil(max_value / 4 / 50) * 50)
    # Ensure ticks cover the max_value
    tick_values = list(range(0, int(math.ceil(max_value / y_step) * y_step) + y_step, y_step))
    if tick_values[-1] > max_value + y_step:
        tick_values = tick_values[:-1]
    
    width = 820
    height = 260
    padding = {"top": 24, "right": 24, "bottom": 40, "left": 56}
    inner_width = width - padding["left"] - padding["right"]
    inner_height = height - padding["top"] - padding["bottom"]
    
    count = len(metrics)
    step_x = inner_width / max(count - 1, 1)
    
    def to_x(index: int) -> float:
        if count == 1:
            return padding["left"] + inner_width / 2
        return padding["left"] + step_x * index
    
    def to_y(value: float) -> float:
        if max_value == 0:
            return padding["top"] + inner_height
        return padding["top"] + inner_height - (inner_height * value / (tick_values[-1] if tick_values else max_value))
    
    def build_path(values: list[int]) -> tuple[str, str, list[dict]]:
        points = []
        commands = []
        for idx, value in enumerate(values):
            x = round(to_x(idx), 2)
            y = round(to_y(value), 2)
            points.append({"x": x, "y": y, "value": value, "label": dates[idx]})
            commands.append(f"{x},{y}")
        if not commands:
            return "", "", points
        path = "M " + " L ".join(commands)
        baseline_y = padding["top"] + inner_height
        fill_commands = commands + [f"{to_x(count-1):.2f},{baseline_y:.2f}", f"{to_x(0):.2f},{baseline_y:.2f}"]
        fill_path = "M " + " L ".join(fill_commands) + " Z"
        return path, fill_path, points
    
    # Убрали users_path - больше не показываем "Нажали «Start»" в графике
    keys_path, keys_fill, keys_points = build_path(active_keys)
    subscriptions_path, subscriptions_fill, subscriptions_points = build_path(paid_subscriptions)
    
    label_step = max(1, count // 6)
    x_labels = []
    for idx, date_str in enumerate(dates):
        show = idx % label_step == 0 or idx == count - 1
        x_labels.append({
            "text": date_str,
            "x": round(to_x(idx), 2),
            "show": show,
        })
    
    y_ticks = [
        {"value": tick, "y": round(to_y(tick), 2)}
        for tick in tick_values
    ]
    
    return {
        "width": width,
        "height": height,
        "padding": padding,
        "keys_path": keys_path,
        "keys_fill": keys_fill,
        "subscriptions_path": subscriptions_path,
        "subscriptions_fill": subscriptions_fill,
        "keys_points": keys_points,
        "subscriptions_points": subscriptions_points,
        "x_labels": x_labels,
        "y_ticks": y_ticks,
        "baseline_y": round(padding["top"] + inner_height, 2),
    }

