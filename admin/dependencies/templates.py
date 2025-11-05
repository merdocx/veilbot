"""
Настройка шаблонов Jinja2
"""
import os
from starlette.templating import Jinja2Templates
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def timestamp_filter(value):
    """Конвертирует timestamp в читаемую дату"""
    if not value:
        return "—"
    try:
        if isinstance(value, str):
            value = float(value)
        dt = datetime.fromtimestamp(float(value))
        return dt.strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError, OSError):
        return "—"


def my_datetime_local_filter(value):
    """Конвертирует timestamp в формат datetime-local (YYYY-MM-DDTHH:mm)"""
    if not value:
        return ""
    try:
        if isinstance(value, str):
            value = float(value)
        dt = datetime.fromtimestamp(float(value))
        return dt.strftime("%Y-%m-%dT%H:%M")
    except (ValueError, TypeError, OSError):
        return ""


# Добавляем кастомные фильтры
templates.env.filters['timestamp'] = timestamp_filter
templates.env.filters['my_datetime_local'] = my_datetime_local_filter


def get_templates() -> Jinja2Templates:
    """Получить объект шаблонов"""
    return templates

