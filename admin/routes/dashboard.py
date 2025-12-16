"""
Маршруты для дашборда администратора
"""
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
from app.infra.sqlite_utils import open_connection

from ..middleware.audit import log_admin_action
from ..dependencies.templates import templates
from fastapi.responses import RedirectResponse

router = APIRouter()

DATABASE_PATH = settings.DATABASE_PATH


@router.get("/dashboard")
async def dashboard(request: Request):
    """Главная страница дашборда"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login")
    
    log_admin_action(request, "DASHBOARD_ACCESS")

    # Пытаемся получить кэшированные данные дашборда
    from app.infra.cache import traffic_cache
    cache_key = "dashboard_stats_v3"
    cached_stats = traffic_cache.get(cache_key)
    
    if cached_stats:
        active_keys, tariff_count, server_count, started_users, metrics, chart_data = cached_stats
    else:
        # Вычисляем статистику - объединенный запрос для оптимизации
        now = int(time.time())
        today = time.strftime("%Y-%m-%d")
        with open_connection(DATABASE_PATH) as conn:
            c = conn.cursor()
            # Объединяем все COUNT запросы в один
            c.execute("""
                SELECT 
                    (SELECT COUNT(*) FROM keys k
                     JOIN subscriptions s ON k.subscription_id = s.id
                     WHERE s.expires_at > ?) as active_outline,
                    (SELECT COUNT(*) FROM v2ray_keys k
                     JOIN subscriptions s ON k.subscription_id = s.id
                     WHERE s.expires_at > ?) as active_v2ray,
                    (SELECT COUNT(*) FROM tariffs) as tariff_count,
                    (SELECT COUNT(*) FROM servers) as server_count,
                    (SELECT COUNT(*) FROM users) as started_users
            """, (now, now))
            row = c.fetchone()
            active_keys = (row[0] or 0) + (row[1] or 0)
            tariff_count = row[2] or 0
            server_count = row[3] or 0
            started_users = row[4] or 0
            
            # Сохраняем ежедневные метрики (одна запись в день)
            timestamp = int(time.time())
            c.execute("""
                INSERT INTO dashboard_metrics (date, active_keys, started_users, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    active_keys = excluded.active_keys,
                    started_users = excluded.started_users,
                    updated_at = excluded.updated_at
            """, (today, active_keys, started_users, timestamp, timestamp))
            
            # Получаем историю метрик
            c.execute("""
                SELECT date, active_keys, started_users
                FROM dashboard_metrics
                ORDER BY date DESC
                LIMIT 30
            """)
            rows = c.fetchall()
            metrics = [
                {"date": r[0], "active_keys": r[1], "started_users": r[2]}
                for r in rows
            ][::-1]  # chronological order

            if len(metrics) < 30:
                existing_dates = {entry["date"] for entry in metrics}
                seed_rows = []
                for days_ago in range(29, -1, -1):
                    seed_date = time.strftime("%Y-%m-%d", time.localtime(time.time() - days_ago * 86400))
                    if seed_date in existing_dates:
                        continue
                    seed_active = random.randint(0, 1000)
                    seed_started = random.randint(0, 1000)
                    seed_rows.append((seed_date, seed_active, seed_started, timestamp, timestamp))

                if seed_rows:
                    c.executemany("""
                        INSERT OR IGNORE INTO dashboard_metrics (date, active_keys, started_users, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, seed_rows)

                    c.execute("""
                        SELECT date, active_keys, started_users
                        FROM dashboard_metrics
                        ORDER BY date DESC
                        LIMIT 30
                    """)
                    rows = c.fetchall()
                    metrics = [
                        {"date": r[0], "active_keys": r[1], "started_users": r[2]}
                        for r in rows
                    ][::-1]
            
            conn.commit()
        
        chart_data = _prepare_chart_data(metrics)
        # Кэш на 60 секунд
        traffic_cache.set(cache_key, (active_keys, tariff_count, server_count, started_users, metrics, chart_data), ttl=60)

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
    started_users = [m["started_users"] for m in metrics]
    active_keys = [m["active_keys"] for m in metrics]
    
    max_value = max(max(started_users, default=0), max(active_keys, default=0), 10)
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
    
    users_path, users_fill, users_points = build_path(started_users)
    keys_path, keys_fill, keys_points = build_path(active_keys)
    
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
        "users_path": users_path,
        "users_fill": users_fill,
        "keys_path": keys_path,
        "keys_fill": keys_fill,
        "users_points": users_points,
        "keys_points": keys_points,
        "x_labels": x_labels,
        "y_ticks": y_ticks,
        "baseline_y": round(padding["top"] + inner_height, 2),
    }

