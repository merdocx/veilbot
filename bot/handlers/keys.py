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
from bot.services.subscription_service import SubscriptionService
from bot.utils.subscription_links import subscription_mirror_fallback_markdown

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
            SELECT id, subscription_token, expires_at, tariff_id, traffic_limit_mb
            FROM subscriptions
            WHERE user_id = ? AND is_active = 1 AND expires_at > ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (user_id, now))
        subscription = cursor.fetchone()
        
        if subscription:
            subscription_id, token, expires_at, tariff_id, sub_limit_mb = subscription
            
            # Получаем количество серверов в подписке
            # ВАЖНО: expiry_at берется из подписки, не из ключей
            cursor.execute("""
                SELECT COUNT(DISTINCT server_id)
                FROM v2ray_keys
                WHERE subscription_id = ?
            """, (subscription_id,))
            server_count = cursor.fetchone()[0] or 0
            
            # Эффективный лимит:
            # 1) если в подписке задан traffic_limit_mb (включая 0 как "безлимит"), используем его
            # 2) иначе берём лимит из тарифа
            effective_limit_mb: Optional[int] = None
            if sub_limit_mb is not None:
                effective_limit_mb = int(sub_limit_mb or 0)
            elif tariff_id:
                cursor.execute("SELECT traffic_limit_mb FROM tariffs WHERE id = ?", (tariff_id,))
                tariff_row = cursor.fetchone()
                if tariff_row and tariff_row[0] is not None:
                    effective_limit_mb = int(tariff_row[0] or 0)
            
            # Формируем информацию о лимите трафика
            if effective_limit_mb and effective_limit_mb > 0:
                traffic_limit = f"{effective_limit_mb} ГБ"
            else:
                traffic_limit = "без ограничений"
            
            subscription_info = {
                'id': subscription_id,
                'token': token,
                'expires_at': expires_at,
                'server_count': server_count,
                'traffic_limit': traffic_limit,
            }
    
    with get_db_cursor() as cursor:
        # Отдельные V2Ray-ключи (vless), если есть сохранённый client_config
        if subscription_info:
            cursor.execute("""
                SELECT k.client_config, COALESCE(sub.expires_at, 0) as expiry_at, s.country, k.subscription_id
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND sub.expires_at > ? AND k.subscription_id = ?
                  AND k.client_config IS NOT NULL AND TRIM(k.client_config) != ''
            """, (user_id, now, subscription_info['id']))
        else:
            cursor.execute("""
                SELECT k.client_config, COALESCE(sub.expires_at, 0) as expiry_at, s.country, k.subscription_id
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND (sub.expires_at > ? OR sub.expires_at IS NULL)
                  AND k.client_config IS NOT NULL AND TRIM(k.client_config) != ''
            """, (user_id, now))
        v2ray_rows = cursor.fetchall()

    for key_row in v2ray_rows:
        client_config, exp, country, sub_id = key_row
        all_keys.append({
            'type': 'v2ray',
            'config': client_config,
            'expiry': exp,
            'protocol': 'v2ray',
            'country': country,
            'subscription_id': sub_id,
        })

    # Формируем сообщение
    msg = ""
    
    # Если есть подписка, показываем её первой
    if subscription_info:
        from datetime import datetime
        subscription_url = f"https://veil-bot.ru/api/subscription/{subscription_info['token']}"
        
        # Формируем информацию о сроке действия
        expiry_date = datetime.fromtimestamp(subscription_info['expires_at']).strftime("%d.%m.%Y")
        remaining_time = subscription_info['expires_at'] - now
        remaining_str = format_duration(remaining_time)
        time_info = f"⏳ Осталось времени: {remaining_str} (до {expiry_date})"
        
        # Получаем информацию о трафике
        traffic_state = SubscriptionService().get_subscription_traffic_state(subscription_info['id'])
        
        # Форматируем информацию о трафике
        if not traffic_state.is_unlimited:
            remaining_traffic_formatted = _format_bytes_short(traffic_state.remaining_bytes)
            traffic_info = f"📊 Осталось трафика: {remaining_traffic_formatted}"
        else:
            traffic_info = "📊 Осталось трафика: без ограничений"
        
        msg += (
            f"📋 *Ваша подписка (коснитесь, чтобы скопировать):*\n\n"
            f"🔗 `{subscription_url}`\n\n"
            f"{subscription_mirror_fallback_markdown(subscription_info['token'])}"
            f"{time_info}\n\n"
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
        msg += "*Отдельные ключи серверов (V2Ray):*\n\n"
    
    for key in all_keys:
        remaining_seconds = key['expiry'] - now
        time_str = format_duration(remaining_seconds)
        
        protocol_info = PROTOCOLS['v2ray']
        
        # Вся информация о трафике берется из подписки
        remaining_line = "📊 Осталось трафика: без ограничений"
        subscription_id = key.get('subscription_id')
        if subscription_id:
            traffic_state = SubscriptionService().get_subscription_traffic_state(subscription_id)
            
            if not traffic_state.is_unlimited:
                remaining_line = (
                    f"📊 Осталось трафика: {_format_bytes_short(traffic_state.remaining_bytes)} из "
                    f"{_format_bytes_short(traffic_state.limit_bytes)}"
                )
            elif traffic_state.usage_bytes:
                remaining_line = f"📊 Израсходовано: {_format_bytes_short(traffic_state.usage_bytes)}"
        
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

