"""
Настройка шаблонов Jinja2
"""
import os
import time
from datetime import datetime
from urllib.parse import quote

from starlette.requests import Request
from starlette.templating import Jinja2Templates

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Устанавливаем глобальную переменную для версионирования статических файлов
_STARTUP_TIME = int(time.time())
templates.env.globals['static_version'] = _STARTUP_TIME
import logging
logging.info(f"[templates] Set static_version={_STARTUP_TIME} in globals")


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


def rub_filter(value):
    """Конвертирует сумму в копейках в рубли с форматом"""
    if value is None:
        return "0.00 ₽"
    try:
        if isinstance(value, str):
            value = float(value)
        rubles = value / 100.0
        return f"{rubles:,.2f} ₽".replace(",", " ")
    except (ValueError, TypeError):
        return f"{value} ₽"


_STATUS_LABELS = {
    "pending": "Ожидает",
    "paid": "Оплачен",
    "completed": "Завершён",
    "failed": "Ошибка",
    "cancelled": "Отменён",
    "refunded": "Возврат",
    "expired": "Истёк",
}


def status_text_filter(value):
    """Конвертирует статус платежа в читаемый текст"""
    if value is None:
        return ""

    if hasattr(value, "value"):
        raw = str(value.value)
    else:
        raw = str(value)

    key = raw.lower()
    return _STATUS_LABELS.get(key, raw)


def pretty_json_filter(value):
    """Форматирует JSON в читаемый вид"""
    import json
    if value is None:
        return ""
    try:
        if isinstance(value, str):
            # Пытаемся распарсить как JSON
            parsed = json.loads(value)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        else:
            # Если это уже объект, просто форматируем
            return json.dumps(value, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        # Если не JSON, возвращаем как есть, но обрезаем длину
        str_value = str(value)
        if len(str_value) > 500:
            return str_value[:500] + "..."
        return str_value


def safe_truncate_filter(value, length=20):
    """Безопасное обрезание строки с преобразованием в строку"""
    if value is None:
        return ""
    str_value = str(value)
    if len(str_value) <= length:
        return str_value
    return str_value[:length] + "..."


def tojson_filter(value, indent=2):
    """Преобразовать значение в JSON строку (совместимость с Jinja2)"""
    import json
    if value is None:
        return "null"
    try:
        return json.dumps(value, ensure_ascii=False, indent=indent)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False, indent=indent)


def urlencode_filter(value):
    """URL-кодирование строки для использования в query параметрах"""
    if value is None:
        return ""
    return quote(str(value), safe="")


# Добавляем кастомные фильтры
templates.env.filters['timestamp'] = timestamp_filter
templates.env.filters['my_datetime_local'] = my_datetime_local_filter
templates.env.filters['rub'] = rub_filter
templates.env.filters['status_text'] = status_text_filter
templates.env.filters['pretty_json'] = pretty_json_filter
templates.env.filters['safe_truncate'] = safe_truncate_filter
templates.env.filters['tojson'] = tojson_filter
templates.env.filters['urlencode'] = urlencode_filter


def get_templates() -> Jinja2Templates:
    """Получить объект шаблонов"""
    return templates


def add_csp_nonce_to_context(request: Request) -> dict:
    """Добавляет CSP nonce в контекст шаблонов"""
    csp_nonce = getattr(request.state, 'csp_nonce', None)
    if not csp_nonce:
        import secrets
        csp_nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = csp_nonce
    return {"csp_nonce": csp_nonce}

