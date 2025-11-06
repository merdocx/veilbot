"""
Сервис для работы с бесплатными тарифами
"""
import time
import sqlite3
import logging
from typing import Optional, Dict, Any
from aiogram import types
from config import PROTOCOLS
from bot.keyboards import get_main_menu
from bot.services.key_creation import create_new_key_flow_with_protocol


def check_free_tariff_limit_by_protocol_and_country(
    cursor: sqlite3.Cursor, 
    user_id: int, 
    protocol: str = "outline", 
    country: Optional[str] = None
) -> bool:
    """
    Проверка лимита бесплатных ключей для конкретного протокола и страны - один раз навсегда
    
    Args:
        cursor: Курсор БД
        user_id: ID пользователя
        protocol: Протокол (outline или v2ray)
        country: Страна (опционально)
    
    Returns:
        True если пользователь уже получал бесплатный ключ, False иначе
    """
    # Проверяем в таблице free_key_usage - это основная проверка
    # Сначала проверяем, получал ли пользователь бесплатный ключ для этого протокола вообще
    cursor.execute("""
        SELECT created_at FROM free_key_usage 
        WHERE user_id = ? AND protocol = ?
    """, (user_id, protocol))
    
    row = cursor.fetchone()
    if row:
        return True  # Пользователь уже получал бесплатный ключ для этого протокола
    
    # Если указана конкретная страна, дополнительно проверяем для неё
    if country:
        cursor.execute("""
            SELECT created_at FROM free_key_usage 
            WHERE user_id = ? AND protocol = ? AND country = ?
        """, (user_id, protocol, country))
        
        row = cursor.fetchone()
        if row:
            return True  # Пользователь уже получал бесплатный ключ для этого протокола и страны
    
    # Дополнительная проверка в таблицах ключей (для обратной совместимости)
    if protocol == "outline":
        if country:
            cursor.execute("""
                SELECT k.created_at FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND t.price_rub = 0 AND s.country = ?
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id, country))
        else:
            cursor.execute("""
                SELECT k.created_at FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND t.price_rub = 0
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id,))
    else:  # v2ray
        if country:
            cursor.execute("""
                SELECT k.created_at FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND t.price_rub = 0 AND s.country = ?
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id, country))
        else:
            cursor.execute("""
                SELECT k.created_at FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND t.price_rub = 0
                ORDER BY k.created_at DESC LIMIT 1
            """, (user_id,))
    
    row = cursor.fetchone()
    # Если найден любой бесплатный ключ — нельзя (только один раз навсегда)
    if row:
        return True
    # Иначе можно
    return False


def check_free_tariff_limit(cursor: sqlite3.Cursor, user_id: int) -> bool:
    """
    Проверка лимита бесплатных ключей - один раз навсегда (для обратной совместимости)
    
    Args:
        cursor: Курсор БД
        user_id: ID пользователя
    
    Returns:
        True если пользователь уже получал бесплатный ключ, False иначе
    """
    return check_free_tariff_limit_by_protocol_and_country(cursor, user_id, "outline")


def check_free_tariff_limit_by_protocol(cursor: sqlite3.Cursor, user_id: int, protocol: str = "outline") -> bool:
    """
    Проверка лимита бесплатных ключей для конкретного протокола - один раз навсегда (для обратной совместимости)
    
    Args:
        cursor: Курсор БД
        user_id: ID пользователя
        protocol: Протокол (outline или v2ray)
    
    Returns:
        True если пользователь уже получал бесплатный ключ, False иначе
    """
    return check_free_tariff_limit_by_protocol_and_country(cursor, user_id, protocol)


def record_free_key_usage(
    cursor: sqlite3.Cursor, 
    user_id: int, 
    protocol: str = "outline", 
    country: Optional[str] = None
) -> bool:
    """
    Записывает использование бесплатного ключа пользователем
    
    Args:
        cursor: Курсор БД
        user_id: ID пользователя
        protocol: Протокол (outline или v2ray)
        country: Страна (опционально)
    
    Returns:
        True если запись успешна, False если запись уже существует или произошла ошибка
    """
    now = int(time.time())
    try:
        cursor.execute("""
            INSERT INTO free_key_usage (user_id, protocol, country, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, protocol, country, now))
        return True
    except sqlite3.IntegrityError:
        # Запись уже существует (UNIQUE constraint)
        return False
    except Exception as e:
        logging.error(f"Failed to record free key usage: {e}")
        return False


async def handle_free_tariff(
    cursor: sqlite3.Cursor, 
    message: types.Message, 
    user_id: int, 
    tariff: Dict[str, Any], 
    country: Optional[str] = None
) -> None:
    """
    Обработка бесплатного тарифа (старая версия без протоколов, для обратной совместимости)
    
    Args:
        cursor: Курсор БД
        message: Telegram сообщение
        user_id: ID пользователя
        tariff: Словарь с данными тарифа
        country: Страна (опционально)
    """
    main_menu = get_main_menu()
    
    if check_free_tariff_limit(cursor, user_id):
        await message.answer("Вы уже получали бесплатный тариф ранее. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
        return
    
    now = int(time.time())
    # Проверяем наличие активного ключа и его тип
    cursor.execute("""
        SELECT k.id, k.expiry_at, t.price_rub
        FROM keys k
        JOIN tariffs t ON k.tariff_id = t.id
        WHERE k.user_id = ? AND k.expiry_at > ?
        ORDER BY k.expiry_at DESC LIMIT 1
    """, (user_id, now))
    existing_key = cursor.fetchone()
    
    if existing_key:
        if existing_key[2] > 0:
            await message.answer("У вас уже есть активный платный ключ. Бесплатный ключ можно получить только если нет активных платных ключей.", reply_markup=main_menu)
            return
        else:
            await message.answer("У вас уже есть активный бесплатный ключ. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
            return
    else:
        # Импортируем create_new_key_flow из bot.py для обратной совместимости
        # Это старая функция, которая не поддерживает протоколы
        from bot import create_new_key_flow
        await create_new_key_flow(cursor, message, user_id, tariff, None, country)


async def handle_free_tariff_with_protocol(
    cursor: sqlite3.Cursor, 
    message: types.Message, 
    user_id: int, 
    tariff: Dict[str, Any], 
    country: Optional[str] = None, 
    protocol: str = "outline"
) -> None:
    """
    Обработка бесплатного тарифа с поддержкой протоколов
    
    Args:
        cursor: Курсор БД
        message: Telegram сообщение
        user_id: ID пользователя
        tariff: Словарь с данными тарифа
        country: Страна (опционально)
        protocol: Протокол (outline или v2ray)
    """
    main_menu = get_main_menu()
    
    # Проверяем лимит бесплатных ключей для выбранного протокола и страны
    if check_free_tariff_limit_by_protocol_and_country(cursor, user_id, protocol, country):
        if country:
            await message.answer(f"Вы уже получали бесплатный тариф {PROTOCOLS[protocol]['name']} для страны {country} ранее. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
        else:
            await message.answer(f"Вы уже получали бесплатный тариф {PROTOCOLS[protocol]['name']} ранее. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
        return
    
    now = int(time.time())
    
    # Проверяем наличие активного ключа для конкретной страны и протокола
    if protocol == "outline":
        if country:
            cursor.execute("""
                SELECT k.id, k.expiry_at, t.price_rub
                FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ? AND s.country = ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now, country))
        else:
            cursor.execute("""
                SELECT k.id, k.expiry_at, t.price_rub
                FROM keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now))
    else:  # v2ray
        if country:
            cursor.execute("""
                SELECT k.id, k.expiry_at, t.price_rub
                FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ? AND s.country = ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now, country))
        else:
            cursor.execute("""
                SELECT k.id, k.expiry_at, t.price_rub
                FROM v2ray_keys k
                JOIN tariffs t ON k.tariff_id = t.id
                WHERE k.user_id = ? AND k.expiry_at > ?
                ORDER BY k.expiry_at DESC LIMIT 1
            """, (user_id, now))
    
    existing_key = cursor.fetchone()
    if existing_key:
        if existing_key[2] > 0:
            if country:
                await message.answer(f"У вас уже есть активный платный ключ {PROTOCOLS[protocol]['name']} для страны {country}. Бесплатный ключ можно получить только если нет активных платных ключей.", reply_markup=main_menu)
            else:
                await message.answer(f"У вас уже есть активный платный ключ {PROTOCOLS[protocol]['name']}. Бесплатный ключ можно получить только если нет активных платных ключей.", reply_markup=main_menu)
            return
        else:
            if country:
                await message.answer(f"У вас уже есть активный бесплатный ключ {PROTOCOLS[protocol]['name']} для страны {country}. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
            else:
                await message.answer(f"У вас уже есть активный бесплатный ключ {PROTOCOLS[protocol]['name']}. Бесплатный ключ можно получить только один раз.", reply_markup=main_menu)
            return
    else:
        # Для бесплатного тарифа создаем ключ сразу без запроса email
        # Импортируем user_states из bot.py через lazy import для избежания циклических зависимостей
        try:
            import importlib
            bot_module = importlib.import_module('bot')
            user_states = getattr(bot_module, 'user_states', {})
        except Exception as e:
            logging.error(f"Error importing user_states: {e}")
            user_states = {}
        
        await create_new_key_flow_with_protocol(
            cursor, 
            message, 
            user_id, 
            tariff, 
            None,  # email
            country, 
            protocol,
            for_renewal=False,
            user_states=user_states
        )

