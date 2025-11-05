"""
Обработчики сообщений и callback-запросов бота
"""
from .start import register_start_handler
from .keys import register_keys_handler
from .purchase import register_purchase_handlers
from .renewal import register_renewal_handlers

__all__ = [
    'register_start_handler',
    'register_keys_handler',
    'register_purchase_handlers',
    'register_renewal_handlers'
]

