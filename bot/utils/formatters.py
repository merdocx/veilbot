"""
Форматирование сообщений для бота
"""
from config import PROTOCOLS
from vpn_protocols import format_duration, get_protocol_instructions
import logging

def format_key_message(access_url: str) -> str:
    """Форматирование сообщения с ключом (устаревшее имя; делегирует V2Ray-формату)."""
    return format_key_message_unified(access_url, "v2ray")

def format_key_message_unified(config: str, protocol: str, tariff: dict = None, remaining_time: int = None) -> str:
    """Унифицированное форматирование сообщения с ключом для всех протоколов"""
    # В продукте поддерживается один протокол, поэтому в пользовательских сообщениях
    # не показываем его название (чтобы не перегружать UX).
    protocol_name = "VPN"
    protocol_icon = "🔒"
    
    # Форматируем оставшееся время
    if remaining_time:
        time_str = format_duration(remaining_time)
        time_info = f"\n⏰ *Осталось:* {time_str}"
    else:
        time_info = ""
    
    # Форматируем информацию о тарифе
    if tariff:
        tariff_info = f"\n📦 *Тариф:* {tariff.get('name', 'Неизвестно')}"
        if tariff.get('price_rub', 0) > 0:
            tariff_info += f" — {tariff['price_rub']}₽"
        else:
            tariff_info += " — бесплатно"
    else:
        tariff_info = ""
    
    # Получаем инструкции по подключению
    try:
        instructions = get_protocol_instructions(protocol)
    except Exception as e:
        logging.warning(f"Не удалось получить инструкции для протокола {protocol}: {e}")
        instructions = "Инструкции по подключению временно недоступны."
    
    return (
        f"{protocol_icon} *{protocol_name}*\n\n"
        f"*Ваш ключ* (коснитесь, чтобы скопировать):\n"
        f"`{config}`\n\n"
        f"🔧 *Как подключиться:*\n{instructions}"
        f"{tariff_info}{time_info}"
    )

def format_key_message_with_protocol(config: str, protocol: str, tariff: dict) -> str:
    """Форматирование сообщения с ключом с указанием протокола (для обратной совместимости)"""
    return (
        "*Ваш ключ* (коснитесь, чтобы скопировать):\n"
        f"`{config}`\n\n"
        f"📦 Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
        f"⏱ Срок действия: *{format_duration(tariff.get('duration_sec', 0))}*\n\n"
        f"🔧 *Как подключиться:*\n"
        f"{get_protocol_instructions(protocol)}"
    )

