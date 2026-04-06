"""
Обработчики продления ключей
"""
import time
import logging
from aiogram import Dispatcher, types
from config import PROTOCOLS
from app.infra.sqlite_utils import get_db_cursor
from bot.utils import safe_send_message
from bot.keyboards import get_main_menu, get_payment_method_keyboard, get_country_menu, get_countries_by_protocol
from bot.services.key_creation import create_new_key_flow_with_protocol
from bot_rate_limiter import rate_limit

def register_renewal_handlers(
    dp: Dispatcher,
    user_states: dict,
    bot
):
    """
    Регистрация обработчиков продления ключей
    
    Args:
        dp: Dispatcher aiogram
        user_states: Словарь состояний пользователей
        bot: Экземпляр бота
    """
    
    @dp.callback_query_handler(lambda c: c.data == "buy")
    @rate_limit("renew")
    async def callback_buy_button(callback_query: types.CallbackQuery):
        """Обработчик кнопки 'Продлить' - показывает выбор способа платежа (как при покупке)"""
        user_id = callback_query.from_user.id
        now = int(time.time())
        
        # Находим активный V2Ray ключ (самый новый по сроку действия подписки)
        with get_db_cursor() as cursor:
            cursor.execute("""
                SELECT k.id, COALESCE(sub.expires_at, 0) as expiry_at, s.protocol, s.country
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND sub.expires_at > ?
                ORDER BY sub.expires_at DESC LIMIT 1
            """, (user_id, now))
            current_key = cursor.fetchone()
        
        if not current_key:
            await callback_query.answer("У вас нет активных ключей для продления", show_alert=True)
            return
        
        # Получаем протокол и страну из найденного ключа
        key_id, expiry_at, protocol, country = current_key
        
        # Устанавливаем состояние для выбора способа платежа (как при покупке)
        user_states[user_id] = {
            "state": "waiting_payment_method_after_country",
            "country": country,
            "protocol": protocol,
            "is_renewal": True,  # Флаг, что это продление
            "paid_only": True
        }
        
        # Показываем выбор способа платежа
        msg = f"💳 *Выберите способ оплаты*\n\n"
        msg += f"{PROTOCOLS[protocol]['icon']} {PROTOCOLS[protocol]['name']}\n"
        msg += f"🌍 Страна: *{country}*\n"
        
        await safe_send_message(
            bot,
            user_id,
            msg,
            reply_markup=get_payment_method_keyboard(),
            parse_mode="Markdown"
        )
        
        try:
            await callback_query.answer()
        except Exception:
            pass
    
    @dp.message_handler(lambda m: user_states.get(m.from_user.id, {}).get("state") == "reactivation_country_selection")
    async def handle_reactivation_country_selection(message: types.Message) -> None:
        """Обработчик выбора страны при реактивации истекшего ключа"""
        user_id = message.from_user.id
        text = message.text or ""
        
        # Проверяем, что это кнопка "Отмена"
        if text == "🔙 Отмена":
            user_states.pop(user_id, None)
            await message.answer("Покупка отменена.", reply_markup=get_main_menu(user_id))
            return
        
        # Получаем сохраненное состояние
        state = user_states.get(user_id, {})
        tariff = state.get("tariff")
        email = state.get("email")
        protocol = state.get("protocol", "v2ray")
        last_country = state.get("last_country")
        
        if not tariff:
            await message.answer("Ошибка: данные тарифа не найдены. Попробуйте еще раз.", reply_markup=get_main_menu(user_id))
            user_states.pop(user_id, None)
            return
        
        # Извлекаем название страны из текста
        selected_country = text
        if text.startswith("🔄 ") and "(как раньше)" in text:
            # Убираем "🔄 " и " (как раньше)"
            selected_country = text[2:].replace(" (как раньше)", "")
        
        # Проверяем, что страна доступна для выбранного протокола
        countries = get_countries_by_protocol(protocol)
        if selected_country not in countries:
            await message.answer(
                f"Пожалуйста, выберите страну из списка для {PROTOCOLS[protocol]['name']}:",
                reply_markup=get_country_menu(countries)
            )
            return
        
        # Очищаем состояние и создаем ключ с выбранной страной
        user_states.pop(user_id, None)
        
        # Создаем ключ через существующую функцию
        with get_db_cursor(commit=True) as cursor:
            await create_new_key_flow_with_protocol(cursor, message, user_id, tariff, email, selected_country, protocol)

