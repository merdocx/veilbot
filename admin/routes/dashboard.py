"""
Маршруты для дашборда администратора
"""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
import sqlite3
import time
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.settings import settings

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
    cache_key = "dashboard_stats"
    cached_stats = traffic_cache.get(cache_key)
    
    if cached_stats:
        active_keys, tariff_count, server_count = cached_stats
    else:
        # Вычисляем статистику - объединенный запрос для оптимизации
        now = int(time.time())
        with sqlite3.connect(DATABASE_PATH) as conn:
            c = conn.cursor()
            # Объединяем все COUNT запросы в один
            c.execute("""
                SELECT 
                    (SELECT COUNT(*) FROM keys WHERE expiry_at > ?) as active_outline,
                    (SELECT COUNT(*) FROM v2ray_keys WHERE expiry_at > ?) as active_v2ray,
                    (SELECT COUNT(*) FROM tariffs) as tariff_count,
                    (SELECT COUNT(*) FROM servers) as server_count
            """, (now, now))
            row = c.fetchone()
            active_keys = (row[0] or 0) + (row[1] or 0)
            tariff_count = row[2] or 0
            server_count = row[3] or 0
        
        # Кэш на 60 секунд
        traffic_cache.set(cache_key, (active_keys, tariff_count, server_count), ttl=60)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_keys": active_keys,
        "tariff_count": tariff_count,
        "server_count": server_count,
    })

