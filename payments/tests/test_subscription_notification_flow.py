"""
Тесты порядка операций при покупке подписки (реальная async SQLite + мок платежа/бота).

Текущее поведение process_subscription_purchase:
- атомарный захват paid -> processing_subscription;
- подписка и ключи через реальную БД;
- пользовательское уведомление не должно ронять весь процесс при сбое отправки.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from datetime import datetime, timezone

from payments.models.payment import Payment, PaymentStatus
from payments.services.subscription_purchase_service import SubscriptionPurchaseService


def _create_notification_test_db(db_path: Path) -> None:
    """Минимальная схема для прохождения _get_or_create_subscription и _create_keys_for_subscription."""
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.executescript(
            """
            PRAGMA foreign_keys = OFF;
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                is_vip INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                api_url TEXT,
                cert_sha256 TEXT,
                domain TEXT,
                api_key TEXT,
                v2ray_path TEXT,
                country TEXT,
                protocol TEXT DEFAULT 'v2ray',
                active INTEGER DEFAULT 1,
                max_keys INTEGER DEFAULT 100,
                access_level TEXT DEFAULT 'all',
                subscription_group_id TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subscription_token TEXT,
                created_at INTEGER,
                expires_at INTEGER,
                tariff_id INTEGER,
                is_active INTEGER DEFAULT 1,
                last_updated_at INTEGER,
                notified INTEGER DEFAULT 0,
                purchase_notification_sent INTEGER DEFAULT 0,
                traffic_limit_mb INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS v2ray_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                user_id INTEGER,
                v2ray_uuid TEXT,
                created_at INTEGER,
                email TEXT,
                tariff_id INTEGER,
                client_config TEXT,
                subscription_id INTEGER,
                traffic_limit_mb INTEGER DEFAULT 0,
                traffic_usage_bytes INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                tariff_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT DEFAULT 'RUB',
                email TEXT,
                status TEXT DEFAULT 'pending',
                country TEXT,
                protocol TEXT DEFAULT 'v2ray',
                provider TEXT DEFAULT 'yookassa',
                method TEXT,
                description TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                paid_at INTEGER,
                metadata TEXT,
                subscription_id INTEGER
            );
            """
        )
        c.execute(
            "INSERT OR REPLACE INTO users (user_id, username, is_vip) VALUES (12345, 't', 0)"
        )
        c.execute(
            """
            INSERT INTO servers (id, name, api_url, api_key, domain, v2ray_path, protocol, active, cert_sha256)
            VALUES (1, 'Server1', 'http://api', 'key', 'example.com', '/', 'v2ray', 1, NULL)
            """
        )
        conn.commit()
    finally:
        conn.close()


def _patch_claim_and_get_payment(subscription_service, payment: Payment, call_order: list | None = None):
    """Атомарный захват paid -> processing_subscription (и далее COMPLETED) для моков репозитория."""

    async def try_update_side_effect(pid, new_status, expected_status):
        if payment.payment_id != pid:
            return False
        if payment.status != expected_status:
            return False
        if call_order is not None and new_status == PaymentStatus.COMPLETED:
            call_order.append(("payment_completed",))
        payment.status = new_status
        return True

    return (
        patch.object(
            subscription_service.payment_repo,
            "get_by_payment_id",
            AsyncMock(return_value=payment),
        ),
        patch.object(
            subscription_service.payment_repo,
            "try_update_status",
            AsyncMock(side_effect=try_update_side_effect),
        ),
    )


def _notification_patches():
    """Стабильные стабы для Telegram-разметки и отправки в тестах."""
    return (
        patch(
            "payments.services.subscription_purchase_service.get_main_menu",
            return_value={"keyboard": [[{"text": "x"}]], "resize_keyboard": True},
        ),
        patch(
            "payments.services.subscription_purchase_service.reset_subscription_traffic",
            AsyncMock(return_value=True),
        ),
        patch.object(
            SubscriptionPurchaseService,
            "_verify_subscription_consistency",
            AsyncMock(return_value=None),
        ),
    )


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock())
    return bot


@pytest.fixture
def subscription_service(mock_bot, tmp_path):
    db_path = tmp_path / "notification_flow.db"
    _create_notification_test_db(db_path)
    with patch(
        "payments.services.subscription_purchase_service.get_bot_instance",
        return_value=mock_bot,
    ):
        with patch(
            "bot.services.admin_notifications.get_bot_instance",
            return_value=mock_bot,
        ):
            yield SubscriptionPurchaseService(db_path=str(db_path))


@pytest.mark.asyncio
async def test_subscription_notification_sent_immediately_after_creation(
    subscription_service, mock_bot
):
    """После успешной обработки платёж завершается (completed), уведомление идёт через safe_send_message."""
    payment_id = "test_payment_123"
    user_id = 12345

    payment = Payment(
        payment_id=payment_id,
        user_id=user_id,
        tariff_id=1,
        amount=10000,
        email="test@example.com",
        status=PaymentStatus.PAID,
        protocol="v2ray",
        paid_at=datetime.now(timezone.utc),
        metadata={"key_type": "subscription"},
    )

    mock_protocol_client = AsyncMock()
    mock_protocol_client.create_user = AsyncMock(return_value={"uuid": "test-uuid"})
    mock_protocol_client.get_user_config = AsyncMock(return_value="vless://test-config\n")
    mock_protocol_client.close = AsyncMock()

    get_payment_p, try_claim_p = _patch_claim_and_get_payment(subscription_service, payment)
    p_main, p_reset, p_verify = _notification_patches()

    with get_payment_p, try_claim_p, p_main, p_reset, p_verify:
        with patch.object(
            subscription_service.payment_repo, "update_subscription_id", AsyncMock(return_value=None)
        ):
            with patch.object(
                subscription_service.tariff_repo,
                "get_tariff",
                return_value=(1, "Test Tariff", 86400, 100, 0),
            ):
                with patch(
                    "payments.services.subscription_purchase_service.ProtocolFactory.create_protocol",
                    return_value=mock_protocol_client,
                ):
                    with patch(
                        "payments.services.subscription_purchase_service.safe_send_message",
                        new_callable=AsyncMock,
                        return_value=True,
                    ) as mock_safe:
                        success, error_msg = await subscription_service.process_subscription_purchase(
                            payment_id
                        )

                        assert success is True
                        assert error_msg is None
                        mock_safe.assert_called()
                        assert payment.status == PaymentStatus.COMPLETED


@pytest.mark.asyncio
async def test_payment_completes_even_if_user_notification_returns_false(
    subscription_service, mock_bot
):
    """Если safe_send_message вернул False, покупка всё равно успешна; платёж completed (неблокирующее уведомление)."""
    payment_id = "test_payment_456"
    user_id = 12345

    payment = Payment(
        payment_id=payment_id,
        user_id=user_id,
        tariff_id=1,
        amount=10000,
        email="test@example.com",
        status=PaymentStatus.PAID,
        protocol="v2ray",
        paid_at=datetime.now(timezone.utc),
        metadata={"key_type": "subscription"},
    )

    mock_protocol_client = AsyncMock()
    mock_protocol_client.create_user = AsyncMock(return_value={"uuid": "test-uuid"})
    mock_protocol_client.get_user_config = AsyncMock(return_value="vless://test-config\n")
    mock_protocol_client.close = AsyncMock()

    get_payment_p, try_claim_p = _patch_claim_and_get_payment(subscription_service, payment)
    p_main, p_reset, p_verify = _notification_patches()

    with get_payment_p, try_claim_p, p_main, p_reset, p_verify:
        with patch.object(
            subscription_service.payment_repo, "update_subscription_id", AsyncMock(return_value=None)
        ):
            with patch.object(
                subscription_service.tariff_repo,
                "get_tariff",
                return_value=(1, "Test Tariff", 86400, 100, 0),
            ):
                with patch(
                    "payments.services.subscription_purchase_service.ProtocolFactory.create_protocol",
                    return_value=mock_protocol_client,
                ):
                    with patch(
                        "payments.services.subscription_purchase_service.safe_send_message",
                        new_callable=AsyncMock,
                        return_value=False,
                    ):
                        success, error_msg = await subscription_service.process_subscription_purchase(
                            payment_id
                        )

                        assert success is True
                        assert error_msg is None
                        assert payment.status == PaymentStatus.COMPLETED


@pytest.mark.asyncio
async def test_notification_order_after_subscription_creation(subscription_service, mock_bot):
    """Текущий код: сначала _mark_payment_completed (COMPLETED), затем user notification через safe_send_message."""
    payment_id = "test_payment_789"
    user_id = 12345
    call_order: list = []

    payment = Payment(
        payment_id=payment_id,
        user_id=user_id,
        tariff_id=1,
        amount=10000,
        email="test@example.com",
        status=PaymentStatus.PAID,
        protocol="v2ray",
        paid_at=datetime.now(timezone.utc),
        metadata={"key_type": "subscription"},
    )

    async def tracked_safe_send(*args, **kwargs):
        call_order.append(("send_notification",))
        return True

    mock_protocol_client = AsyncMock()
    mock_protocol_client.create_user = AsyncMock(return_value={"uuid": "test-uuid"})
    mock_protocol_client.get_user_config = AsyncMock(return_value="vless://test-config\n")
    mock_protocol_client.close = AsyncMock()

    get_payment_p, try_claim_p = _patch_claim_and_get_payment(
        subscription_service, payment, call_order=call_order
    )
    p_main, p_reset, p_verify = _notification_patches()

    with get_payment_p, try_claim_p, p_main, p_reset, p_verify:
        with patch.object(
            subscription_service.payment_repo, "update_subscription_id", AsyncMock(return_value=None)
        ):
            with patch.object(
                subscription_service.tariff_repo,
                "get_tariff",
                return_value=(1, "Test Tariff", 86400, 100, 0),
            ):
                with patch(
                    "payments.services.subscription_purchase_service.ProtocolFactory.create_protocol",
                    return_value=mock_protocol_client,
                ):
                    with patch(
                        "payments.services.subscription_purchase_service.safe_send_message",
                        new_callable=AsyncMock,
                        side_effect=tracked_safe_send,
                    ):
                        success, error_msg = await subscription_service.process_subscription_purchase(
                            payment_id
                        )

                        assert success is True
                        assert error_msg is None

                        completed_idx = next(
                            i for i, x in enumerate(call_order) if x[0] == "payment_completed"
                        )
                        send_idx = next(i for i, x in enumerate(call_order) if x[0] == "send_notification")
                        assert completed_idx < send_idx
