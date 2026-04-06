"""Интеграционные тесты для bot.handlers.purchase"""
from contextlib import contextmanager

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.handlers import purchase as purchase_module


class DummyDispatcher:
    """Простой dp, который сохраняет зарегистрированные message handlers"""

    def __init__(self):
        self.message_handlers = []

    def message_handler(self, *args, **kwargs):  # pragma: no cover - простая обёртка
        def decorator(func):
            self.message_handlers.append(func)
            return func

        return decorator


@pytest.fixture
def dummy_dependencies(monkeypatch):
    """Заменяет внешние зависимости на простые заглушки"""

    monkeypatch.setattr(purchase_module, "get_protocol_selection_menu", lambda: "PROTOCOL_MENU")
    monkeypatch.setattr(purchase_module, "get_country_menu", lambda countries: f"COUNTRY_MENU:{','.join(countries)}")
    monkeypatch.setattr(purchase_module, "get_payment_method_keyboard", lambda: "PAYMENT_MENU")
    monkeypatch.setattr(purchase_module, "get_tariff_menu", lambda **kwargs: MagicMock(keyboard=[["Тариф 1"], ["🔙 Назад"]]))

    rate_limit_stub = lambda *a, **k: (lambda f: f)  # noqa: E731
    monkeypatch.setattr(purchase_module, "rate_limit", rate_limit_stub)


def register_handlers(temp_db, monkeypatch):
    dp = DummyDispatcher()

    @contextmanager
    def fake_cursor():
        cursor = temp_db.cursor()
        try:
            yield cursor
        finally:
            temp_db.commit()

    monkeypatch.setattr(purchase_module, "get_db_cursor", fake_cursor)

    user_states = {}
    bot = AsyncMock()

    purchase_module.register_purchase_handlers(
        dp=dp,
        user_states=user_states,
        bot=bot,
        main_menu=lambda user_id: "MAIN_MENU",
        cancel_keyboard=lambda: "CANCEL_MENU",
        is_valid_email=lambda email: "@" in email,
        create_payment_with_email_and_protocol=AsyncMock(),
        create_new_key_flow_with_protocol=AsyncMock(),
        handle_free_tariff_with_protocol=AsyncMock(),
        handle_invite_friend=AsyncMock(),
        get_tariff_by_name_and_price=AsyncMock(return_value=None),
    )

    handlers = {func.__name__: func for func in dp.message_handlers}
    return handlers, user_states, bot


@pytest.mark.asyncio
async def test_handle_buy_menu_no_servers(temp_db, mock_message, monkeypatch, dummy_dependencies):
    handlers, user_states, _ = register_handlers(temp_db, monkeypatch)

    mock_message.text = "Купить доступ"

    await handlers["handle_buy_menu"](mock_message)

    mock_message.answer.assert_awaited()
    answer_text = mock_message.answer.await_args_list[0].args[0]
    assert "нет доступных серверов" in answer_text.lower()
    assert user_states == {}


@pytest.mark.asyncio
async def test_handle_buy_menu_single_protocol_country(temp_db, mock_message, monkeypatch, dummy_dependencies):
    cursor = temp_db.cursor()
    cursor.execute(
        """
        INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path,
                             country, protocol, active, available_for_purchase, max_keys)
        VALUES (1, 'Server 1', 'https://server.test', 'sha256', 'server.test', 'key', '/v2ray',
                'Россия', 'v2ray', 1, 1, 100)
        """
    )
    temp_db.commit()

    monkeypatch.setattr(purchase_module, "get_countries_by_protocol", lambda protocol: ["Россия"])

    handlers, user_states, _ = register_handlers(temp_db, monkeypatch)

    mock_message.text = "Купить доступ"

    await handlers["handle_buy_menu"](mock_message)

    assert user_states[mock_message.from_user.id]["state"] == "waiting_payment_method_after_country"
    last_message = mock_message.answer.await_args_list[-1].args[0]
    assert "выберите способ оплаты" in last_message.lower()


@pytest.mark.asyncio
async def test_handle_payment_method_after_country_to_tariff(temp_db, mock_message, monkeypatch, dummy_dependencies):
    monkeypatch.setattr(purchase_module, "get_countries_by_protocol", lambda protocol: ["Россия"])

    handlers, user_states, _ = register_handlers(temp_db, monkeypatch)

    user_id = mock_message.from_user.id
    user_states[user_id] = {
        "state": "waiting_payment_method_after_country",
        "country": "Россия",
        "protocol": "v2ray",
    }

    mock_message.text = "💳 Карта РФ / Карта зарубеж / СБП"

    await handlers["handle_payment_method_after_country"](mock_message)

    assert user_states[user_id]["state"] == "waiting_tariff"
    reply_text = mock_message.answer.await_args_list[-1].args[0]
    assert "оплата картой" in reply_text.lower()
