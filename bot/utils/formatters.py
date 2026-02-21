"""
Форматирование сообщений для бота
"""
from config import PROTOCOLS
from vpn_protocols import format_duration, get_protocol_instructions
import logging

def format_key_message(access_url: str) -> str:
    """Форматирование сообщения с ключом Outline (устаревшее, для обратной совместимости)"""
    return (
        f"*Ваш ключ* (коснитесь, чтобы скопировать):\n"
        f"`{access_url}`\n\n"
        "🔧 *Как подключиться:*\n"
        "1. Установите Outline:\n"
        "   • [App Store](https://apps.apple.com/app/outline-app/id1356177741)\n"
        "   • [Google Play](https://play.google.com/store/apps/details?id=org.outline.android.client)\n"
        "2. Откройте приложение и нажмите «Добавить сервер» или «+»\n"
        "3. Вставьте ключ выше"
    )

def format_key_message_unified(config: str, protocol: str, tariff: dict = None, remaining_time: int = None) -> str:
    """Унифицированное форматирование сообщения с ключом для всех протоколов"""
    protocol_info = PROTOCOLS.get(protocol, {})
    protocol_name = protocol_info.get('name', protocol.upper())
    protocol_icon = protocol_info.get('icon', '🔒')
    
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
    protocol_info = PROTOCOLS.get(protocol, {})
    
    return (
        f"*Ваш ключ {protocol_info.get('icon', '🔒')} {protocol_info.get('name', protocol.upper())}* (коснитесь, чтобы скопировать):\n"
        f"`{config}`\n\n"
        f"📦 Тариф: *{tariff.get('name', 'Неизвестно')}*\n"
        f"⏱ Срок действия: *{format_duration(tariff.get('duration_sec', 0))}*\n\n"
        f"🔧 *Как подключиться:*\n"
        f"{get_protocol_instructions(protocol)}"
    )

