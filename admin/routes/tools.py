"""
Маршруты для административных инструментов
"""
from typing import Dict, Any

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..middleware.audit import log_admin_action
from ..dependencies.templates import templates
from ..services.broadcast_service import send_broadcast

router = APIRouter()

AUDIENCE_OPTIONS = [
    {
        "value": "all_started",
        "title": "Всем, кто нажал /start",
        "description": "Отправить сообщение каждому пользователю, который когда-либо запускал бота и не заблокировал его.",
    },
    {
        "value": "has_subscription",
        "title": "Всем с активной подпиской",
        "description": "Сообщение получат пользователи, у которых сейчас есть активная подписка (не истекла и не отключена).",
    },
    {
        "value": "started_without_subscription",
        "title": "Нажали /start, но без подписки",
        "description": "Для пользователей, которые запускали бота, но у них нет активной подписки — удобно для промо.",
    },
]
AUDIENCE_VALUES = {opt["value"] for opt in AUDIENCE_OPTIONS}
DEFAULT_AUDIENCE = AUDIENCE_OPTIONS[0]["value"]


def _broadcast_context(request: Request, **extra: Any) -> Dict[str, Any]:
    context = {
        "request": request,
        "audience_options": AUDIENCE_OPTIONS,
        "selected_audience": extra.get("selected_audience", DEFAULT_AUDIENCE),
    }
    context.update(extra)
    return context


@router.get("/tools/broadcast")
async def broadcast_page(request: Request):
    """Страница рассылки сообщений пользователям"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login", status_code=303)
    
    log_admin_action(request, "BROADCAST_PAGE_ACCESS")
    
    return templates.TemplateResponse(
        "tools/broadcast.html",
        _broadcast_context(request, error=None, success=None, message_text=""),
    )


@router.post("/tools/broadcast")
async def broadcast_send(
    request: Request,
    message_text: str = Form(...),
    audience: str = Form(DEFAULT_AUDIENCE),
):
    """Отправить рассылку пользователям.

    Запускает рассылку в фоне, чтобы не блокировать HTTP-запрос и не упираться в таймауты nginx/браузера.
    """
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login", status_code=303)
    
    audience_value = audience if audience in AUDIENCE_VALUES else DEFAULT_AUDIENCE
    
    if not message_text or not message_text.strip():
        return templates.TemplateResponse(
            "tools/broadcast.html",
            _broadcast_context(
                request,
                error="Текст сообщения не может быть пустым",
                success=None,
                message_text=message_text,
                selected_audience=audience_value,
            ),
        )
    
    log_admin_action(
        request,
        "BROADCAST_SEND",
        details=f"Message length: {len(message_text)}, audience={audience_value}",
    )

    # Запускаем рассылку в фоне, чтобы не блокировать HTTP-запрос
    try:
        import asyncio

        asyncio.create_task(
            send_broadcast(message_text=message_text.strip(), audience=audience_value)
        )
    except Exception as e:
        # Если даже создание фоновой задачи упало — показываем ошибку
        return templates.TemplateResponse(
            "tools/broadcast.html",
            _broadcast_context(
                request,
                error=f"Ошибка при запуске рассылки: {str(e)}",
                success=None,
                message_text=message_text,
                selected_audience=audience_value,
            ),
        )

    # Немедленно возвращаем ответ: рассылка запущена, отчёт придёт в Telegram
    return templates.TemplateResponse(
        "tools/broadcast.html",
        _broadcast_context(
            request,
            error=None,
            success="Рассылка запущена. Отчёт придёт администратору в Telegram, страницу можно закрыть.",
            message_text="",
            selected_audience=audience_value,
        ),
    )

