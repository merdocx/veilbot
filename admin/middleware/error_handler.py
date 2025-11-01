"""
Глобальная обработка ошибок для админки
"""
import logging
from fastapi import Request, status
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.exceptions import RequestValidationError, HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import RedirectResponse

logger = logging.getLogger(__name__)


async def global_exception_handler(request: Request, exc: Exception):
    """
    Глобальный обработчик всех необработанных исключений
    """
    logger.error(
        f"Unhandled exception: {exc}",
        exc_info=True,
        extra={
            "path": request.url.path,
            "method": request.method,
            "client_ip": request.client.host if request.client else "unknown"
        }
    )
    
    # Для API запросов возвращаем JSON
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "message": "Произошла внутренняя ошибка сервера"
            }
        )
    
    # Для HTML запросов редиректим на страницу ошибки или показываем сообщение
    from ..dependencies.templates import templates
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "error_code": 500,
            "error_message": "Внутренняя ошибка сервера"
        },
        status_code=500
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Обработчик HTTP исключений (404, 403, и т.д.)
    """
    status_code = exc.status_code
    
    # Для API запросов возвращаем JSON
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=status_code,
            content={
                "error": exc.detail,
                "status_code": status_code
            }
        )
    
    # Специальная обработка для 403 (Forbidden)
    if status_code == 403:
        # Если не авторизован, редиректим на логин
        if not request.session.get("admin_logged_in"):
            return RedirectResponse(url="/login", status_code=303)
    
    # Для HTML запросов показываем страницу ошибки
    from ..dependencies.templates import templates
    error_messages = {
        404: "Страница не найдена",
        403: "Доступ запрещён",
        500: "Внутренняя ошибка сервера"
    }
    
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "error_code": status_code,
            "error_message": error_messages.get(status_code, exc.detail)
        },
        status_code=status_code
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Обработчик ошибок валидации (Pydantic)
    """
    logger.warning(
        f"Validation error: {exc.errors()}",
        extra={
            "path": request.url.path,
            "method": request.method,
            "client_ip": request.client.host if request.client else "unknown"
        }
    )
    
    # Для API запросов возвращаем детали ошибок валидации
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=422,
            content={
                "error": "Validation error",
                "details": exc.errors()
            }
        )
    
    # Для HTML запросов показываем общее сообщение
    from ..dependencies.templates import templates
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "error_code": 422,
            "error_message": "Ошибка валидации данных"
        },
        status_code=422
    )

