"""
Обработчик кнопки "Мои ключи"
"""
import time
import logging
from typing import Optional
from aiogram import Dispatcher, types
from utils import get_db_cursor
from config import PROTOCOLS
from vpn_protocols import format_duration, ProtocolFactory, normalize_vless_host
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
    keys_to_update = []  # Список ключей, для которых нужно обновить конфигурацию в БД
    
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
        cursor.execute("""
            SELECT k.access_url, k.expiry_at, k.protocol, s.country, k.traffic_limit_mb
            FROM keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ?
        """, (user_id, now))
        outline_keys = cursor.fetchall()
        
        # Получаем V2Ray ключи БЕЗ подписки (отдельные ключи)
        cursor.execute("""
            SELECT k.v2ray_uuid, k.expiry_at, s.domain, s.v2ray_path, s.country, k.email, s.api_url, s.api_key, k.client_config,
                   k.traffic_limit_mb, k.traffic_usage_bytes, k.traffic_over_limit_at
            FROM v2ray_keys k
            JOIN servers s ON k.server_id = s.id
            WHERE k.user_id = ? AND k.expiry_at > ? AND k.subscription_id IS NULL
        """, (user_id, now))
        v2ray_keys = cursor.fetchall()
    
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
    
    # Добавляем V2Ray ключи
    for (
        v2ray_uuid,
        exp,
        domain,
        path,
        country,
        email,
        api_url,
        api_key,
        saved_config,
        limit_mb,
        usage_bytes,
        over_limit_at,
    ) in v2ray_keys:
        config = None
        normalized_saved = None

        if saved_config and 'vless://' in saved_config:
            lines = saved_config.split('\n')
            for line in lines:
                if line.strip().startswith('vless://'):
                    normalized_saved = normalize_vless_host(
                        line.strip(),
                        domain,
                        api_url or ''
                    )
                    break
            else:
                normalized_saved = normalize_vless_host(
                    saved_config.strip(),
                    domain,
                    api_url or ''
                )
        elif saved_config:
            normalized_saved = saved_config.strip()

        # Всегда пытаемся получить актуальную конфигурацию с сервера
        if api_url and api_key:
            server_config = {'api_url': api_url, 'api_key': api_key}
            try:
                protocol_client = ProtocolFactory.create_protocol('v2ray', server_config)
                fetched_config = await protocol_client.get_user_config(v2ray_uuid, {
                    'domain': domain,
                    'port': 443,
                    'path': path or '/v2ray',
                    'email': email or f"user_{user_id}@veilbot.com"
                })
                if 'vless://' in fetched_config:
                    lines = fetched_config.split('\n')
                    for line in lines:
                        if line.strip().startswith('vless://'):
                            fetched_config = line.strip()
                            break
                fetched_config = normalize_vless_host(
                    fetched_config.strip(),
                    domain,
                    api_url or ''
                )

                config = fetched_config
                if not normalized_saved or normalized_saved != fetched_config:
                    keys_to_update.append((fetched_config, v2ray_uuid))
                    logging.info(
                        "[MY_KEYS] Refreshed client_config for UUID %s (updated DB)",
                        v2ray_uuid[:8],
                    )
            except Exception as e:
                logging.error(f"Error getting V2Ray config for {v2ray_uuid}: {e}")

        if not config:
            config = normalized_saved

        if not config:
            # Fallback к старому формату при отсутствии данных
            config = (
                f"vless://{v2ray_uuid}@{domain or 'example.com'}:443"
                "?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome"
                "&pbk=TJcEEU2FS6nX_mBo-qXiuq9xBaP1nAcVia1MlYyUHWQ&sid=827d3b463ef6638f"
                f"&spx=/&type=tcp&flow=#{email or 'VeilBot-V2Ray'}"
            )
        
        all_keys.append({
            'type': 'v2ray',
            'config': config,
            'expiry': exp,
            'protocol': 'v2ray',
            'country': country,
            'traffic_limit_mb': limit_mb or 0,
            'traffic_usage_bytes': usage_bytes if usage_bytes is not None else 0,
            'traffic_over_limit_at': over_limit_at,
        })
    
    # Обновляем конфигурации в БД, если нужно
    if keys_to_update:
        with get_db_cursor(commit=True) as cursor:
            for config, v2ray_uuid in keys_to_update:
                cursor.execute("UPDATE v2ray_keys SET client_config = ? WHERE v2ray_uuid = ?", (config, v2ray_uuid))

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
            f"📋 Ваша подписка:\n\n"
            f"🔗 {subscription_url}\n\n"
            f"⏳ Осталось времени: {remaining_str} (до {expiry_date})\n\n"
            f"{traffic_info}\n\n"
            f"📱 [App Store](https://apps.apple.com/ru/app/v2raytun/id6476628951) | [Google Play](https://play.google.com/store/apps/details?id=com.v2raytun.android)\n\n"
            f"💡 Как использовать:\n"
            f"1. Откройте приложение V2Ray\n"
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
        msg += "*Отдельные ключи:*\n\n"
    
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

