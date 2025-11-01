"""
Настройка шаблонов Jinja2
"""
import os
from starlette.templating import Jinja2Templates

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def get_templates() -> Jinja2Templates:
    """Получить объект шаблонов"""
    return templates

