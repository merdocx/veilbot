"""
Клавиатуры для бота
"""
import time
from typing import Optional
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from app.infra.sqlite_utils import get_db_cursor
from config import PROTOCOLS, FREE_V2RAY_TARIFF_ID
from app.infra.cache import SimpleCache

# Кэш для меню
_menu_cache = SimpleCache()

def invalidate_menu_cache():
    """Инвалидировать кэш меню (вызывать при изменении тарифов/серверов)"""
    _menu_cache.delete("protocol_selection_menu")
    # Удаляем все кэшированные меню тарифов
    # Так как ключи могут быть разные, очищаем весь кэш меню
    _menu_cache.clear()

def get_main_menu(user_id: Optional[int] = None) -> ReplyKeyboardMarkup:
    """
    Главное меню бота с динамическим добавлением кнопок подписки
    
    Args:
        user_id: ID пользователя для проверки наличия активной подписки
    """
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    menu.add(KeyboardButton("Получить доступ"))
    menu.add(KeyboardButton("Мои ключи"))
    menu.add(KeyboardButton("Получить месяц бесплатно"))
    menu.add(KeyboardButton("Помощь"))
    return menu

def get_help_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для помощи"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("Инструкция по подключению к Apple TV"))
    keyboard.add(KeyboardButton("💬 Связаться с поддержкой"))
    keyboard.add(KeyboardButton("🔙 Назад"))
    return keyboard

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура отмены"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("🔙 Отмена"))
    return keyboard

def get_protocol_selection_menu() -> ReplyKeyboardMarkup:
    """Создает меню выбора протокола, показывая только те протоколы, у которых есть доступные серверы"""
    cache_key = "protocol_selection_menu"
    cached = _menu_cache.get(cache_key)
    if cached:
        return cached
    
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    
    # Проверяем наличие доступных серверов для каждого протокола
    with get_db_cursor() as cursor:
        # Проверяем Outline
        cursor.execute("""
            SELECT COUNT(*) FROM servers 
            WHERE active = 1 AND available_for_purchase = 1 AND protocol = 'outline'
        """)
        outline_count = cursor.fetchone()[0]
        
        # Проверяем V2Ray
        cursor.execute("""
            SELECT COUNT(*) FROM servers 
            WHERE active = 1 AND available_for_purchase = 1 AND protocol = 'v2ray'
        """)
        v2ray_count = cursor.fetchone()[0]
    
    # Добавляем только протоколы с доступными серверами
    if outline_count > 0:
        menu.add(KeyboardButton(f"{PROTOCOLS['outline']['icon']} {PROTOCOLS['outline']['name']}"))
    
    if v2ray_count > 0:
        menu.add(KeyboardButton(f"{PROTOCOLS['v2ray']['icon']} {PROTOCOLS['v2ray']['name']}"))
    
    # Добавляем кнопку "Назад"
    menu.add(KeyboardButton("🔙 Назад"))
    
    # Кэшируем на 5 минут
    _menu_cache.set(cache_key, menu, ttl=300)
    
    return menu

def get_tariff_menu(paid_only: bool = False, payment_method: str = None) -> ReplyKeyboardMarkup:
    """
    Получить меню тарифов с ценами в зависимости от способа оплаты
    
    Args:
        paid_only: Показывать только платные тарифы
        payment_method: Способ оплаты ('yookassa' или 'cryptobot')
    """
    # Кэш ключ включает параметры фильтрации
    cache_key = f"tariff_menu:{paid_only}:{payment_method or 'none'}"
    cached = _menu_cache.get(cache_key)
    if cached:
        return cached
    
    with get_db_cursor() as cursor:
        if paid_only:
            cursor.execute("SELECT id, name, price_rub, duration_sec, price_crypto_usd FROM tariffs WHERE price_rub > 0 ORDER BY price_rub ASC")
        else:
            cursor.execute("SELECT id, name, price_rub, duration_sec, price_crypto_usd FROM tariffs ORDER BY price_rub ASC")
        tariffs = cursor.fetchall()

    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    has_available_tariffs = False
    
    for tariff_id, name, price, duration, price_crypto in tariffs:
        if tariff_id == FREE_V2RAY_TARIFF_ID:
            continue
        if price > 0:
            # Если выбран способ оплаты, показываем соответствующую цену
            if payment_method == "cryptobot":
                # Для криптовалюты показываем только тарифы с крипто-ценой
                if price_crypto:
                    label = f"{name} — ${price_crypto:.2f}"
                    menu.add(KeyboardButton(label))
                    has_available_tariffs = True
                # Если нет крипто-цены, просто не показываем тариф
            elif payment_method == "yookassa":
                label = f"{name} — {price}₽"
                menu.add(KeyboardButton(label))
                has_available_tariffs = True
            else:
                # Если способ оплаты не выбран, показываем обе цены
                if price_crypto:
                    label = f"{name} — {price}₽ / ${price_crypto:.2f}"
                else:
                    label = f"{name} — {price}₽"
                menu.add(KeyboardButton(label))
                has_available_tariffs = True
        else:
            # Бесплатные тарифы показываем только если не выбрана крипта
            if payment_method != "cryptobot":
                if tariff_id == FREE_V2RAY_TARIFF_ID:
                    continue
                label = f"{name} — бесплатно"
                menu.add(KeyboardButton(label))
                has_available_tariffs = True
    
    # Если для криптовалюты нет доступных тарифов, добавляем сообщение
    if payment_method == "cryptobot" and not has_available_tariffs:
        # Но не добавляем кнопку, просто вернем пустое меню
        pass
    
    menu.add(KeyboardButton("🔙 Назад"))
    
    # Кэшируем на 5 минут (тарифы меняются редко)
    _menu_cache.set(cache_key, menu, ttl=300)
    
    return menu

def get_payment_method_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура выбора способа оплаты"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("💳 Карта РФ / СБП"))
    keyboard.add(KeyboardButton("₿ Криптовалюта (USDT)"))
    keyboard.add(KeyboardButton("🔙 Назад"))
    return keyboard

def get_country_menu(countries):
    """Создает меню выбора страны"""
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    for country in countries:
        menu.add(KeyboardButton(country))
    menu.add(KeyboardButton("🔙 Назад"))
    return menu

def get_countries():
    """Получить список доступных стран"""
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT country FROM servers 
            WHERE active = 1 AND available_for_purchase = 1 AND country IS NOT NULL AND country != ''
            ORDER BY country
        """)
        return [row[0] for row in cursor.fetchall()]

def get_countries_by_protocol(protocol):
    """Получить список стран для конкретного протокола"""
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT country FROM servers 
            WHERE active = 1 AND available_for_purchase = 1 AND protocol = ? AND country IS NOT NULL AND country != ''
            ORDER BY country
        """, (protocol,))
        return [row[0] for row in cursor.fetchall()]

