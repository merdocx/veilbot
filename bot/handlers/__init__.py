"""
Обработчики сообщений и callback-запросов бота
"""
from .start import register_start_handler
from .keys import register_keys_handler

__all__ = [
    'register_start_handler',
    'register_keys_handler'
]

