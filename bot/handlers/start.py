"""
Обработчик команды /start
"""
import time
import logging
from typing import Dict, Any
from aiogram import Dispatcher, types
from utils import get_db_cursor
from bot.keyboards import get_main_menu
from app.infra.foreign_keys import safe_foreign_keys_off

async def handle_start(message: types.Message, user_states: Dict[int, Dict[str, Any]]) -> None:
    """
    Обработчик команды /start
    
    Args:
        message: Telegram сообщение
        user_states: Словарь состояний пользователей
    """
    args = message.get_args()
    user_id = message.from_user.id
    
    # Save or update user in users table
    with get_db_cursor(commit=True) as cursor:
        now = int(time.time())
        username = message.from_user.username
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name
        
        # Временно отключаем проверку foreign keys для INSERT OR REPLACE
        with safe_foreign_keys_off(cursor):
            cursor.execute("""
                INSERT OR REPLACE INTO users 
                (user_id, username, first_name, last_name, created_at, last_active_at, blocked)
                VALUES (?, ?, ?, ?, 
                    COALESCE((SELECT created_at FROM users WHERE user_id = ?), ?), 
                    ?, 0)
            """, (user_id, username, first_name, last_name, user_id, now, now))
    
    # Обработка реферальной ссылки
    if args and args.isdigit() and int(args) != user_id:
        referrer_id = int(args)
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("SELECT 1 FROM referrals WHERE referred_id = ?", (user_id,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                    (referrer_id, user_id, int(time.time()))
                )
    
    # Clear any existing state
    if user_id in user_states:
        del user_states[user_id]
    
    main_menu = get_main_menu()
    await message.answer("Нажмите «Купить доступ» для получения доступа", reply_markup=main_menu)

def register_start_handler(dp: Dispatcher, user_states: Dict[int, Dict[str, Any]]) -> None:
    """Регистрация обработчика команды /start"""
    @dp.message_handler(commands=["start"])
    async def start_handler(message: types.Message):
        await handle_start(message, user_states)
    
    return start_handler

