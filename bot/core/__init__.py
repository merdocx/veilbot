"""
Централизованное управление bot instance для устранения циклических зависимостей

Этот модуль позволяет получать bot instance из любого места в проекте
без создания циклических зависимостей.

Для обратной совместимости реэкспортирует функции из bot.core.state
"""
from bot.core.state import (
    set_bot_instance,
    get_bot_instance,
    set_dp_instance,
    get_dp_instance,
    get_user_states,
    clear_user_state,
    set_user_state,
    get_user_state,
)

# Для обратной совместимости
def is_bot_registered() -> bool:
    """Проверяет, зарегистрирован ли bot instance"""
    from bot.core.state import get_bot_instance
    return get_bot_instance() is not None


