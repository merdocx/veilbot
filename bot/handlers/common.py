"""
Обработчики общих команд: помощь, поддержка, рассылка, приглашение друга
"""
import asyncio
import logging
from typing import Dict, Optional
from aiogram import Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_ID, SUPPORT_USERNAME
from utils import get_db_cursor
from bot.core import get_bot_instance
from bot.keyboards import get_main_menu, get_help_keyboard
from bot_error_handler import BotErrorHandler

# Временное хранилище для текстов рассылки
broadcast_texts: Dict[int, str] = {}


async def handle_invite_friend(message: types.Message) -> None:
    """Обработчик кнопки 'Получить месяц бесплатно'"""
    logging.debug(f"handle_invite_friend called: user_id={message.from_user.id}")
    user_id = message.from_user.id
    bot = get_bot_instance()
    if not bot:
        await message.answer("Ошибка: бот не инициализирован", reply_markup=get_main_menu())
        return
    
    try:
        bot_username = (await bot.get_me()).username
        invite_link = f"https://t.me/{bot_username}?start={user_id}"
        await message.answer(
            f"Пригласите друга по этой ссылке:\n{invite_link}\n\nЕсли друг купит доступ, вы получите месяц бесплатно!",
            reply_markup=get_main_menu()
        )
    except Exception as e:
        await BotErrorHandler.handle_error(message, e, "handle_invite_friend", bot, ADMIN_ID)


async def handle_help(message: types.Message) -> None:
    """Обработчик команды 'Помощь'"""
    help_keyboard = get_help_keyboard()
    help_text = (
        "Если VPN не работает:\n"
        "- возможно был заблокирован сервер, поможет перевыпуск ключа;\n"
        "- сломалось приложение, поможет его смена.\n\n"
        "Оплаченный срок действия ключа сохранится!\n\n"
        "Выберите вариант ниже:"
    )
    await message.answer(help_text, reply_markup=help_keyboard)


async def handle_support(message: types.Message) -> None:
    """Обработчик кнопки связи с поддержкой"""
    help_keyboard = get_help_keyboard()
    
    if SUPPORT_USERNAME:
        # Убираем @ если пользователь добавил его
        username = SUPPORT_USERNAME.lstrip('@')
        support_text = (
            f"💬 Напишите нашему специалисту поддержки:\n"
            f"@{username}\n\n"
            f"Мы поможем решить любую проблему!"
        )
        support_button = InlineKeyboardMarkup()
        support_button.add(InlineKeyboardButton(
            "💬 Написать в поддержку",
            url=f"https://t.me/{username}?start"
        ))
        await message.answer(support_text, reply_markup=support_button)
    else:
        await message.answer(
            "❌ Контакт поддержки не настроен.\n"
            "Обратитесь к администратору бота.",
            reply_markup=help_keyboard
        )


async def handle_help_back(message: types.Message) -> None:
    """Обработчик возврата из помощи в главное меню"""
    main_menu = get_main_menu()
    await message.answer("Главное меню:", reply_markup=main_menu)


async def broadcast_message(message_text: str, admin_id: Optional[int] = None) -> None:
    """
    Функция для рассылки сообщений всем пользователям бота
    
    Args:
        message_text (str): Текст сообщения для рассылки
        admin_id (int): ID администратора для уведомлений о результатах рассылки
    """
    bot = get_bot_instance()
    success_count = 0
    failed_count = 0
    total_users = 0
    
    try:
        # Получаем всех пользователей из таблицы users
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT user_id FROM users 
                WHERE blocked = 0
                ORDER BY user_id
            """)
            user_ids = [row[0] for row in cursor.fetchall()]
            total_users = len(user_ids)
        
        if total_users == 0:
            if admin_id:
                await bot.send_message(admin_id, "❌ Нет пользователей для рассылки")
            return
        
        # Отправляем сообщение каждому пользователю
        for user_id in user_ids:
            try:
                await bot.send_message(user_id, message_text, parse_mode='Markdown')
                success_count += 1
                # Небольшая задержка, чтобы не превысить лимиты Telegram
                await asyncio.sleep(0.05)
            except Exception as e:
                failed_count += 1
                logging.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
                continue
        
        # Отправляем отчет администратору
        if admin_id:
            report = (
                f"📊 *Отчет о рассылке*\n\n"
                f"✅ Успешно отправлено: {success_count}\n"
                f"❌ Ошибок: {failed_count}\n"
                f"📈 Всего пользователей: {total_users}\n"
                f"📊 Процент успеха: {(success_count/total_users*100):.1f}%"
            )
            await bot.send_message(admin_id, report, parse_mode='Markdown')
            
    except Exception as e:
        error_msg = f"❌ Ошибка при рассылке: {e}"
        logging.error(error_msg, exc_info=True)
        if admin_id:
            try:
                await bot.send_message(admin_id, error_msg)
            except Exception as send_error:
                logging.error(f"Failed to send error notification to admin: {send_error}")


async def handle_broadcast_command(message: types.Message) -> None:
    """
    Обработчик команды /broadcast для администратора
    Использование: /broadcast <текст сообщения>
    """
    # Проверяем, что команда отправлена администратором
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав для выполнения этой команды")
        return
    
    # Получаем текст сообщения (убираем команду /broadcast)
    command_parts = message.text.split(' ', 1)
    if len(command_parts) < 2:
        await message.answer(
            "❌ Неверный формат команды\n"
            "Использование: /broadcast <текст сообщения>\n\n"
            "Пример:\n"
            "/broadcast 🔔 Важное обновление! Добавлены новые серверы."
        )
        return
    
    broadcast_text = command_parts[1]
    
    # Сохраняем текст в временное хранилище
    text_hash = hash(broadcast_text)
    broadcast_texts[text_hash] = broadcast_text
    
    # Подтверждение рассылки
    confirm_keyboard = InlineKeyboardMarkup()
    confirm_keyboard.add(
        InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_broadcast:{text_hash}"),
        InlineKeyboardButton("❌ Отменить", callback_data="cancel_broadcast")
    )
    
    await message.answer(
        f"📢 *Предварительный просмотр рассылки:*\n\n"
        f"{broadcast_text}\n\n"
        f"⚠️ Это сообщение будет отправлено всем пользователям бота!",
        reply_markup=confirm_keyboard,
        parse_mode='Markdown'
    )


async def handle_confirm_broadcast(callback_query: types.CallbackQuery) -> None:
    """Обработчик подтверждения рассылки"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("❌ У вас нет прав для выполнения этой команды")
        return
    
    # Получаем хеш сообщения из callback_data
    message_hash = int(callback_query.data.split(":")[1])
    
    # Получаем оригинальный текст из временного хранилища
    original_text = broadcast_texts.get(message_hash)
    if not original_text:
        await callback_query.answer("❌ Ошибка: текст рассылки не найден")
        return
    
    await callback_query.message.edit_text(
        "📤 *Рассылка запущена...*\n\n"
        "⏳ Пожалуйста, подождите. Отчет будет отправлен по завершении.",
        parse_mode='Markdown'
    )
    
    # Запускаем рассылку с оригинальным текстом
    await broadcast_message(original_text, ADMIN_ID)
    
    # Очищаем временное хранилище
    del broadcast_texts[message_hash]


async def handle_cancel_broadcast(callback_query: types.CallbackQuery) -> None:
    """Обработчик отмены рассылки"""
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("❌ У вас нет прав для выполнения этой команды")
        return
    
    await callback_query.message.edit_text("❌ Рассылка отменена")
    await callback_query.answer()


def register_common_handlers(dp: Dispatcher) -> None:
    """
    Регистрирует обработчики общих команд
    
    Args:
        dp: Экземпляр Dispatcher
    """
    # Регистрация обработчиков помощи и поддержки
    @dp.message_handler(lambda m: m.text == "Помощь")
    async def help_handler(message: types.Message):
        await handle_help(message)
    
    @dp.message_handler(lambda m: m.text == "💬 Связаться с поддержкой")
    async def support_handler(message: types.Message):
        await handle_support(message)
    
    # Регистрация обработчика возврата из помощи
    help_keyboard = get_help_keyboard()
    @dp.message_handler(lambda m: m.text == "🔙 Назад" and m.reply_markup == help_keyboard)
    async def help_back_handler(message: types.Message):
        await handle_help_back(message)
    
    # Регистрация обработчиков рассылки
    @dp.message_handler(commands=["broadcast"])
    async def broadcast_command_handler(message: types.Message):
        await handle_broadcast_command(message)
    
    @dp.callback_query_handler(lambda c: c.data.startswith("confirm_broadcast:"))
    async def confirm_broadcast_handler(callback_query: types.CallbackQuery):
        await handle_confirm_broadcast(callback_query)
    
    @dp.callback_query_handler(lambda c: c.data == "cancel_broadcast")
    async def cancel_broadcast_handler(callback_query: types.CallbackQuery):
        await handle_cancel_broadcast(callback_query)
    
    # Регистрация обработчика кнопки "Получить месяц бесплатно"
    @dp.message_handler(lambda m: m.text == "Получить месяц бесплатно")
    async def invite_friend_handler(message: types.Message):
        await handle_invite_friend(message)

