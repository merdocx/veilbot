"""Тесты форматирования уведомлений администратору."""
import pytest

from bot.services.admin_notifications import (
    format_amount_rub_from_kopecks,
    format_background_task_error_markdown,
    format_background_task_error_plain,
    format_bot_error_markdown,
    format_bot_error_plain,
    format_purchase_notification_markdown,
    format_purchase_notification_plain,
)


def test_format_amount_rub_from_kopecks_whole_rubles():
    assert format_amount_rub_from_kopecks(2_000_000) == "20 000 ₽"


def test_format_amount_rub_from_kopecks_with_kopecks():
    assert "1 234,56 ₽" in format_amount_rub_from_kopecks(123456)


def test_format_amount_rub_from_kopecks_invalid():
    assert format_amount_rub_from_kopecks("not-a-number") == "—"


def test_format_purchase_notification_plain_and_markdown():
    md = format_purchase_notification_markdown(
        user_id=1064926530,
        tariff_name="1 мес",
        amount_kopecks=200_00,
        payment_method="YooKassa",
        expires_date="26.04.2026 11:07",
        purchase_type="продление",
        payment_id="31545aa5-000f-5001-8000-1a625a5299e0",
    )
    plain = format_purchase_notification_plain(
        user_id=1064926530,
        tariff_name="1 мес",
        amount_kopecks=200_00,
        payment_method="YooKassa",
        expires_date="26.04.2026 11:07",
        purchase_type="продление",
        payment_id="31545aa5-000f-5001-8000-1a625a5299e0",
    )
    assert "1064926530" in plain
    assert "`1064926530`" in md
    assert "200 ₽" in plain
    assert "YooKassa" in md
    assert "31545aa5" in plain


def test_format_background_task_error():
    err = ValueError("boom")
    md = format_background_task_error_markdown("auto_delete_expired_keys", err)
    plain = format_background_task_error_plain("auto_delete_expired_keys", err)
    assert "auto_delete_expired_keys" in md
    assert "ValueError" in plain
    assert "boom" in plain


def test_format_bot_error():
    exc = RuntimeError("fail")
    md = format_bot_error_markdown(
        context="handle_buy_menu",
        exception=exc,
        user_id=42,
        username="u",
        traceback_str="line1\nline2",
    )
    plain = format_bot_error_plain(
        context="handle_buy_menu",
        exception=exc,
        user_id=42,
        username="u",
        traceback_str="line1\nline2",
    )
    assert "handle_buy_menu" in md
    assert "42" in plain
    assert "@u" in md
    assert "line1" in plain


@pytest.mark.asyncio
async def test_send_admin_message_uses_http_when_no_bot(monkeypatch):
    from bot.services import admin_notifications as an

    calls = []

    def fake_get_bot():
        return None

    async def fake_http(token, chat_id, text, **kwargs):
        calls.append((chat_id, text))
        return True

    monkeypatch.setattr(an, "get_bot_instance", fake_get_bot)
    monkeypatch.setattr(an.settings, "ADMIN_ID", 999)
    monkeypatch.setattr(an.settings, "TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setattr(an, "_send_via_telegram_api", fake_http)

    ok = await an.send_admin_message(
        "*hi*",
        text_plain="hi",
        admin_id=999,
    )
    assert ok is True
    assert calls == [(999, "hi")]
