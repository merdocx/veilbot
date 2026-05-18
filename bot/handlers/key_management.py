"""
Обработчики управления ключами (перевыпуск)
"""
import time
import logging
from typing import Dict, Any, Callable, List
from aiogram import Dispatcher, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import PROTOCOLS
from app.infra.sqlite_utils import get_db_cursor
from bot.keyboards import get_main_menu
from bot_rate_limiter import rate_limit


# Функции показа меню для управления ключами
async def show_key_selection_menu(
    message: types.Message, 
    user_id: int, 
    keys: List[Dict[str, Any]]
) -> None:
    """
    Показывает меню выбора ключа для перевыпуска
    
    Отображает список доступных ключей пользователя с возможностью выбора
    конкретного ключа для перевыпуска.
    
    Args:
        message: Telegram сообщение для отправки меню
        user_id: ID пользователя
        keys: Список словарей с данными ключей, каждый должен содержать:
            - id: ID ключа в базе данных
            - type: Тип ключа ('v2ray')
            - protocol: Протокол VPN
            - country: Страна сервера
            - expiry_at: Время истечения ключа
            - tariff_id: ID тарифа
    """
    
    # Создаем клавиатуру для выбора ключа
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for i, key in enumerate(keys):
        # Получаем информацию о тарифе
        with get_db_cursor() as cursor:
            cursor.execute("SELECT name FROM tariffs WHERE id = ?", (key['tariff_id'],))
            tariff_result = cursor.fetchone()
            tariff_name = tariff_result[0] if tariff_result else "Неизвестно"
        
        # Форматируем время истечения
        expiry_time = time.strftime('%d.%m.%Y %H:%M', time.localtime(key['expiry_at']))
        
        # Создаем текст кнопки
        protocol_icon = PROTOCOLS[key['protocol']]['icon']
        button_text = f"{protocol_icon} {key['country']} - {tariff_name} (до {expiry_time})"
        
        keyboard.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"reissue_key_{key['type']}_{key['id']}"
        ))
    
    keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="cancel_reissue"))
    
    await message.answer(
        "Выберите ключ для перевыпуска:",
        reply_markup=keyboard
    )


def register_key_management_handlers(
    dp: Dispatcher,
    _: Bot,
    reissue_specific_key: Callable,
) -> None:
    """
    Регистрация обработчиков управления ключами
    
    Args:
        dp: Dispatcher aiogram
        bot: Экземпляр бота
        reissue_specific_key: Функция перевыпуска ключа
    """
    
    @dp.message_handler(lambda m: m.text == "Перевыпустить ключ")
    @rate_limit("reissue")
    async def handle_reissue_key(message: types.Message):
        user_id = message.from_user.id
        now = int(time.time())
        
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT k.id, COALESCE(sub.expires_at, 0) as expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol,
                       'v2ray' as key_type, s.domain, s.v2ray_path, k.traffic_limit_mb,
                       COALESCE(k.panel_total_bytes_observed, 0), k.traffic_over_limit_at, k.traffic_over_limit_notified
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND sub.expires_at > ?
                ORDER BY sub.expires_at DESC
            """, (user_id, now))
            v2ray_keys = cursor.fetchall()

            all_keys = []
            for key in v2ray_keys:
                all_keys.append({
                    'id': key[0],
                    'expiry_at': key[1],
                    'server_id': key[2],
                    'v2ray_uuid': key[3],
                    'country': key[4],
                    'tariff_id': key[5],
                    'email': key[6],
                    'protocol': key[7],
                    'type': key[8],
                    'domain': key[9],
                    'v2ray_path': key[10],
                    'traffic_limit_mb': key[11],
                    'traffic_usage_bytes': key[12],
                    'traffic_over_limit_at': key[13],
                    'traffic_over_limit_notified': key[14],
                })
            
            if not all_keys:
                await message.answer("У вас нет активных ключей для перевыпуска.", reply_markup=get_main_menu(user_id))
                return
            
            if len(all_keys) == 1:
                # Если только один ключ, перевыпускаем его сразу
                await reissue_specific_key(message, user_id, all_keys[0])
            else:
                # Если несколько ключей, показываем список для выбора
                await show_key_selection_menu(message, user_id, all_keys)
    
    @dp.callback_query_handler(lambda c: c.data.startswith("reissue_key_"))
    @rate_limit("reissue")
    async def handle_reissue_key_callback(callback_query: types.CallbackQuery):
        """Обработчик выбора ключа для перевыпуска"""
        user_id = callback_query.from_user.id
        
        # Парсим callback_data: reissue_key_{type}_{id}
        parts = callback_query.data.split("_")
        if len(parts) != 4:
            await callback_query.answer("Ошибка: неверный формат данных")
            return
        
        parts[2]
        key_id = int(parts[3])
        
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT k.id, COALESCE(sub.expires_at, 0) as expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol,
                       s.domain, s.v2ray_path, k.traffic_limit_mb, COALESCE(k.panel_total_bytes_observed, 0),
                       k.traffic_over_limit_at, k.traffic_over_limit_notified
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.id = ? AND k.user_id = ?
            """, (key_id, user_id))

            key_data = cursor.fetchone()
            if not key_data:
                await callback_query.answer("Ключ не найден")
                return

            key_dict = {
                'id': key_data[0],
                'expiry_at': key_data[1],
                'server_id': key_data[2],
                'v2ray_uuid': key_data[3],
                'country': key_data[4],
                'tariff_id': key_data[5],
                'email': key_data[6],
                'protocol': key_data[7],
                'domain': key_data[8],
                'v2ray_path': key_data[9],
                'traffic_limit_mb': key_data[10],
                'traffic_usage_bytes': key_data[11],
                'traffic_over_limit_at': key_data[12],
                'traffic_over_limit_notified': key_data[13],
                'type': 'v2ray',
            }

        # Перевыпускаем ключ
        logging.debug(f"Передаем key_dict в reissue_specific_key: {list(key_dict.keys())}")
        await reissue_specific_key(callback_query.message, user_id, key_dict)
        await callback_query.answer()
    
    @dp.callback_query_handler(lambda c: c.data == "cancel_reissue")
    async def handle_cancel_reissue(callback_query: types.CallbackQuery):
        """Обработчик отмены перевыпуска ключа"""
        await callback_query.message.edit_text("Перевыпуск ключа отменен.")
        await callback_query.answer()
