import sqlite3
import time

import pytest

from app.repositories.subscription_repository import SubscriptionRepository


def _init_db(tmp_path):
    db_path = tmp_path / "subscription_repo.db"
    conn = sqlite3.connect(db_path)

    conn.execute(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at INTEGER,
            last_active_at INTEGER,
            blocked INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subscription_token TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            tariff_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            notified INTEGER DEFAULT 0,
            traffic_limit_mb INTEGER DEFAULT 0,
            traffic_usage_bytes INTEGER DEFAULT 0,
            traffic_over_limit_at INTEGER,
            traffic_over_limit_notified INTEGER DEFAULT 0,
            last_updated_at INTEGER,
            purchase_notification_sent INTEGER DEFAULT 0,
            last_traffic_reset_at INTEGER
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE tariffs (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            traffic_limit_mb INTEGER DEFAULT 0,
            duration_sec INTEGER DEFAULT 0,
            price_rub INTEGER DEFAULT 0,
            price_crypto_usd REAL DEFAULT NULL,
            enable_yookassa INTEGER DEFAULT 1,
            enable_platega INTEGER DEFAULT 1,
            enable_cryptobot INTEGER DEFAULT 1
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE servers (
            id INTEGER PRIMARY KEY,
            api_url TEXT,
            api_key TEXT,
            cert_sha256 TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE v2ray_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER,
            server_id INTEGER,
            v2ray_uuid TEXT,
            FOREIGN KEY(subscription_id) REFERENCES subscriptions(id),
            FOREIGN KEY(server_id) REFERENCES servers(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER,
            server_id INTEGER,
            key_id TEXT,
            protocol TEXT,
            FOREIGN KEY(subscription_id) REFERENCES subscriptions(id),
            FOREIGN KEY(server_id) REFERENCES servers(id)
        )
        """
    )

    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def repo(tmp_path):
    db_path = _init_db(tmp_path)
    return SubscriptionRepository(db_path=db_path)


def _insert_subscription(conn, **overrides):
    now = int(time.time())
    values = {
        "user_id": overrides.get("user_id", 1),
        "subscription_token": overrides.get("subscription_token", "token-1"),
        "created_at": overrides.get("created_at", now - 10),
        "expires_at": overrides.get("expires_at", now + 3600),
        "tariff_id": overrides.get("tariff_id"),
        "is_active": overrides.get("is_active", 1),
        "notified": overrides.get("notified", 0),
        "traffic_limit_mb": overrides.get("traffic_limit_mb", 0),
        "traffic_usage_bytes": overrides.get("traffic_usage_bytes", 0),
        "traffic_over_limit_at": overrides.get("traffic_over_limit_at"),
        "traffic_over_limit_notified": overrides.get(
            "traffic_over_limit_notified", 0
        ),
        "last_updated_at": overrides.get("last_updated_at"),
        "purchase_notification_sent": overrides.get("purchase_notification_sent", 0),
    }
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"""
        INSERT INTO subscriptions (
            user_id, subscription_token, created_at, expires_at, tariff_id,
            is_active, notified, traffic_limit_mb, traffic_usage_bytes,
            traffic_over_limit_at, traffic_over_limit_notified,
            last_updated_at, purchase_notification_sent
        )
        VALUES ({placeholders})
        """,
        tuple(values.values()),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_get_subscriptions_with_traffic_limits_prefers_subscription_limit(repo):
    conn = sqlite3.connect(repo.db_path)
    # Создаем пользователя для корректной работы JOIN
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, is_vip) VALUES (1, 0)"
    )
    conn.execute(
        "INSERT INTO tariffs (id, name, traffic_limit_mb) VALUES (1, 'base', 1024)"
    )
    sub_id = _insert_subscription(
        conn,
        tariff_id=1,
        traffic_limit_mb=2048,
        expires_at=int(time.time()) + 10_000,
    )
    conn.commit()
    conn.close()

    rows = repo.get_subscriptions_with_traffic_limits(int(time.time()))
    assert rows, "Ожидали получить хотя бы одну подписку"
    row = rows[0]
    assert row[0] == sub_id
    assert row[7] == 2048  # индивидуальный лимит должен переопределить тариф


def test_get_subscriptions_with_traffic_limits_fallbacks_to_tariff(repo):
    conn = sqlite3.connect(repo.db_path)
    # Создаем пользователя для корректной работы JOIN
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, is_vip) VALUES (1, 0)"
    )
    conn.execute(
        "INSERT INTO tariffs (id, name, traffic_limit_mb) VALUES (2, 'premium', 512)"
    )
    # Создаем подписку с NULL в traffic_limit_mb, чтобы проверить fallback на тариф
    # Теперь 0 означает безлимит, а NULL означает использовать тариф
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO subscriptions (
            user_id, subscription_token, created_at, expires_at, tariff_id,
            is_active, notified, traffic_limit_mb, traffic_usage_bytes,
            traffic_over_limit_at, traffic_over_limit_notified,
            last_updated_at, purchase_notification_sent
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1, "token-2", now - 10, now + 10_000, 2,
            1, 0, None, 0,  # traffic_limit_mb = None (NULL) для fallback на тариф
            None, 0, None, 0
        )
    )
    conn.commit()
    conn.close()

    rows = repo.get_subscriptions_with_traffic_limits(int(time.time()))
    assert rows, "Ожидали получить подписку с лимитом из тарифа"
    assert rows[0][7] == 512


def test_update_subscription_traffic_updates_usage(repo):
    conn = sqlite3.connect(repo.db_path)
    sub_id = _insert_subscription(conn, traffic_usage_bytes=123)
    conn.commit()
    conn.close()

    repo.update_subscription_traffic(sub_id, usage_bytes=2048)

    conn = sqlite3.connect(repo.db_path)
    usage = conn.execute(
        "SELECT traffic_usage_bytes FROM subscriptions WHERE id = ?", (sub_id,)
    ).fetchone()[0]
    conn.close()
    assert usage == 2048


def test_get_subscription_keys_for_deletion_returns_all_protocols(repo):
    conn = sqlite3.connect(repo.db_path)
    conn.execute(
        "INSERT INTO servers (id, api_url, api_key, cert_sha256) VALUES (1, 'https://srv', 'api-key', 'cert')"
    )
    conn.execute(
        "INSERT INTO servers (id, api_url, api_key, cert_sha256) VALUES (2, 'https://outline', 'outline-key', 'cert2')"
    )
    sub_id = _insert_subscription(conn)
    conn.execute(
        """
        INSERT INTO v2ray_keys (subscription_id, server_id, v2ray_uuid)
        VALUES (?, ?, ?)
        """,
        (sub_id, 1, "uuid-1"),
    )
    conn.execute(
        """
        INSERT INTO keys (subscription_id, server_id, key_id, protocol)
        VALUES (?, ?, ?, 'outline')
        """,
        (sub_id, 2, "outline-key-id"),
    )
    conn.commit()
    conn.close()

    keys = repo.get_subscription_keys_for_deletion(sub_id)
    protocols = sorted(proto for *_, proto in keys)
    assert protocols == ["outline", "v2ray"]

