"""
Централизованная обработка ошибок для бота VeilBot
"""
import logging
import traceback
from typing import Optional
from aiogram import types
from bot.services.admin_notifications import (
    AdminNotificationCategory,
    format_bot_error_markdown,
    format_bot_error_plain,
    send_admin_message,
)

logger = logging.getLogger(__name__)


class BotErrorHandler:
    """Централизованный обработчик ошибок для бота"""
    
    def __init__(self, bot, admin_id: int):
        self.bot = bot
        self.admin_id = admin_id
        
        # Регистрируем глобальный обработчик ошибок
        self._register_error_handlers()
    
    def _register_error_handlers(self):
        """Регистрирует обработчики ошибок в диспетчере"""
        from aiogram.dispatcher import Dispatcher
        dp = Dispatcher.get_current()
        
        if dp:
            # Обработчик всех исключений
            dp.errors_handler.register(self._handle_exception)
    
    async def _handle_exception(self, update: types.Update, exception: Exception):
        """Глобальный обработчик всех исключений"""
        try:
            # Получаем информацию о пользователе
            user_id = None
            username = None
            context = "unknown"
            
            if update.message:
                user_id = update.message.from_user.id
                username = update.message.from_user.username
                context = f"message_handler: {update.message.text[:50]}"
            elif update.callback_query:
                user_id = update.callback_query.from_user.id
                username = update.callback_query.from_user.username
                context = f"callback_handler: {update.callback_query.data}"
            
            # Логируем ошибку
            error_msg = f"Error in {context}"
            if user_id:
                error_msg += f" (user_id: {user_id}"
                if username:
                    error_msg += f", username: @{username}"
                error_msg += ")"
            
            logger.error(
                error_msg,
                exc_info=True,
                extra={
                    "user_id": user_id,
                    "username": username,
                    "exception_type": type(exception).__name__,
                    "exception_message": str(exception)
                }
            )
            
            # Отправляем уведомление админу
            await self._notify_admin(error_msg, exception, user_id, username)
            
            # Отправляем сообщение пользователю
            user_message = await self._get_user_message(exception)
            if user_id:
                try:
                    if update.message:
                        await update.message.answer(user_message, reply_markup=self._get_main_menu())
                    elif update.callback_query:
                        await update.callback_query.message.answer(user_message, reply_markup=self._get_main_menu())
                        await update.callback_query.answer("Произошла ошибка", show_alert=True)
                except Exception as send_error:
                    logger.error(f"Failed to send error message to user: {send_error}")
            
            return True  # Ошибка обработана
            
        except Exception as handler_error:
            logger.critical(f"Error in error handler itself: {handler_error}", exc_info=True)
            return True
    
    async def _notify_admin(self, context: str, exception: Exception, user_id: Optional[int], username: Optional[str]):
        """Отправляет уведомление админу об ошибке"""
        try:
            traceback_str = traceback.format_exc()[:1000]
            md = format_bot_error_markdown(
                context=context,
                exception=exception,
                user_id=user_id,
                username=username,
                traceback_str=traceback_str,
            )
            plain = format_bot_error_plain(
                context=context,
                exception=exception,
                user_id=user_id,
                username=username,
                traceback_str=traceback_str,
            )
            await send_admin_message(
                md,
                text_plain=plain,
                admin_id=self.admin_id,
                category=AdminNotificationCategory.ERROR,
            )
        except Exception as e:
            logger.error(f"Failed to notify admin about error: {e}")
    
    async def _get_user_message(self, exception: Exception) -> str:
        """Возвращает понятное сообщение для пользователя"""
        exception_type = type(exception).__name__
        
        # Специальные сообщения для известных типов ошибок
        if "ValidationError" in exception_type:
            return f"❌ Ошибка валидации: {str(exception)}"
        
        if "Timeout" in exception_type or "timeout" in str(exception).lower():
            return "⏱️ Превышено время ожидания. Попробуйте еще раз."
        
        if "Connection" in exception_type or "connection" in str(exception).lower():
            return "🔌 Проблемы с подключением. Попробуйте позже."
        
        if "NotFound" in exception_type or "not found" in str(exception).lower():
            return "❌ Запрашиваемый ресурс не найден. Попробуйте позже."
        
        # Общее сообщение
        return (
            "❌ Произошла ошибка при обработке запроса.\n\n"
            "Попробуйте еще раз или обратитесь в поддержку, если проблема сохраняется."
        )
    
    def _get_main_menu(self):
        """Возвращает главное меню"""
        from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
        menu = ReplyKeyboardMarkup(resize_keyboard=True)
        menu.add(KeyboardButton("Получить доступ"))
        menu.add(KeyboardButton("Мои ключи"))
        menu.add(KeyboardButton("Получить месяц бесплатно"))
        menu.add(KeyboardButton("Помощь"))
        return menu
    
    @staticmethod
    async def handle_error(
        message: types.Message,
        error: Exception,
        context: str,
        bot=None,
        admin_id: Optional[int] = None
    ):
        """
        Статический метод для обработки ошибок в обработчиках
        
        Args:
            message: Telegram сообщение
            error: Исключение
            context: Контекст ошибки (например, "handle_purchase")
            bot: Экземпляр бота (опционально)
            admin_id: ID админа (опционально)
        """
        user_id = message.from_user.id
        username = message.from_user.username
        
        # Логируем ошибку
        logger.error(
            f"Error in {context}: {error}",
            exc_info=True,
            extra={
                "user_id": user_id,
                "username": username,
                "context": context,
                "exception_type": type(error).__name__
            }
        )
        
        # Отправляем уведомление админу (бот или HTTP fallback)
        if admin_id:
            try:
                traceback_str = traceback.format_exc()[:1000]
                md = format_bot_error_markdown(
                    context=context,
                    exception=error,
                    user_id=user_id,
                    username=username,
                    traceback_str=traceback_str,
                )
                plain = format_bot_error_plain(
                    context=context,
                    exception=error,
                    user_id=user_id,
                    username=username,
                    traceback_str=traceback_str,
                )
                await send_admin_message(
                    md,
                    text_plain=plain,
                    admin_id=admin_id,
                    category=AdminNotificationCategory.ERROR,
                )
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
        
        # Отправляем сообщение пользователю
        user_message = await BotErrorHandler._get_user_message_static(error)
        try:
            from bot import main_menu
            await message.answer(user_message, reply_markup=main_menu)
        except Exception as e:
            logger.debug("Send with main_menu failed, sending without keyboard: %s", e)
            await message.answer(user_message)
    
    @staticmethod
    async def _get_user_message_static(exception: Exception) -> str:
        """Статический метод для получения сообщения пользователю"""
        exception_type = type(exception).__name__
        
        if "ValidationError" in exception_type:
            return f"❌ Ошибка валидации: {str(exception)}"
        
        if "Timeout" in exception_type or "timeout" in str(exception).lower():
            return "⏱️ Превышено время ожидания. Попробуйте еще раз."
        
        if "Connection" in exception_type or "connection" in str(exception).lower():
            return "🔌 Проблемы с подключением. Попробуйте позже."
        
        return (
            "❌ Произошла ошибка при обработке запроса.\n\n"
            "Попробуйте еще раз или обратитесь в поддержку, если проблема сохраняется."
        )


def setup_error_handler(bot, admin_id: int):
    """Настройка обработчика ошибок для бота"""
    return BotErrorHandler(bot, admin_id)

