"""
Обработчик кнопки "Мои ключи"
"""
import time
from typing import Optional
from aiogram import Dispatcher, types
from app.infra.sqlite_utils import get_db_cursor
from config import PROTOCOLS
from vpn_protocols import format_duration
from bot.keyboards import get_main_menu
from bot_rate_limiter import rate_limit
from app.repositories.subscription_repository import SubscriptionRepository

def _format_bytes_short(num_bytes: Optional[float]) -> str:
    """Форматирование байт в читаемый вид."""
    if num_bytes is None:
        return "—"
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(num_bytes)
    for unit in units:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} ПБ"


async def handle_my_keys_btn(message: types.Message):
    """
    Обработчик кнопки "Мои ключи"
    
    Args:
        message: Telegram сообщение
    """
    user_id = message.from_user.id
    now = int(time.time())
    
    all_keys = []
    
    # Получаем активную подписку V2Ray
    subscription_info = None
    with get_db_cursor() as cursor:
        cursor.execute("""
            SELECT id, subscription_token, expires_at, tariff_id
            FROM subscriptions
            WHERE user_id = ? AND is_active = 1 AND expires_at > ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (user_id, now))
        subscription = cursor.fetchone()
        
        if subscription:
            subscription_id, token, expires_at, tariff_id = subscription
            
            # Получаем количество серверов в подписке
            cursor.execute("""
                SELECT COUNT(DISTINCT server_id)
                FROM v2ray_keys
                WHERE subscription_id = ? AND expiry_at > ?
            """, (subscription_id, now))
            server_count = cursor.fetchone()[0] or 0
            
            # Получаем информацию о тарифе
            traffic_limit = "без ограничений"
            if tariff_id:
                cursor.execute("SELECT traffic_limit_mb FROM tariffs WHERE id = ?", (tariff_id,))
                tariff_row = cursor.fetchone()
                if tariff_row and tariff_row[0] and tariff_row[0] > 0:
                    traffic_limit = f"{tariff_row[0]} ГБ"
            
            subscription_info = {
                'id': subscription_id,
                'token': token,
                'expires_at': expires_at,
                'server_count': server_count,
                'traffic_limit': traffic_limit
            }
    
    with get_db_cursor() as cursor:
        # Получаем Outline ключи с информацией о стране
        # ВАЖНО: Показываем только ключи, связанные с активной подпиской
        # Если есть активная подписка, показываем только её ключи
        # Если подписки нет, показываем все активные outline ключи
        if subscription_info:
            # Есть активная подписка - показываем только её outline ключи
            cursor.execute("""
                SELECT k.access_url, k.expiry_at, k.protocol, s.country, k.traffic_limit_mb
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ? AND k.subscription_id = ?
            """, (user_id, now, subscription_info['id']))
        else:
            # Нет активной подписки - показываем все активные outline ключи
            cursor.execute("""
                SELECT k.access_url, k.expiry_at, k.protocol, s.country, k.traffic_limit_mb
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.user_id = ? AND k.expiry_at > ?
            """, (user_id, now))
        outline_keys = cursor.fetchall()
    
    # Добавляем Outline ключи
    for access_url, exp, protocol, country, limit_mb in outline_keys:
        all_keys.append({
            'type': 'outline',
            'config': access_url,
            'expiry': exp,
            'protocol': protocol or 'outline',
            'country': country,
            'traffic_limit_mb': limit_mb or 0,
            'traffic_usage_bytes': None,
        })

    # Формируем сообщение
    msg = ""
    
    # Если есть подписка, показываем её первой
    if subscription_info:
        from datetime import datetime
        expiry_date = datetime.fromtimestamp(subscription_info['expires_at']).strftime("%d.%m.%Y")
        remaining_time = subscription_info['expires_at'] - now
        remaining_str = format_duration(remaining_time)
        subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_info['token']}"
        
        # Получаем информацию о трафике
        repo = SubscriptionRepository()
        traffic_usage_bytes = repo.get_subscription_traffic_sum(subscription_info['id'])
        traffic_limit_bytes = repo.get_subscription_traffic_limit(subscription_info['id'])
        
        # Форматируем информацию о трафике
        if traffic_limit_bytes and traffic_limit_bytes > 0:
            remaining_bytes = max(0, traffic_limit_bytes - (traffic_usage_bytes or 0))
            remaining_traffic_formatted = _format_bytes_short(remaining_bytes)
            traffic_info = f"📊 Осталось трафика: {remaining_traffic_formatted}"
        else:
            traffic_info = "📊 Осталось трафика: без ограничений"
        
        msg += (
            f"📋 *Ваша подписка (коснитесь, чтобы скопировать):*\n\n"
            f"🔗 `{subscription_url}`\n\n"
            f"⏳ Осталось времени: {remaining_str} (до {expiry_date})\n\n"
            f"{traffic_info}\n\n"
            f"📱 [App Store](https://apps.apple.com/ru/app/v2raytun/id6476628951) | [Google Play](https://play.google.com/store/apps/details?id=com.v2raytun.android)\n\n"
            f"💡 Как использовать:\n"
            f"1. Откройте приложение\n"
            f"2. Нажмите \"+\" → \"Добавить из буфера\" или \"Импорт подписки\"\n"
            f"3. Вставьте ссылку выше\n"
            f"4. Все серверы будут добавлены автоматически\n\n"
        )
        
        if all_keys:
            msg += "─────────────────────\n\n"
    
    if not all_keys and not subscription_info:
        main_menu = get_main_menu(user_id)
        await message.answer("У вас нет активных ключей.", reply_markup=main_menu)
        return
    
    if all_keys:
        msg += "*Запасные ключи (потребуется скачать другое приложение):*\n\n"
    
    for key in all_keys:
        remaining_seconds = key['expiry'] - now
        time_str = format_duration(remaining_seconds)
        
        protocol_info = PROTOCOLS[key['protocol']]
        limit_mb = key.get('traffic_limit_mb') or 0
        usage_bytes = key.get('traffic_usage_bytes')
        remaining_line = "📊 Осталось трафика: без ограничений"
        if limit_mb and limit_mb > 0:
            limit_bytes = int(limit_mb * 1024 * 1024)
            usage = int(usage_bytes or 0)
            remaining_bytes = max(limit_bytes - usage, 0)
            remaining_line = (
                f"📊 Осталось трафика: {_format_bytes_short(remaining_bytes)} из "
                f"{_format_bytes_short(limit_bytes)}"
            )
        elif usage_bytes:
            remaining_line = f"📊 Израсходовано: {_format_bytes_short(usage_bytes)}"
        
        # Получаем ссылки на приложения в зависимости от протокола
        if key['protocol'] == 'outline':
            app_links = "📱 [App Store](https://apps.apple.com/app/outline-app/id1356177741) | [Google Play](https://play.google.com/store/apps/details?id=org.outline.android.client)"
        else:  # v2ray
            app_links = "📱 [App Store](https://apps.apple.com/ru/app/v2raytun/id6476628951) | [Google Play](https://play.google.com/store/apps/details?id=com.v2raytun.android)"
            
        msg += (
            f"{protocol_info['icon']} *{protocol_info['name']}*\n"
            f"🌍 Страна: {key['country']}\n"
            f"`{key['config']}`\n"
            f"⏳ Осталось времени: {time_str}\n"
            f"{remaining_line}\n"
            f"{app_links}\n\n"
        )
    
    main_menu = get_main_menu(user_id)
    await message.answer(msg, reply_markup=main_menu, disable_web_page_preview=True, parse_mode="Markdown")

def register_keys_handler(dp: Dispatcher):
    """Регистрация обработчика кнопки "Мои ключи" """
    @dp.message_handler(lambda m: m.text == "Мои ключи")
    @rate_limit("keys")
    async def keys_handler(message: types.Message):
        await handle_my_keys_btn(message)
    
    return keys_handler

