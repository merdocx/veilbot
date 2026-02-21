import sqlite3

import db


def test_init_db_with_migrations_creates_core_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "veilbot_test.db"
    monkeypatch.setattr(db, "DATABASE_PATH", str(db_path), raising=False)

    db.init_db_with_migrations()

    conn = sqlite3.connect(db.DATABASE_PATH)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert {"servers", "tariffs", "subscriptions", "dashboard_metrics"}.issubset(tables)

