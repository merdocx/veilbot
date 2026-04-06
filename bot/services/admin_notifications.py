"""
Единая точка форматирования и отправки уведомлений администратору в Telegram.

Используется вместо разрозненных safe_send_message(..., ADMIN_ID) и дублирования
HTTP fallback из процесса админки (когда get_bot_instance() is None).
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Optional

from app.settings import settings
from bot.core import get_bot_instance
from bot.utils.messaging import safe_send_message

logger = logging.getLogger(__name__)


class AdminNotificationCategory(str, Enum):
    """Категории уведомлений (для логов и единообразных заголовков)."""

    PURCHASE = "purchase"
    INFO = "info"
    OPS_ALERT = "ops_alert"
    ERROR = "error"
    DATA_QUALITY = "data_quality"
    BROADCAST_REPORT = "broadcast_report"


def format_amount_rub_from_kopecks(amount_kopecks: Any) -> str:
    """Форматирует сумму из копеек в рубли для человекочитаемого вывода."""
    try:
        amount_dec = Decimal(str(amount_kopecks))
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    amount_rub = amount_dec / Decimal("100")
    formatted = f"{amount_rub:,.2f}".replace(",", " ").replace(".", ",")
    if formatted.endswith(",00"):
        formatted = formatted[:-3]
    return f"{formatted} ₽"


def format_purchase_notification_markdown(
    *,
    user_id: int,
    tariff_name: str,
    amount_kopecks: Any,
    payment_method: str,
    expires_date: str,
    purchase_type: str,
    payment_id: str,
) -> str:
    amount_rub = format_amount_rub_from_kopecks(amount_kopecks)
    return (
        f"💳 *Покупка подписки*\n\n"
        f"👤 Пользователь: `{user_id}`\n"
        f"📦 Тариф: {tariff_name}\n"
        f"💰 Сумма: {amount_rub}\n"
        f"💳 Способ оплаты: {payment_method}\n"
        f"📅 Действует до: {expires_date}\n"
        f"🔄 Тип: {purchase_type}\n"
        f"🧾 Платеж: `{payment_id}`"
    )


def format_purchase_notification_plain(
    *,
    user_id: int,
    tariff_name: str,
    amount_kopecks: Any,
    payment_method: str,
    expires_date: str,
    purchase_type: str,
    payment_id: str,
) -> str:
    amount_rub = format_amount_rub_from_kopecks(amount_kopecks)
    return (
        f"💳 Покупка подписки\n\n"
        f"👤 Пользователь: {user_id}\n"
        f"📦 Тариф: {tariff_name}\n"
        f"💰 Сумма: {amount_rub}\n"
        f"💳 Способ оплаты: {payment_method}\n"
        f"📅 Действует до: {expires_date}\n"
        f"🔄 Тип: {purchase_type}\n"
        f"🧾 Платеж: {payment_id}"
    )


def format_free_access_info_markdown(
    *,
    user_id: int,
    activated: bool,
    needs_server_check: bool,
) -> str:
    status = (
        "Статус: бесплатный доступ активирован"
        if activated
        else "Статус: бесплатный доступ не активирован"
    )
    text = (
        f"ℹ️ *Информационное сообщение*\n"
        f"Пользователь: `{user_id}`\n"
        f"{status}"
    )
    if needs_server_check:
        text += "\nТребуется проверка серверов."
    return text


def format_free_access_info_plain(
    *,
    user_id: int,
    activated: bool,
    needs_server_check: bool,
) -> str:
    status = (
        "Статус: бесплатный доступ активирован"
        if activated
        else "Статус: бесплатный доступ не активирован"
    )
    text = (
        f"ℹ️ Информационное сообщение\n"
        f"Пользователь: {user_id}\n"
        f"{status}"
    )
    if needs_server_check:
        text += "\nТребуется проверка серверов."
    return text


def format_background_task_error_markdown(task_name: str, error: Exception) -> str:
    return (
        f"⚠️ *Фоновая задача*\n\n"
        f"Задача: `{task_name}`\n"
        f"Ошибка: `{type(error).__name__}: {error}`\n"
        "Повторная попытка будет выполнена автоматически."
    )


def format_background_task_error_plain(task_name: str, error: Exception) -> str:
    return (
        f"⚠️ Фоновая задача\n\n"
        f"Задача: {task_name}\n"
        f"Ошибка: {type(error).__name__}: {error}\n"
        "Повторная попытка будет выполнена автоматически."
    )


def format_key_capacity_low_markdown(free_keys: int) -> str:
    return (
        f"⚠️ *Емкость серверов*\n\n"
        f"Осталось мало свободных слотов под ключи: *{free_keys}*."
    )


def format_key_capacity_low_plain(free_keys: int) -> str:
    return (
        f"⚠️ Емкость серверов\n\n"
        f"Осталось мало свободных слотов под ключи: {free_keys}."
    )


def format_key_capacity_ok_markdown(free_keys: int) -> str:
    return (
        f"✅ *Емкость серверов*\n\n"
        f"Свободных слотов под ключи восстановлено: *{free_keys}*."
    )


def format_key_capacity_ok_plain(free_keys: int) -> str:
    return (
        f"✅ Емкость серверов\n\n"
        f"Свободных слотов под ключи восстановлено: {free_keys}."
    )


def format_bot_error_markdown(
    *,
    context: str,
    exception: Exception,
    user_id: Optional[int],
    username: Optional[str],
    traceback_str: str,
) -> str:
    message = "❌ *Ошибка в боте*\n\n"
    message += f"*Контекст:* {context}\n"
    if user_id is not None:
        message += f"*User ID:* `{user_id}`\n"
    if username:
        message += f"*Username:* @{username}\n"
    message += f"*Тип ошибки:* `{type(exception).__name__}`\n"
    message += f"*Сообщение:* `{str(exception)[:500]}`\n\n"
    message += f"*Traceback:*\n```\n{traceback_str[:1000]}\n```"
    return message


def format_bot_error_plain(
    *,
    context: str,
    exception: Exception,
    user_id: Optional[int],
    username: Optional[str],
    traceback_str: str,
) -> str:
    lines = ["❌ Ошибка в боте", "", f"Контекст: {context}"]
    if user_id is not None:
        lines.append(f"User ID: {user_id}")
    if username:
        lines.append(f"Username: @{username}")
    lines.append(f"Тип ошибки: {type(exception).__name__}")
    lines.append(f"Сообщение: {str(exception)[:500]}")
    lines.append("")
    lines.append("Traceback:")
    lines.append(traceback_str[:1000])
    return "\n".join(lines)


def format_reconcile_result_markdown(*, pending_processed: int, issued_processed: int) -> str:
    return (
        f"🧾 *Реконсиляция платежей*\n\n"
        f"• Pending обработано: {pending_processed}\n"
        f"• Выдано ключей: {issued_processed}\n"
    )


def format_reconcile_result_plain(*, pending_processed: int, issued_processed: int) -> str:
    return (
        f"🧾 Реконсиляция платежей\n\n"
        f"• Pending обработано: {pending_processed}\n"
        f"• Выдано ключей: {issued_processed}\n"
    )


def format_subscription_discrepancy_markdown(
    *,
    user_id: int,
    subscription_id: int,
    tariff_name: str,
    diff_days: float,
    sub_expires_at: str,
    calculated_expires: str,
    payments_count: int,
    bonuses_count: int,
) -> str:
    return (
        f"⚠️ *Расхождение по подписке*\n\n"
        f"👤 Пользователь: `{user_id}`\n"
        f"📋 Подписка: #{subscription_id}\n"
        f"📦 Тариф: {tariff_name}\n"
        f"📊 Расхождение: {diff_days:+.1f} дней\n"
        f"📅 Текущий срок: {sub_expires_at}\n"
        f"📅 Расчетный срок: {calculated_expires}\n"
        f"💳 Платежей: {payments_count}\n"
        f"🎁 Бонусов: {bonuses_count}"
    )


def format_subscription_discrepancy_plain(
    *,
    user_id: int,
    subscription_id: int,
    tariff_name: str,
    diff_days: float,
    sub_expires_at: str,
    calculated_expires: str,
    payments_count: int,
    bonuses_count: int,
) -> str:
    return (
        f"⚠️ Расхождение по подписке\n\n"
        f"👤 Пользователь: {user_id}\n"
        f"📋 Подписка: #{subscription_id}\n"
        f"📦 Тариф: {tariff_name}\n"
        f"📊 Расхождение: {diff_days:+.1f} дней\n"
        f"📅 Текущий срок: {sub_expires_at}\n"
        f"📅 Расчетный срок: {calculated_expires}\n"
        f"💳 Платежей: {payments_count}\n"
        f"🎁 Бонусов: {bonuses_count}"
    )


def format_broadcast_report_markdown(
    *,
    success_count: int,
    failed_count: int,
    total_users: int,
    audience_label: str,
) -> str:
    report = (
        f"📊 *Отчет о рассылке*\n\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Ошибок: {failed_count}\n"
        f"📈 Всего пользователей: {total_users}\n"
        f"🎯 Аудитория: {audience_label}\n"
    )
    if total_users:
        report += f"📊 Процент успеха: {(success_count / total_users * 100):.1f}%"
    return report


def format_broadcast_report_plain(
    *,
    success_count: int,
    failed_count: int,
    total_users: int,
    audience_label: str,
) -> str:
    report = (
        f"📊 Отчет о рассылке\n\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Ошибок: {failed_count}\n"
        f"📈 Всего пользователей: {total_users}\n"
        f"🎯 Аудитория: {audience_label}\n"
    )
    if total_users:
        report += f"📊 Процент успеха: {(success_count / total_users * 100):.1f}%"
    return report


async def _send_via_telegram_api(
    token: str,
    chat_id: int,
    text: str,
    *,
    parse_mode: str = "",
    disable_web_page_preview: bool = True,
    max_retries: int = 3,
) -> bool:
    """Отправка через Telegram Bot API (процесс без aiogram-бота)."""
    import aiohttp

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    last_status = None
    last_body = None
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    last_status = resp.status
                    body = await resp.text()
                    last_body = body
                    if resp.status == 200:
                        logger.info(
                            "[ADMIN_NOTIFY] Telegram API OK chat_id=%s attempt=%s",
                            chat_id,
                            attempt + 1,
                        )
                        return True
                    if resp.status == 429:
                        try:
                            import json

                            data = json.loads(body) if body else {}
                            retry_after = data.get("parameters", {}).get("retry_after", 5)
                        except Exception:
                            retry_after = 5
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_after)
                        continue
                    if resp.status == 400 and parse_mode:
                        payload_no = {
                            "chat_id": chat_id,
                            "text": text,
                            "disable_web_page_preview": disable_web_page_preview,
                        }
                        try:
                            async with aiohttp.ClientSession() as session2:
                                async with session2.post(
                                    url, json=payload_no, timeout=aiohttp.ClientTimeout(total=15)
                                ) as retry_resp:
                                    if retry_resp.status == 200:
                                        logger.info(
                                            "[ADMIN_NOTIFY] Telegram API OK (no parse_mode) chat_id=%s",
                                            chat_id,
                                        )
                                        return True
                        except Exception as e2:
                            logger.warning("[ADMIN_NOTIFY] Retry without parse_mode failed: %s", e2)
                    logger.warning(
                        "[ADMIN_NOTIFY] Telegram API %s chat_id=%s attempt %d/%d: %s",
                        resp.status,
                        chat_id,
                        attempt + 1,
                        max_retries,
                        (body or "")[:300],
                    )
        except Exception as e:
            logger.warning(
                "[ADMIN_NOTIFY] Telegram API exception chat_id=%s attempt %d/%d: %s",
                chat_id,
                attempt + 1,
                max_retries,
                e,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(2**attempt)
            else:
                logger.error(
                    "[ADMIN_NOTIFY] Failed after %d attempts chat_id=%s",
                    max_retries,
                    chat_id,
                    exc_info=True,
                )
                return False

    logger.error(
        "[ADMIN_NOTIFY] Failed chat_id=%s status=%s body=%s",
        chat_id,
        last_status,
        (last_body or "")[:300],
    )
    return False


async def send_admin_message(
    text_markdown: str,
    *,
    text_plain: str,
    admin_id: Optional[int] = None,
    disable_web_page_preview: bool = True,
    category: AdminNotificationCategory = AdminNotificationCategory.INFO,
) -> bool:
    """
    Отправить сообщение администратору: сначала через aiogram, иначе HTTP API.

    text_plain используется для fallback без parse_mode (надёжнее для длинных текстов).
    """
    target_id = admin_id if admin_id is not None else getattr(settings, "ADMIN_ID", None)
    if not target_id:
        logger.debug("[ADMIN_NOTIFY] ADMIN_ID not set, skip category=%s", category.value)
        return False

    token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
    bot = get_bot_instance()

    if bot:
        result = await safe_send_message(
            bot,
            target_id,
            text_markdown,
            parse_mode="Markdown",
            disable_web_page_preview=disable_web_page_preview,
            mark_blocked=False,
        )
        if result is not None:
            logger.debug("[ADMIN_NOTIFY] Sent via bot category=%s", category.value)
            return True
        logger.warning("[ADMIN_NOTIFY] safe_send_message returned None, try HTTP category=%s", category.value)

    if not token:
        logger.warning("[ADMIN_NOTIFY] No TELEGRAM_BOT_TOKEN, cannot HTTP fallback category=%s", category.value)
        return False

    ok = await _send_via_telegram_api(
        token,
        target_id,
        text_plain,
        parse_mode="",
        disable_web_page_preview=disable_web_page_preview,
    )
    if ok:
        logger.info("[ADMIN_NOTIFY] Sent via Telegram API category=%s", category.value)
    return ok
