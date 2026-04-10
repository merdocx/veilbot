from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from payments.models.payment import Payment, PaymentStatus
from payments.services.subscription_purchase_service import SubscriptionPurchaseService


def _init_db(db_path: Path) -> None:
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
            CREATE TABLE IF NOT EXISTS tariffs (
                id INTEGER PRIMARY KEY,
                name TEXT,
                duration_sec INTEGER,
                price_rub INTEGER,
                traffic_limit_mb INTEGER,
                price_crypto_usd REAL,
                enable_yookassa INTEGER DEFAULT 1,
                enable_platega INTEGER DEFAULT 1,
                enable_cryptobot INTEGER DEFAULT 1,
                is_archived INTEGER DEFAULT 0
            );
            """
        )
        c.execute("INSERT OR REPLACE INTO users (user_id, username, is_vip) VALUES (42, 'u', 0)")
        c.execute(
            """
            INSERT OR REPLACE INTO servers (id, name, api_url, api_key, domain, v2ray_path, protocol, active, cert_sha256)
            VALUES (1, 'Server1', 'http://api', 'key', 'example.com', '/', 'v2ray', 1, NULL)
            """
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock())
    return bot


@pytest.fixture
def service(tmp_path, mock_bot):
    db_path = tmp_path / "grace_renewal.db"
    _init_db(db_path)
    with patch("payments.services.subscription_purchase_service.get_bot_instance", return_value=mock_bot):
        with patch("bot.services.admin_notifications.get_bot_instance", return_value=mock_bot):
            yield SubscriptionPurchaseService(db_path=str(db_path))


@pytest.mark.asyncio
async def test_grace_renewal_extends_from_max_now_expires(service, tmp_path):
    """
    Если подписка истекла, но в grace-периоде, продление должно идти от now (max(now, expires_at)).
    """
    db_path = Path(service.db_path)
    now = int(time.time())

    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute(
            """
            INSERT OR REPLACE INTO tariffs (
                id, name, duration_sec, price_rub, traffic_limit_mb,
                price_crypto_usd, enable_yookassa, enable_platega, enable_cryptobot, is_archived
            ) VALUES (1, 't', 86400, 100, 1024, NULL, 1, 1, 1, 0)
            """
        )
        # expires_at час назад, но в grace (24ч)
        c.execute(
            """
            INSERT INTO subscriptions (id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, notified, traffic_limit_mb)
            VALUES (10, 42, 'token', ?, ?, 1, 1, 0, 1024)
            """,
            (now - 100000, now - 3600),
        )
        # Чтобы не создавать ключи fallback-веткой
        c.execute(
            """
            INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, created_at, email, tariff_id, client_config, subscription_id)
            VALUES (1, 42, 'uuid', ?, 'e', 1, 'cfg', 10)
            """,
            (now - 1000,),
        )
        conn.commit()
    finally:
        conn.close()

    payment = Payment(
        payment_id="p1",
        user_id=42,
        tariff_id=1,
        amount=10000,
        email="test@example.com",
        status=PaymentStatus.PAID,
        protocol="v2ray",
        paid_at=datetime.now(timezone.utc),
        metadata={"key_type": "subscription"},
    )

    with patch.object(service.payment_repo, "get_by_payment_id", AsyncMock(return_value=payment)):
        # paid -> processing_subscription -> completed
        async def try_update(pid, new_status, expected_status):
            if pid != "p1":
                return False
            if payment.status != expected_status:
                return False
            payment.status = new_status
            return True

        with patch.object(service.payment_repo, "try_update_status", AsyncMock(side_effect=try_update)):
            with patch.object(service.payment_repo, "update_subscription_id", AsyncMock(return_value=True)):
                with patch(
                    "payments.services.subscription_purchase_service.safe_send_message",
                    new_callable=AsyncMock,
                    return_value=True,
                ):
                    with patch(
                        "payments.services.subscription_purchase_service.reset_subscription_traffic",
                        new_callable=AsyncMock,
                        return_value=True,
                    ) as mock_reset:
                        ok, err = await service.process_subscription_purchase("p1")
                        assert ok is True
                        assert err is None
                        # Лимитный тариф -> reset должен вызываться с reset_ts
                        assert mock_reset.called
                        _, kwargs = mock_reset.call_args
                        assert "reset_ts" in kwargs

    # Проверяем expires_at в БД
    conn = sqlite3.connect(db_path)
    try:
        expires_at = conn.execute("SELECT expires_at FROM subscriptions WHERE id = 10").fetchone()[0]
    finally:
        conn.close()
    assert expires_at >= now + 86400 - 5


@pytest.mark.asyncio
async def test_unlimited_tariff_does_not_reset_traffic_on_renewal(service):
    """
    Политика трафика: для безлимитных тарифов (traffic_limit_mb=0) reset при продлении не вызываем.
    """
    db_path = Path(service.db_path)
    now = int(time.time())

    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute(
            """
            INSERT OR REPLACE INTO tariffs (
                id, name, duration_sec, price_rub, traffic_limit_mb,
                price_crypto_usd, enable_yookassa, enable_platega, enable_cryptobot, is_archived
            ) VALUES (2, 'u', 86400, 100, 0, NULL, 1, 1, 1, 0)
            """
        )
        c.execute(
            """
            INSERT INTO subscriptions (id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, notified, traffic_limit_mb)
            VALUES (11, 42, 'token2', ?, ?, 2, 1, 0, 0)
            """,
            (now - 100000, now + 1000),
        )
        c.execute(
            """
            INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, created_at, email, tariff_id, client_config, subscription_id)
            VALUES (1, 42, 'uuid2', ?, 'e', 2, 'cfg', 11)
            """,
            (now - 1000,),
        )
        conn.commit()
    finally:
        conn.close()

    payment = Payment(
        payment_id="p2",
        user_id=42,
        tariff_id=2,
        amount=10000,
        email="test@example.com",
        status=PaymentStatus.PAID,
        protocol="v2ray",
        paid_at=datetime.now(timezone.utc),
        metadata={"key_type": "subscription"},
    )

    with patch.object(service.payment_repo, "get_by_payment_id", AsyncMock(return_value=payment)):
        async def try_update(pid, new_status, expected_status):
            if pid != "p2":
                return False
            if payment.status != expected_status:
                return False
            payment.status = new_status
            return True

        with patch.object(service.payment_repo, "try_update_status", AsyncMock(side_effect=try_update)):
            with patch.object(service.payment_repo, "update_subscription_id", AsyncMock(return_value=True)):
                with patch(
                    "payments.services.subscription_purchase_service.safe_send_message",
                    new_callable=AsyncMock,
                    return_value=True,
                ):
                    with patch(
                        "payments.services.subscription_purchase_service.reset_subscription_traffic",
                        new_callable=AsyncMock,
                        return_value=True,
                    ) as mock_reset:
                        ok, err = await service.process_subscription_purchase("p2")
                        assert ok is True
                        assert err is None
                        assert mock_reset.called is False

