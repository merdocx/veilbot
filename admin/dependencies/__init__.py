"""
Общие зависимости для админки
"""
from .csrf import get_csrf_token, validate_csrf_token, generate_csrf_token
from .templates import get_templates

__all__ = [
    'get_csrf_token',
    'validate_csrf_token',
    'generate_csrf_token',
    'get_templates'
]

