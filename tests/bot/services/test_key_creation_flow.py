"""Тесты для create_new_key_flow_with_protocol"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.services import key_creation as key_creation_module


@pytest.fixture(autouse=True)
def reset_vpn_protocols_available(monkeypatch):
    """Сбрасывает глобальное значение VPN_PROTOCOLS_AVAILABLE перед каждым тестом."""
    monkeypatch.setattr(key_creation_module, "VPN_PROTOCOLS_AVAILABLE", True, raising=False)


@pytest.mark.asyncio
async def test_create_new_outline_key_inserts_record(temp_db, mock_message, monkeypatch):
    """Создание нового Outline ключа вставляет запись и отправляет сообщение пользователю."""
    cursor = temp_db.cursor()

    # Подготовка данных
    cursor.execute(
        """
        INSERT INTO tariffs (id, name, price_rub, duration_sec, traffic_limit_mb)
        VALUES (?, ?, ?, ?, ?)
        """,
        (1, "Тест", 100.0, 3600, 0),
    )
    cursor.execute(
        """
        INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path,
                             country, protocol, active, available_for_purchase, max_keys)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Server 1",
            "https://server.test",
            "sha256",
            "server.test",
            "api-key",
            "/v2ray",
            "Россия",
            "outline",
            1,
            1,
            100,
        ),
    )
    temp_db.commit()

    class DummyProtocol:
        async def create_user(self, email):
            return {"id": "outline-123", "accessUrl": "ss://outline-config"}

    dummy_factory = SimpleNamespace(create_protocol=lambda protocol, config: DummyProtocol())

    security_logger = MagicMock()

    monkeypatch.setattr(key_creation_module, "ProtocolFactory", dummy_factory)
    monkeypatch.setattr(key_creation_module, "get_security_logger", lambda: security_logger)
    monkeypatch.setattr(key_creation_module, "get_main_menu", lambda: "MAIN_MENU")
    monkeypatch.setattr(key_creation_module, "get_bot_instance", lambda: AsyncMock())
    monkeypatch.setattr(key_creation_module, "get_vpn_service", lambda: object())

    async def _noop(*args, **kwargs):  # pragma: no cover - вспомогательная функция
        return False

    async def _record(*args, **kwargs):  # pragma: no cover - вспомогательная функция
        return True

    user_states = {}
    tariff = {"id": 1, "duration_sec": 3600, "name": "Тест", "price_rub": 100.0}

    await key_creation_module.create_new_key_flow_with_protocol(
        cursor,
        mock_message,
        user_id=12345,
        tariff=tariff,
        email="user@example.com",
        country="Россия",
        protocol="outline",
        user_states=user_states,
        extend_existing_key_with_fallback=_noop,
        change_country_and_extend=_noop,
        switch_protocol_and_extend=_noop,
        record_free_key_usage=_record,
    )

    cursor.execute("SELECT access_url, tariff_id FROM keys WHERE user_id = ?", (12345,))
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "ss://outline-config"
    assert row[1] == 1
    assert user_states == {}

    assert mock_message.answer.await_count >= 2
    final_message = mock_message.answer.await_args_list[-1].args[0]
    assert "Ваш ключ" in final_message


@pytest.mark.asyncio
async def test_create_new_key_no_servers_notifies_user(temp_db, monkeypatch):
    """Если нет доступных серверов, пользователь получает уведомление."""
    cursor = temp_db.cursor()

    bot_mock = AsyncMock()
    monkeypatch.setattr(key_creation_module, "get_bot_instance", lambda: bot_mock)
    monkeypatch.setattr(key_creation_module, "get_main_menu", lambda: "MAIN_MENU")
    monkeypatch.setattr(key_creation_module, "get_vpn_service", lambda: object())
    monkeypatch.setattr(key_creation_module, "ProtocolFactory", SimpleNamespace(create_protocol=lambda *_: None))
    monkeypatch.setattr(key_creation_module, "get_security_logger", lambda: None)

    async def _noop(*args, **kwargs):  # pragma: no cover - вспомогательная функция
        return False

    user_states = {}
    tariff = {"id": 1, "duration_sec": 3600, "name": "Тест", "price_rub": 100.0}

    await key_creation_module.create_new_key_flow_with_protocol(
        cursor,
        message=None,
        user_id=54321,
        tariff=tariff,
        email="user@example.com",
        country="Россия",
        protocol="outline",
        user_states=user_states,
        extend_existing_key_with_fallback=_noop,
        change_country_and_extend=_noop,
        switch_protocol_and_extend=_noop,
        record_free_key_usage=_noop,
    )

    assert bot_mock.send_message.await_count == 1
    msg_text = bot_mock.send_message.await_args_list[0].args[1]
    assert "Нет доступных серверов" in msg_text


@pytest.mark.asyncio
async def test_create_new_key_prompts_for_country(temp_db, mock_message, monkeypatch):
    """Если страна не указана, запрашивается выбор на основе истории."""
    cursor = temp_db.cursor()
    now = int(time.time())

    cursor.execute(
        """
        INSERT INTO tariffs (id, name, price_rub, duration_sec)
        VALUES (?, ?, ?, ?)
        """,
        (1, "Тест", 100.0, 3600),
    )
    cursor.execute(
        """
        INSERT INTO servers (id, name, api_url, cert_sha256, domain, api_key, v2ray_path,
                             country, protocol, active, available_for_purchase, max_keys)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Server 1",
            "https://server.test",
            "sha256",
            "server.test",
            "api-key",
            "/v2ray",
            "Россия",
            "outline",
            1,
            1,
            100,
        ),
    )
    cursor.execute(
        """
        INSERT INTO keys (id, server_id, user_id, access_url, expiry_at, key_id,
                          created_at, email, tariff_id, protocol)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            1,
            999,
            "ss://old",
            now - 90000,  # истёк >24 часов назад
            "old-key",
            now - 200000,
            "old@example.com",
            1,
            "outline",
        ),
    )
    temp_db.commit()

    monkeypatch.setattr(key_creation_module, "get_countries_by_protocol", lambda protocol: ["Россия", "США"])
    monkeypatch.setattr(key_creation_module, "get_main_menu", lambda: "MAIN_MENU")
    monkeypatch.setattr(key_creation_module, "get_bot_instance", lambda: AsyncMock())
    monkeypatch.setattr(key_creation_module, "get_vpn_service", lambda: object())
    monkeypatch.setattr(key_creation_module, "ProtocolFactory", SimpleNamespace(create_protocol=lambda *_: None))
    monkeypatch.setattr(key_creation_module, "get_security_logger", lambda: None)

    async def _noop(*args, **kwargs):  # pragma: no cover - вспомогательная функция
        return False

    user_states = {}
    tariff = {"id": 1, "duration_sec": 3600, "name": "Тест", "price_rub": 100.0}

    await key_creation_module.create_new_key_flow_with_protocol(
        cursor,
        mock_message,
        user_id=999,
        tariff=tariff,
        email="user@example.com",
        country=None,
        protocol="outline",
        user_states=user_states,
        extend_existing_key_with_fallback=_noop,
        change_country_and_extend=_noop,
        switch_protocol_and_extend=_noop,
        record_free_key_usage=_noop,
    )

    assert user_states[999]["state"] == "reactivation_country_selection"
    prompt_message = mock_message.answer.await_args_list[-1].args[0]
    assert "Выберите страну" in prompt_message
