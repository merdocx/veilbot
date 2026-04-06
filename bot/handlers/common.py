"""
Обработчики общих команд: помощь, поддержка, рассылка, приглашение друга
"""
import asyncio
import logging
import time
from typing import Dict, Optional, Set
from pathlib import Path
from aiogram import Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from config import ADMIN_ID, SUPPORT_USERNAME
from app.infra.sqlite_utils import get_db_cursor
from bot.core import get_bot_instance
from bot.keyboards import get_main_menu, get_help_keyboard
from bot_error_handler import BotErrorHandler
from bot.utils import safe_send_message
from bot.services.admin_notifications import (
    AdminNotificationCategory,
    format_broadcast_report_markdown,
    format_broadcast_report_plain,
    send_admin_message,
)

# Временное хранилище для текстов рассылки
broadcast_texts: Dict[int, str] = {}
help_menu_users: Set[int] = set()
APPLE_TV_GUIDE_IMAGE_PATH = Path("bot/static/images/apple_tv_shadowrocket.png")
SHADOWROCKET_APP_URL = "https://apps.apple.com/app/shadowrocket/id932747118"


async def handle_invite_friend(message: types.Message) -> None:
    """Обработчик кнопки 'Получить месяц бесплатно'"""
    logging.debug(f"handle_invite_friend called: user_id={message.from_user.id}")
    user_id = message.from_user.id
    bot = get_bot_instance()
    if not bot:
        await message.answer("Ошибка: бот не инициализирован", reply_markup=get_main_menu(user_id))
        return
    
    try:
        bot_username = (await bot.get_me()).username
        invite_link = f"https://t.me/{bot_username}?start={user_id}"
        await message.answer(
            f"Пригласите друга по этой ссылке:\n{invite_link}\n\nЕсли друг купит доступ, вы получите месяц бесплатно!",
            reply_markup=get_main_menu(user_id),
            disable_web_page_preview=True
        )
    except Exception as e:
        await BotErrorHandler.handle_error(message, e, "handle_invite_friend", bot, ADMIN_ID)


async def handle_help(message: types.Message) -> None:
    """Обработчик команды 'Помощь'"""
    help_keyboard = get_help_keyboard()
    help_text = (
        "В данном меню вы можете:\n\n"
        "• Получить инструкцию по подключению подписки к Apple TV\n"
        "• Связаться с поддержкой для решения любых вопросов\n\n"
        "Выберите вариант ниже:"
    )
    help_menu_users.add(message.from_user.id)
    await message.answer(help_text, reply_markup=help_keyboard)


async def handle_support(message: types.Message) -> None:
    """Обработчик кнопки связи с поддержкой"""
    help_keyboard = get_help_keyboard()
    
    # Используем @vee_support как контакт поддержки
    username = "vee_support"
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


async def handle_help_back(message: types.Message) -> None:
    """Обработчик возврата из помощи в главное меню"""
    user_id = message.from_user.id
    main_menu = get_main_menu(user_id)
    help_menu_users.discard(user_id)
    await message.answer("Главное меню:", reply_markup=main_menu)


AUDIENCE_LABELS = {
    "all_started": "всем, кто нажал /start",
    "has_subscription": "всем с активной подпиской",
    "started_without_subscription": "нажали /start, но без подписки",
}


async def broadcast_message(
    message_text: str,
    admin_id: Optional[int] = None,
    audience: str = "all_started",
) -> None:
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
        audience_value = audience if audience in AUDIENCE_LABELS else "all_started"
        now_ts = int(time.time())
        
        # Получаем пользователей согласно выбранной аудитории
        with get_db_cursor() as cursor:
            if audience_value == "has_subscription":
                cursor.execute(
                    """
                    SELECT DISTINCT u.user_id
                    FROM users u
                    JOIN subscriptions s ON s.user_id = u.user_id
                    WHERE u.blocked = 0 AND s.is_active = 1 AND s.expires_at > ?
                    ORDER BY u.user_id
                    """,
                    (now_ts,),
                )
            elif audience_value == "started_without_subscription":
                cursor.execute(
                    """
                    SELECT u.user_id
                    FROM users u
                    LEFT JOIN subscriptions s 
                        ON s.user_id = u.user_id
                        AND s.is_active = 1
                        AND s.expires_at > ?
                    WHERE u.blocked = 0 AND s.user_id IS NULL
                    ORDER BY u.user_id
                    """,
                    (now_ts,),
                )
            else:
                cursor.execute(
                    """
                    SELECT user_id FROM users 
                    WHERE blocked = 0
                    ORDER BY user_id
                    """
                )
            user_ids = [row[0] for row in cursor.fetchall()]
            total_users = len(user_ids)
        
        if total_users == 0:
            if admin_id:
                empty_msg = "❌ Нет пользователей для рассылки"
                await send_admin_message(
                    empty_msg,
                    text_plain=empty_msg,
                    admin_id=admin_id,
                    category=AdminNotificationCategory.BROADCAST_REPORT,
                )
            return
        
        # Отправляем сообщение каждому пользователю
        for user_id in user_ids:
            try:
                await safe_send_message(bot, user_id, message_text, parse_mode='Markdown')
                success_count += 1
                # Небольшая задержка, чтобы не превысить лимиты Telegram
                await asyncio.sleep(0.05)
            except Exception as e:
                failed_count += 1
                logging.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
                continue
        
        # Отправляем отчет администратору
        if admin_id:
            audience_label = AUDIENCE_LABELS.get(audience_value, AUDIENCE_LABELS["all_started"])
            report_md = format_broadcast_report_markdown(
                success_count=success_count,
                failed_count=failed_count,
                total_users=total_users,
                audience_label=audience_label,
            )
            report_plain = format_broadcast_report_plain(
                success_count=success_count,
                failed_count=failed_count,
                total_users=total_users,
                audience_label=audience_label,
            )
            await send_admin_message(
                report_md,
                text_plain=report_plain,
                admin_id=admin_id,
                category=AdminNotificationCategory.BROADCAST_REPORT,
            )

    except Exception as e:
        error_msg = f"❌ Ошибка при рассылке: {e}"
        logging.error(error_msg, exc_info=True)
        if admin_id:
            await send_admin_message(
                error_msg,
                text_plain=error_msg,
                admin_id=admin_id,
                category=AdminNotificationCategory.BROADCAST_REPORT,
            )


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


async def back_to_main(message: types.Message, user_states: dict) -> None:
    """
    Обработчик кнопки "🔙 Назад" - возврат в главное меню
    
    Args:
        message: Telegram сообщение
        user_states: Словарь состояний пользователей
    """
    # Clear any existing state
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    await message.answer("Главное меню:", reply_markup=get_main_menu(user_id))


async def handle_migrate_to_subscription(message: types.Message) -> None:
    """Обработчик кнопки 'Перейти на подписку'"""
    user_id = message.from_user.id
    bot = get_bot_instance()
    help_keyboard = get_help_keyboard()
    
    if not bot:
        await message.answer(
            "Ошибка: бот не инициализирован",
            reply_markup=help_keyboard
        )
        return
    
    try:
        from app.repositories.subscription_repository import SubscriptionRepository
        from bot.services.subscription_migration import migrate_user_to_subscription
        from vpn_protocols import format_duration
        import time
        
        # Проверка наличия активной подписки
        repo = SubscriptionRepository()
        active_subscription = repo.get_active_subscription(user_id)
        
        if active_subscription:
            await message.answer(
                "У вас уже есть активная подписка.",
                reply_markup=help_keyboard
            )
            return
        
        # Проверка наличия ключей V2Ray с активной подпиской
        now = int(time.time())
        has_keys = False
        
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) FROM v2ray_keys k
                JOIN subscriptions s ON k.subscription_id = s.id
                WHERE k.user_id = ? AND s.expires_at > ?
            """, (user_id, now))
            has_keys = cursor.fetchone()[0] > 0
        
        if not has_keys:
            await message.answer(
                'У вас отсутствует доступ, нажмите "получить доступ".',
                reply_markup=help_keyboard
            )
            return
        
        # Отправляем сообщение о начале процесса
        await message.answer(
            "⏳ Создание подписки...\n\n"
            "Пожалуйста, подождите.",
            reply_markup=help_keyboard
        )
        
        # Выполняем миграцию
        result = await migrate_user_to_subscription(user_id)
        
        if not result['success']:
            error_msg = "❌ Не удалось создать подписку."
            if result['errors']:
                error_msg += f"\n\nОшибки:\n" + "\n".join(result['errors'])
            await message.answer(error_msg, reply_markup=help_keyboard)
            return
        
        # Форматируем срок действия
        expires_at = result['expires_at']
        if expires_at:
            from datetime import datetime
            expiry_date = datetime.fromtimestamp(expires_at).strftime("%d.%m.%Y %H:%M")
            now_ts = int(time.time())
            remaining_sec = expires_at - now_ts
            duration_text = format_duration(remaining_sec)
        else:
            duration_text = "не определен"
            expiry_date = "не определен"
        
        # Формируем сообщение об успехе
        subscription_url = f"https://veil-bot.ru/api/subscription/{result['subscription_token']}"
        
        success_msg = (
            f"✅ *Подписка V2Ray успешно создана!*\n\n"
            f"🔗 *Ссылка подписки:*\n"
            f"`{subscription_url}`\n\n"
            f"⏳ *Срок действия:* {duration_text}\n"
            f"📅 *До:* {expiry_date}\n\n"
            f"💡 *Как использовать:*\n"
            f"1. Откройте приложение V2Ray\n"
            f"2. Нажмите \"+\" → \"Импорт подписки\"\n"
            f"3. Вставьте ссылку выше\n"
            f"4. Все серверы будут добавлены автоматически"
        )
        
        # Добавляем предупреждения о частичных ошибках
        if result['errors']:
            success_msg += "\n\n⚠️ *Предупреждения:*\n" + "\n".join(result['errors'])
        
        await message.answer(
            success_msg,
            reply_markup=help_keyboard,
            disable_web_page_preview=True,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logging.error(f"Error in handle_migrate_to_subscription: {e}", exc_info=True)
        await BotErrorHandler.handle_error(message, e, "handle_migrate_to_subscription", bot, ADMIN_ID)


async def handle_apple_tv_instruction(message: types.Message) -> None:
    """Отправляет инструкцию по подключению Shadowrocket к Apple TV"""
    help_keyboard = get_help_keyboard()
    caption = (
        "📺 *Инструкция по подключению к Apple TV*\n\n"
        "Скачайте на iPhone приложение Shadowrocket и следуйте инструкции с картинки.\n\n"
        f"🔗 [Скачать Shadowrocket]({SHADOWROCKET_APP_URL})"
    )
    
    if APPLE_TV_GUIDE_IMAGE_PATH.exists():
        await message.answer_photo(
            InputFile(APPLE_TV_GUIDE_IMAGE_PATH),
            caption=caption,
            parse_mode="Markdown",
            reply_markup=help_keyboard
        )
    else:
        logging.warning(
            "Apple TV guide image not found at %s", APPLE_TV_GUIDE_IMAGE_PATH
        )
        await message.answer(
            caption + "\n\n⚠️ Изображение временно недоступно.",
            parse_mode="Markdown",
            reply_markup=help_keyboard
        )


def register_common_handlers(dp: Dispatcher, user_states: dict) -> None:
    """
    Регистрирует обработчики общих команд
    
    Args:
        dp: Экземпляр Dispatcher
        user_states: Словарь состояний пользователей
    """
    # Регистрация обработчиков помощи и поддержки
    @dp.message_handler(lambda m: m.text == "Помощь")
    async def help_handler(message: types.Message):
        await handle_help(message)
    
    @dp.message_handler(lambda m: m.text == "Инструкция по подключению к Apple TV")
    async def apple_tv_instruction_handler(message: types.Message):
        await handle_apple_tv_instruction(message)
    
    @dp.message_handler(lambda m: m.text == "💬 Связаться с поддержкой")
    async def support_handler(message: types.Message):
        await handle_support(message)
    
    # Регистрация обработчика возврата из помощи
    help_keyboard = get_help_keyboard()
    @dp.message_handler(lambda m: m.text == "🔙 Назад" and m.from_user.id in help_menu_users)
    async def help_back_handler(message: types.Message):
        await handle_help_back(message)
    
    # Регистрация общего обработчика кнопки "🔙 Назад" (возврат в главное меню)
    @dp.message_handler(lambda m: m.text == "🔙 Назад")
    async def back_handler(message: types.Message):
        await back_to_main(message, user_states)
    
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
    
    # Регистрация обработчика кнопки "Перейти на подписку"
    @dp.message_handler(lambda m: m.text == "Перейти на подписку")
    async def migrate_to_subscription_handler(message: types.Message):
        await handle_migrate_to_subscription(message)
    
    # Регистрация обработчика callback для инлайн кнопки "Перейти на подписку"
    @dp.callback_query_handler(lambda c: c.data == "migrate_to_subscription")
    async def migrate_to_subscription_callback_handler(callback_query: types.CallbackQuery):
        """Обработчик callback от инлайн кнопки 'Перейти на подписку'"""
        await callback_query.answer("Обработка запроса...")
        
        # Используем message из callback_query, если оно есть
        # Если нет, создаем новое сообщение
        if callback_query.message:
            # Меняем текст сообщения, чтобы оно соответствовало обработчику
            callback_query.message.text = "Перейти на подписку"
            await handle_migrate_to_subscription(callback_query.message)
        else:
            # Если message отсутствует, отправляем сообщение пользователю
            bot = get_bot_instance()
            if bot:
                help_keyboard = get_help_keyboard()
                await safe_send_message(
                    bot,
                    callback_query.from_user.id,
                    "❌ Не удалось обработать запрос. Пожалуйста, повторите попытку позже или свяжитесь с поддержкой.",
                    reply_markup=help_keyboard
                )

