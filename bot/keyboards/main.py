"""
Клавиатуры для бота
"""
import asyncio
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
        cursor.execute("""
            SELECT COUNT(*) FROM servers 
            WHERE active = 1 AND (COALESCE(access_level, 'all') = 'all') AND protocol = 'v2ray'
        """)
        v2ray_count = cursor.fetchone()[0]
    
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
        base_sql = """
            SELECT 
                id,
                name,
                price_rub,
                duration_sec,
                price_crypto_usd,
                enable_yookassa,
                enable_platega,
                enable_cryptobot
            FROM tariffs
            WHERE (is_archived IS NULL OR is_archived = 0)
        """
        if paid_only:
            base_sql += " AND price_rub > 0"
        base_sql += " ORDER BY price_rub ASC"
        cursor.execute(base_sql)
        tariffs = cursor.fetchall()

    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    has_available_tariffs = False
    
    for tariff_id, name, price, duration, price_crypto, enable_yookassa, enable_platega, enable_cryptobot in tariffs:
        if tariff_id == FREE_V2RAY_TARIFF_ID:
            continue
        if price > 0:
            # Если выбран способ оплаты, показываем только тарифы, где он разрешен
            if payment_method == "cryptobot":
                # Для криптовалюты показываем только тарифы с крипто-ценой и включенным флагом
                if enable_cryptobot and price_crypto:
                    label = f"{name} — ${price_crypto:.2f}"
                    menu.add(KeyboardButton(label))
                    has_available_tariffs = True
                # Если нет крипто-цены или флаг отключен, тариф не показываем
            elif payment_method == "yookassa":
                if enable_yookassa:
                    label = f"{name} — {price}₽"
                    menu.add(KeyboardButton(label))
                    has_available_tariffs = True
            elif payment_method == "platega":
                if enable_platega:
                    label = f"{name} — {price}₽"
                    menu.add(KeyboardButton(label))
                    has_available_tariffs = True
            else:
                # Если способ оплаты не выбран, показываем обе цены (для отладочных/общих сценариев)
                if price_crypto:
                    label = f"{name} — {price}₽ / ${price_crypto:.2f}"
                else:
                    label = f"{name}₽"
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
    """
    Клавиатура выбора способа оплаты
    Показывает только те способы оплаты, которые доступны хотя бы для одного тарифа
    """
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    
    # Проверяем наличие тарифов с включенными способами оплаты
    with get_db_cursor() as cursor:
        # Проверяем YooKassa (Карта РФ/СБП)
        cursor.execute("""
            SELECT COUNT(*) 
            FROM tariffs
            WHERE (is_archived IS NULL OR is_archived = 0)
              AND enable_yookassa = 1
              AND price_rub > 0
        """)
        has_yookassa = cursor.fetchone()[0] > 0
        
        # Проверяем Platega (Карта РФ / Карта зарубеж / СБП)
        cursor.execute("""
            SELECT COUNT(*) 
            FROM tariffs
            WHERE (is_archived IS NULL OR is_archived = 0)
              AND enable_platega = 1
              AND price_rub > 0
        """)
        has_platega = cursor.fetchone()[0] > 0
        
        # Проверяем CryptoBot (Криптовалюта)
        cursor.execute("""
            SELECT COUNT(*) 
            FROM tariffs
            WHERE (is_archived IS NULL OR is_archived = 0)
              AND enable_cryptobot = 1
              AND price_crypto_usd IS NOT NULL
              AND price_crypto_usd > 0
        """)
        has_cryptobot = cursor.fetchone()[0] > 0
    
    # Показываем только доступные способы оплаты
    if has_yookassa:
        keyboard.add(KeyboardButton("💳 Карта РФ/СБП"))
    
    if has_platega:
        keyboard.add(KeyboardButton("💳 Карта РФ / Карта зарубеж / СБП"))
    
    if has_cryptobot:
        keyboard.add(KeyboardButton("₿ Криптовалюта (USDT)"))
    
    keyboard.add(KeyboardButton("🔙 Назад"))
    return keyboard


def get_platega_method_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура выбора способа оплаты внутри Platega"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("🇷🇺 Карта РФ (Platega)"))
    keyboard.add(KeyboardButton("🌍 Карта зарубеж (Platega)"))
    keyboard.add(KeyboardButton("📱 СБП (QR, Platega)"))
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
            WHERE active = 1 AND (COALESCE(access_level, 'all') = 'all') AND country IS NOT NULL AND country != ''
            ORDER BY country
        """)
        return [row[0] for row in cursor.fetchall()]

def get_countries_by_protocol(protocol):
    """Получить список стран для конкретного протокола"""
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT country FROM servers 
            WHERE active = 1 AND (COALESCE(access_level, 'all') = 'all') AND protocol = ? AND country IS NOT NULL AND country != ''
            ORDER BY country
        """, (protocol,))
        return [row[0] for row in cursor.fetchall()]


async def get_protocol_selection_menu_async() -> ReplyKeyboardMarkup:
    """Асинхронная обёртка: выполнение в executor, чтобы не блокировать event loop."""
    return await asyncio.to_thread(get_protocol_selection_menu)


async def get_tariff_menu_async(paid_only: bool = False, payment_method: str = None) -> ReplyKeyboardMarkup:
    """Асинхронная обёртка: выполнение в executor."""
    return await asyncio.to_thread(get_tariff_menu, paid_only, payment_method)


async def get_payment_method_keyboard_async() -> ReplyKeyboardMarkup:
    """Асинхронная обёртка: выполнение в executor."""
    return await asyncio.to_thread(get_payment_method_keyboard)

