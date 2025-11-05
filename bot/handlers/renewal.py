"""
Обработчики продления ключей
"""
import time
import logging
from aiogram import Dispatcher, types
from config import PROTOCOLS
from utils import get_db_cursor
from bot.keyboards import get_main_menu, get_payment_method_keyboard
from bot_rate_limiter import rate_limit

def register_renewal_handlers(
    dp: Dispatcher,
    user_states: dict,
    bot
):
    """
    Регистрация обработчиков продления ключей
    
    Args:
        dp: Dispatcher aiogram
        user_states: Словарь состояний пользователей
        bot: Экземпляр бота
    """
    
    @dp.callback_query_handler(lambda c: c.data == "buy")
    @rate_limit("renew")
    async def callback_buy_button(callback_query: types.CallbackQuery):
        """Обработчик кнопки 'Продлить' - показывает выбор способа платежа (как при покупке)"""
        user_id = callback_query.from_user.id
        now = int(time.time())
        
        # Находим активный ключ пользователя (самый новый по сроку действия)
        with get_db_cursor() as cursor:
            # Проверяем Outline ключи
            cursor.execute("""
                SELECT k.id, k.expiry_at, s.protocol, s.country
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now))
            outline_key = cursor.fetchone()
            
            # Проверяем V2Ray ключи
            cursor.execute("""
                SELECT k.id, k.expiry_at, s.protocol, s.country
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now))
            v2ray_key = cursor.fetchone()
            
            # Выбираем самый новый ключ
            current_key = None
            if outline_key and v2ray_key:
                # Сравниваем по expiry_at
                current_key = outline_key if outline_key[1] > v2ray_key[1] else v2ray_key
            elif outline_key:
                current_key = outline_key
            elif v2ray_key:
                current_key = v2ray_key
        
        if not current_key:
            await callback_query.answer("У вас нет активных ключей для продления", show_alert=True)
            return
        
        # Получаем протокол и страну из найденного ключа
        key_id, expiry_at, protocol, country = current_key
        
        # Устанавливаем состояние для выбора способа платежа (как при покупке)
        user_states[user_id] = {
            "state": "waiting_payment_method_after_country",
            "country": country,
            "protocol": protocol,
            "is_renewal": True,  # Флаг, что это продление
            "paid_only": True
        }
        
        # Показываем выбор способа платежа
        msg = f"💳 *Выберите способ оплаты*\n\n"
        msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
        msg += f"🌍 Страна: *{country}*\n"
        
        await bot.send_message(
            user_id,
            msg,
            reply_markup=get_payment_method_keyboard(),
            parse_mode="Markdown"
        )
        
        try:
            await callback_query.answer()
        except Exception:
            pass

