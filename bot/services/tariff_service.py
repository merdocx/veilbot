"""
Сервис для работы с тарифами
"""
import sqlite3
from typing import Optional, Dict, Any
from aiogram import types
from config import PROTOCOLS, FREE_V2RAY_TARIFF_ID
from bot.keyboards import get_payment_method_keyboard


def get_tariff_by_name_and_price(
    cursor: sqlite3.Cursor, 
    tariff_name: str, 
    price: float
) -> Optional[Dict[str, Any]]:
    """
    Получение тарифа по имени и цене
    
    Args:
        cursor: Курсор БД
        tariff_name: Название тарифа
        price: Цена в рублях
    
    Returns:
        Словарь с данными тарифа или None, если не найден
    """
    try:
        cursor.execute(
            "SELECT id, name, price_rub, duration_sec, price_crypto_usd, traffic_limit_mb FROM tariffs WHERE name = ? AND price_rub = ? AND (is_archived IS NULL OR is_archived = 0)",
            (tariff_name, price),
        )
    except sqlite3.OperationalError as exc:
        if "traffic_limit_mb" in str(exc):
            cursor.execute(
                "SELECT id, name, price_rub, duration_sec, price_crypto_usd FROM tariffs WHERE name = ? AND price_rub = ? AND (is_archived IS NULL OR is_archived = 0)",
                (tariff_name, price),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "name": row[1],
                "price_rub": row[2],
                "duration_sec": row[3],
                "price_crypto_usd": row[4] if len(row) > 4 else None,
                "traffic_limit_mb": 0,
            }
        raise
    row = cursor.fetchone()
    if not row:
        return None
    if row[0] == FREE_V2RAY_TARIFF_ID:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "price_rub": row[2],
        "duration_sec": row[3],
        "price_crypto_usd": row[4] if len(row) > 4 else None,
        "traffic_limit_mb": row[5] if len(row) > 5 else 0,
    }


async def handle_payment_method_selection(
    cursor: sqlite3.Cursor,
    message: types.Message,
    user_id: int,
    tariff: Dict[str, Any],
    user_states: Dict[int, Dict[str, Any]],
    country: Optional[str] = None,
    protocol: str = "v2ray"
) -> None:
    """
    Обработка выбора способа оплаты для платного тарифа
    
    Args:
        cursor: Курсор БД
        message: Telegram сообщение
        user_id: ID пользователя
        tariff: Словарь с данными тарифа
        user_states: Словарь состояний пользователей
        country: Страна (опционально)
        protocol: Протокол (v2ray)
    """
    # Сохраняем состояние и показываем выбор способа оплаты
    user_states[user_id] = {
        "state": "waiting_payment_method",
        "tariff": tariff,
        "country": country,
        "protocol": protocol
    }
    
    msg = f"💳 *Выберите способ оплаты*\n\n"
    msg += f"📦 Тариф: *{tariff['name']}*\n"
    msg += f"💰 Сумма: *{tariff['price_rub']}₽*"
    
    if tariff.get('price_crypto_usd'):
        msg += f" / *${tariff['price_crypto_usd']:.2f}* (криптовалюта)\n"
    else:
        msg += "\n"
    
    msg += f"\n{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
    
    await message.answer(
        msg,
        reply_markup=get_payment_method_keyboard(),
        parse_mode="Markdown"
    )


async def handle_paid_tariff_with_protocol(
    cursor: sqlite3.Cursor,
    message: types.Message,
    user_id: int,
    tariff: Dict[str, Any],
    user_states: Dict[int, Dict[str, Any]],
    country: Optional[str] = None,
    protocol: str = "v2ray"
) -> None:
    """
    Обработка платного тарифа с поддержкой протоколов
    
    Args:
        cursor: Курсор БД
        message: Telegram сообщение
        user_id: ID пользователя
        tariff: Словарь с данными тарифа
        user_states: Словарь состояний пользователей
        country: Страна (опционально)
        protocol: Протокол (v2ray)
    """
    # Перенаправляем на выбор способа оплаты
    await handle_payment_method_selection(cursor, message, user_id, tariff, user_states, country, protocol)

