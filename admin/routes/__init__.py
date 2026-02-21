"""
Routes для админки VeilBot
"""
from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .tariffs import router as tariffs_router
from .servers import router as servers_router
from .users import router as users_router
from .keys import router as keys_router
from .payments import router as payments_router
from .webhooks import router as webhooks_router
from .cleanup import router as cleanup_router
from .subscriptions import router as subscriptions_router
from .tools import router as tools_router

__all__ = [
    'auth_router',
    'dashboard_router',
    'tariffs_router',
    'servers_router',
    'users_router',
    'keys_router',
    'payments_router',
    'webhooks_router',
    'cleanup_router',
    'subscriptions_router',
    'tools_router',
]

