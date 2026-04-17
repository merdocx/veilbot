"""Ссылки подписки: основной домен и резервное зеркало (для клиентов с ограниченным доступом к основному хосту)."""

PRIMARY_SUBSCRIPTION_BASE = "https://veil-bird.ru"
MIRROR_SUBSCRIPTION_BASE = "https://veil-bot.ru"


def subscription_primary_url(token: str) -> str:
    return f"{PRIMARY_SUBSCRIPTION_BASE}/api/subscription/{token}"


def subscription_mirror_url(token: str) -> str:
    return f"{MIRROR_SUBSCRIPTION_BASE}/api/subscription/{token}"


def subscription_links_block_markdown(token: str) -> str:
    """Блок текста с основной ссылкой и запасной (на случай недоступности основного домена)."""
    primary = subscription_primary_url(token)
    mirror = subscription_mirror_url(token)
    return (
        "🔗 Ссылка подписки (коснитесь, чтобы скопировать):\n"
        f"`{primary}`\n\n"
        "Если при добавлении ссылки в приложение появляется ошибка, то попробуйте эту ссылку:\n"
        f"`{mirror}`\n\n"
    )


def subscription_mirror_fallback_markdown(token: str) -> str:
    """Текст с резервной ссылкой на то же содержимое подписки через зеркало."""
    mirror = subscription_mirror_url(token)
    return (
        "Если при добавлении ссылки в приложение появляется ошибка, то попробуйте эту ссылку:\n"
        f"`{mirror}`\n\n"
    )
