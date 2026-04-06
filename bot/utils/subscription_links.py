"""Ссылки подписки: основной домен и резервное зеркало (для клиентов с ограниченным доступом к основному хосту)."""

PRIMARY_SUBSCRIPTION_BASE = "https://veil-bot.ru"
MIRROR_SUBSCRIPTION_BASE = "https://veil-bird.ru"


def subscription_mirror_url(token: str) -> str:
    return f"{MIRROR_SUBSCRIPTION_BASE}/api/subscription/{token}"


def subscription_mirror_fallback_markdown(token: str) -> str:
    """Текст с резервной ссылкой на то же содержимое подписки через зеркало."""
    mirror = subscription_mirror_url(token)
    return (
        "Если при добавлении этой ссылки в приложение появляется ошибка, то попробуйте эту:\n"
        f"`{mirror}`\n\n"
    )
