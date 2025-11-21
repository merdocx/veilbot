"""
Маршруты для административных инструментов
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ..middleware.audit import log_admin_action
from ..dependencies.templates import templates
from ..services.compare_keys_service import compare_servers, ComparisonResult

router = APIRouter()


@router.get("/tools/compare-keys")
async def compare_keys_page(request: Request):
    """Страница сравнения ключей между БД и серверами"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login", status_code=303)
    
    log_admin_action(request, "COMPARE_KEYS_ACCESS")
    
    # Выполняем сравнение
    try:
        results = await compare_servers()
    except Exception as e:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error_title": "Ошибка при сравнении ключей",
                "error_message": str(e),
            },
        )
    
    # Подготавливаем данные для отображения
    formatted_results = []
    for result in results:
        server_info = {
            "id": result.server.id,
            "name": result.server.name,
            "protocol": result.server.protocol,
            "country": result.server.country,
            "db_count": result.db_count,
            "remote_count": result.remote_count,
            "has_errors": bool(result.errors),
            "errors": result.errors,
            "missing_on_server_count": len(result.missing_on_server),
            "missing_in_db_count": len(result.missing_in_db),
            "db_without_remote_id_count": len(result.db_without_remote_id),
            "is_synced": (
                not result.errors
                and not result.missing_on_server
                and not result.missing_in_db
                and not result.db_without_remote_id
            ),
        }
        
        # Ограничиваем количество записей для отображения (первые 50)
        server_info["missing_on_server"] = result.missing_on_server[:50]
        server_info["missing_in_db"] = result.missing_in_db[:50]
        server_info["db_without_remote_id"] = result.db_without_remote_id[:50]
        
        # Флаги для показа полных списков
        server_info["has_more_missing_on_server"] = len(result.missing_on_server) > 50
        server_info["has_more_missing_in_db"] = len(result.missing_in_db) > 50
        server_info["has_more_db_without_remote_id"] = len(result.db_without_remote_id) > 50
        
        formatted_results.append(server_info)
    
    return templates.TemplateResponse(
        "tools/compare_keys.html",
        {
            "request": request,
            "results": formatted_results,
            "total_servers": len(formatted_results),
            "synced_servers": sum(1 for r in formatted_results if r["is_synced"]),
        },
    )


@router.get("/tools/broadcast")
async def broadcast_page(request: Request):
    """Страница рассылки сообщений пользователям"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login", status_code=303)
    
    log_admin_action(request, "BROADCAST_PAGE_ACCESS")
    
    return templates.TemplateResponse(
        "tools/broadcast.html",
        {
            "request": request,
            "error": None,
            "success": None,
        },
    )


@router.post("/tools/broadcast")
async def broadcast_send(
    request: Request,
    message_text: str = Form(...),
):
    """Отправить рассылку пользователям"""
    if not request.session.get("admin_logged_in"):
        return RedirectResponse("/login", status_code=303)
    
    if not message_text or not message_text.strip():
        return templates.TemplateResponse(
            "tools/broadcast.html",
            {
                "request": request,
                "error": "Текст сообщения не может быть пустым",
                "success": None,
                "message_text": message_text,
            },
        )
    
    log_admin_action(request, "BROADCAST_SEND", details=f"Message length: {len(message_text)}")
    
    try:
        result = await send_broadcast(message_text.strip())
        
        if result["success"]:
            return templates.TemplateResponse(
                "tools/broadcast.html",
                {
                    "request": request,
                    "error": None,
                    "success": result["message"],
                    "message_text": "",
                },
            )
        else:
            return templates.TemplateResponse(
                "tools/broadcast.html",
                {
                    "request": request,
                    "error": result.get("error", "Неизвестная ошибка"),
                    "success": None,
                    "message_text": message_text,
                },
            )
    except Exception as e:
        return templates.TemplateResponse(
            "tools/broadcast.html",
            {
                "request": request,
                "error": f"Ошибка при отправке рассылки: {str(e)}",
                "success": None,
                "message_text": message_text,
            },
        )

