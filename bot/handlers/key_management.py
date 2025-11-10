"""
Обработчики управления ключами (reissue, protocol change, country change)
"""
import asyncio
import time
import logging
from typing import Dict, Any, Callable
from aiogram import Dispatcher, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from config import PROTOCOLS, ADMIN_ID
from utils import get_db_cursor
from outline import create_key, delete_key
from vpn_protocols import ProtocolFactory
from bot.keyboards import get_main_menu, get_country_menu, get_countries_by_protocol
from bot.utils import format_key_message_unified
from bot_error_handler import BotErrorHandler
from bot_rate_limiter import rate_limit

def register_key_management_handlers(
    dp: Dispatcher,
    bot: Bot,
    user_states: Dict[int, Dict[str, Any]],
    change_country_for_key: Callable,
    change_protocol_for_key: Callable,
    reissue_specific_key: Callable,
    delete_old_key_after_success: Callable,
    show_key_selection_menu: Callable,
    show_protocol_change_menu: Callable,
    show_key_selection_for_country_change: Callable,
    show_country_change_menu: Callable
) -> None:
    """
    Регистрация обработчиков управления ключами
    
    Args:
        dp: Dispatcher aiogram
        bot: Экземпляр бота
        user_states: Словарь состояний пользователей
        change_country_for_key: Функция смены страны
        change_protocol_for_key: Функция смены протокола
        reissue_specific_key: Функция перевыпуска ключа
        delete_old_key_after_success: Функция удаления старого ключа
        show_key_selection_menu: Функция показа меню выбора ключа
        show_protocol_change_menu: Функция показа меню смены протокола
        show_key_selection_for_country_change: Функция показа меню выбора ключа для смены страны
        show_country_change_menu: Функция показа меню смены страны
    """
    
    @dp.message_handler(lambda m: m.text == "Перевыпустить ключ")
    @rate_limit("reissue")
    async def handle_reissue_key(message: types.Message):
        user_id = message.from_user.id
        now = int(time.time())
        
        with get_db_cursor() as cursor:
            # Получаем все активные ключи пользователя
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, 'outline' as key_type
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC
            """, (user_id, now))
            outline_keys = cursor.fetchall()
            
            cursor.execute("""
                SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, 'v2ray' as key_type, s.domain, s.v2ray_path
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC
            """, (user_id, now))
            v2ray_keys = cursor.fetchall()
            
            # Объединяем все ключи
            all_keys = []
            for key in outline_keys:
                all_keys.append({
                    'id': key[0],
                    'expiry_at': key[1],
                    'server_id': key[2],
                    'key_id': key[3],
                    'access_url': key[4],
                    'country': key[5],
                    'tariff_id': key[6],
                    'email': key[7],
                    'protocol': key[8],
                    'type': 'outline'
                })
            
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
                    'v2ray_path': key[10]
                })
            
            if not all_keys:
                await message.answer("У вас нет активных ключей для перевыпуска.", reply_markup=get_main_menu())
                return
            
            if len(all_keys) == 1:
                # Если только один ключ, перевыпускаем его сразу
                await reissue_specific_key(message, user_id, all_keys[0])
            else:
                # Если несколько ключей, показываем список для выбора
                await show_key_selection_menu(message, user_id, all_keys)
    
    @dp.message_handler(lambda m: m.text == "Сменить страну")
    @rate_limit("change_country")
    async def handle_change_country(message: types.Message):
        """Обработчик смены страны"""
        user_id = message.from_user.id
        logging.debug(f"handle_change_country called for user {user_id}")
        
        try:
            with get_db_cursor() as cursor:
                # Получаем активные ключи пользователя
                now = int(time.time())
                
                # Получаем Outline ключи
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, k.traffic_limit_mb
                    FROM keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.user_id = ? AND k.expiry_at > ?
                """, (user_id, now))
                outline_keys = cursor.fetchall()
                
                # Получаем V2Ray ключи
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, s.domain, s.v2ray_path, k.traffic_limit_mb
                    FROM v2ray_keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.user_id = ? AND k.expiry_at > ?
                """, (user_id, now))
                v2ray_keys = cursor.fetchall()
                
                # Объединяем все ключи
                all_keys = []
                for key in outline_keys:
                    all_keys.append({
                        'id': key[0],
                        'expiry_at': key[1],
                        'server_id': key[2],
                        'key_id': key[3],
                        'access_url': key[4],
                        'country': key[5],
                        'tariff_id': key[6],
                        'email': key[7],
                        'protocol': key[8],
                        'type': 'outline',
                        'traffic_limit_mb': key[9]
                    })
                
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
                        'type': 'v2ray',
                        'domain': key[8],
                        'v2ray_path': key[9],
                        'traffic_limit_mb': key[10]
                    })
                
                logging.debug(f"Всего активных ключей для смены страны: {len(all_keys)}")
                
                if not all_keys:
                    await message.answer("У вас нет активных ключей для смены страны.", reply_markup=get_main_menu())
                    return
                
                if len(all_keys) == 1:
                    # Если только один ключ, показываем выбор страны сразу
                    logging.debug(f"Меняем страну для одного ключа: {all_keys[0]['type']}")
                    # Передаем user_states в функцию
                    await show_country_change_menu(message, user_id, all_keys[0], user_states)
                else:
                    # Если несколько ключей, показываем список для выбора
                    logging.debug("Показываем меню выбора ключа для смены страны")
                    await show_key_selection_for_country_change(message, user_id, all_keys)
        
        except Exception as e:
            await BotErrorHandler.handle_error(message, e, "handle_change_country", bot, ADMIN_ID)
    
    @dp.message_handler(lambda m: m.text == "Сменить приложение")
    @rate_limit("change_protocol")
    async def handle_change_app(message: types.Message):
        logging.debug(f"Обработчик 'Сменить приложение' вызван для пользователя {message.from_user.id}")
        user_id = message.from_user.id
        now = int(time.time())
        
        try:
            with get_db_cursor() as cursor:
                # Получаем все активные ключи пользователя
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, 'outline' as key_type, k.traffic_limit_mb
                    FROM keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.user_id = ? AND k.expiry_at > ?
                    ORDER BY k.expiry_at DESC
                """, (user_id, now))
                outline_keys = cursor.fetchall()
                logging.debug(f"Найдено {len(outline_keys)} Outline ключей")
                
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, 'v2ray' as key_type, s.domain, s.v2ray_path, k.traffic_limit_mb
                    FROM v2ray_keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.user_id = ? AND k.expiry_at > ?
                    ORDER BY k.expiry_at DESC
                """, (user_id, now))
                v2ray_keys = cursor.fetchall()
                logging.debug(f"Найдено {len(v2ray_keys)} V2Ray ключей")
                
                # Объединяем все ключи
                all_keys = []
                for key in outline_keys:
                    all_keys.append({
                        'id': key[0],
                        'expiry_at': key[1],
                        'server_id': key[2],
                        'key_id': key[3],
                        'access_url': key[4],
                        'country': key[5],
                        'tariff_id': key[6],
                        'email': key[7],
                        'protocol': key[8],
                        'type': 'outline',
                        'traffic_limit_mb': key[10]
                    })
                
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
                        'traffic_limit_mb': key[11]
                    })
                
                logging.debug(f"Всего активных ключей: {len(all_keys)}")
                
                if not all_keys:
                    await message.answer("У вас нет активных ключей для смены протокола.", reply_markup=get_main_menu())
                    return
                
                if len(all_keys) == 1:
                    # Если только один ключ, меняем его протокол сразу
                    logging.debug(f"Меняем протокол для одного ключа: {all_keys[0]['type']}")
                    await change_protocol_for_key(message, user_id, all_keys[0])
                else:
                    # Если несколько ключей, показываем список для выбора
                    logging.debug("Показываем меню выбора ключа для смены протокола")
                    await show_protocol_change_menu(message, user_id, all_keys)
        
        except Exception as e:
            await BotErrorHandler.handle_error(message, e, "handle_change_app", bot, ADMIN_ID)
    
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
        
        key_type = parts[2]
        key_id = int(parts[3])
        
        # Получаем данные ключа
        with get_db_cursor() as cursor:
            if key_type == "outline":
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, k.traffic_limit_mb
                    FROM keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.id = ? AND k.user_id = ?
                """, (key_id, user_id))
            else:  # v2ray
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, s.domain, s.v2ray_path, k.traffic_limit_mb
                    FROM v2ray_keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.id = ? AND k.user_id = ?
                """, (key_id, user_id))
            
            key_data = cursor.fetchone()
            if not key_data:
                await callback_query.answer("Ключ не найден")
                return
            
            # Формируем словарь с данными ключа
            if key_type == "outline":
                key_dict = {
                    'id': key_data[0],
                    'expiry_at': key_data[1],
                    'server_id': key_data[2],
                    'key_id': key_data[3],
                    'access_url': key_data[4],
                    'country': key_data[5],
                    'tariff_id': key_data[6],
                    'email': key_data[7],
                    'protocol': key_data[8],
                    'traffic_limit_mb': key_data[9],
                    'type': 'outline'
                }
            else:
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
                    'type': 'v2ray'
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
    
    @dp.callback_query_handler(lambda c: c.data.startswith("change_country_"))
    @rate_limit("change_country")
    async def handle_change_country_callback(callback_query: types.CallbackQuery):
        """Обработчик выбора ключа для смены страны"""
        user_id = callback_query.from_user.id
        
        # Парсим callback_data: change_country_{type}_{id}
        parts = callback_query.data.split("_")
        if len(parts) != 4:
            await callback_query.answer("Ошибка: неверный формат данных")
            return
        
        key_type = parts[2]
        key_id = int(parts[3])
        
        # Получаем данные ключа
        with get_db_cursor() as cursor:
            if key_type == "outline":
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, k.traffic_limit_mb
                    FROM keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.id = ? AND k.user_id = ?
                """, (key_id, user_id))
            else:  # v2ray
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, s.domain, s.v2ray_path, k.traffic_limit_mb
                    FROM v2ray_keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.id = ? AND k.user_id = ?
                """, (key_id, user_id))
            
            key_data = cursor.fetchone()
            if not key_data:
                await callback_query.answer("Ключ не найден")
                return
            
            # Формируем словарь с данными ключа
            if key_type == "outline":
                key_dict = {
                    'id': key_data[0],
                    'expiry_at': key_data[1],
                    'server_id': key_data[2],
                    'key_id': key_data[3],
                    'access_url': key_data[4],
                    'country': key_data[5],
                    'tariff_id': key_data[6],
                    'email': key_data[7],
                    'protocol': key_data[8],
                    'traffic_limit_mb': key_data[9],
                    'type': 'outline'
                }
            else:  # v2ray
                key_dict = {
                    'id': key_data[0],
                    'expiry_at': key_data[1],
                    'server_id': key_data[2],
                    'v2ray_uuid': key_data[3],
                    'country': key_data[4],
                    'tariff_id': key_data[5],
                    'email': key_data[6],
                    'protocol': key_data[7],
                    'type': 'v2ray',
                    'domain': key_data[8],
                    'v2ray_path': key_data[9],
                    'traffic_limit_mb': key_data[10]
                }
        
        await callback_query.answer()
        await show_country_change_menu(callback_query.message, user_id, key_dict, user_states)
    
    @dp.callback_query_handler(lambda c: c.data == "cancel_country_change")
    async def handle_cancel_country_change(callback_query: types.CallbackQuery):
        """Обработчик отмены смены страны"""
        await callback_query.answer()
        await callback_query.message.answer("Смена страны отменена.", reply_markup=get_main_menu())
    
    @dp.callback_query_handler(lambda c: c.data.startswith("change_protocol_"))
    @rate_limit("change_protocol")
    async def handle_change_protocol_callback(callback_query: types.CallbackQuery):
        """Обработчик выбора ключа для смены протокола"""
        user_id = callback_query.from_user.id
        
        # Парсим callback_data: change_protocol_{type}_{id}
        parts = callback_query.data.split("_")
        if len(parts) != 4:
            await callback_query.answer("Ошибка: неверный формат данных")
            return
        
        key_type = parts[2]
        key_id = int(parts[3])
        
        # Получаем данные ключа
        with get_db_cursor() as cursor:
            if key_type == "outline":
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.key_id, k.access_url, s.country, k.tariff_id, k.email, s.protocol, k.traffic_limit_mb
                    FROM keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.id = ? AND k.user_id = ?
                """, (key_id, user_id))
            else:  # v2ray
                cursor.execute("""
                    SELECT k.id, k.expiry_at, k.server_id, k.v2ray_uuid, s.country, k.tariff_id, k.email, s.protocol, s.domain, s.v2ray_path, k.traffic_limit_mb
                    FROM v2ray_keys k
                    JOIN servers s ON k.server_id = s.id
                    WHERE k.id = ? AND k.user_id = ?
                """, (key_id, user_id))
            
            key_data = cursor.fetchone()
            if not key_data:
                await callback_query.answer("Ключ не найден")
                return
            
            # Формируем словарь с данными ключа
            if key_type == "outline":
                key_dict = {
                    'id': key_data[0],
                    'expiry_at': key_data[1],
                    'server_id': key_data[2],
                    'key_id': key_data[3],
                    'access_url': key_data[4],
                    'country': key_data[5],
                    'tariff_id': key_data[6],
                    'email': key_data[7],
                    'protocol': key_data[8],
                    'traffic_limit_mb': key_data[9],
                    'type': 'outline'
                }
            else:
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
                    'type': 'v2ray'
                }
        
        # Меняем протокол для ключа
        await change_protocol_for_key(callback_query.message, user_id, key_dict)
        await callback_query.answer()
    
    @dp.callback_query_handler(lambda c: c.data == "cancel_protocol_change")
    async def handle_cancel_protocol_change(callback_query: types.CallbackQuery):
        """Обработчик отмены смены протокола"""
        await callback_query.message.edit_text("Смена протокола отменена.")
        await callback_query.answer()
    
    @dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "country_change_selection")
    async def handle_country_change_selection(message: types.Message):
        """Обработчик выбора страны для смены"""
        user_id = message.from_user.id
        text = message.text or ""
        
        # Проверяем, что это кнопка "Назад"
        if text == "🔙 Назад":
            user_states.pop(user_id, None)
            await message.answer("Главное меню:", reply_markup=get_main_menu())
            return
        
        # Извлекаем название страны из текста (убираем эмодзи)
        if text.startswith("🌍 "):
            selected_country = text[2:]  # Убираем "🌍 "
        else:
            selected_country = text
        
        # Получаем данные ключа из состояния
        state = user_states.get(user_id, {})
        key_data = state.get("key_data")
        
        if not key_data:
            await message.answer("Ошибка: данные ключа не найдены. Попробуйте еще раз.", reply_markup=get_main_menu())
            return
        
        # Очищаем состояние
        user_states.pop(user_id, None)
        
        # Выполняем смену страны
        await change_country_for_key(message, user_id, key_data, selected_country)

