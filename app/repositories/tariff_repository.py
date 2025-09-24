from __future__ import annotations

import sqlite3
from typing import List, Tuple
from app.settings import settings
from app.infra.sqlite_utils import open_connection


class TariffRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def list_tariffs(self) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id, name, duration_sec, price_rub FROM tariffs ORDER BY price_rub ASC")
            return c.fetchall()

    def get_tariff(self, tariff_id: int) -> Tuple | None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id, name, duration_sec, price_rub FROM tariffs WHERE id = ?", (tariff_id,))
            return c.fetchone()

    def add_tariff(self, name: str, duration_sec: int, price_rub: int) -> int:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO tariffs (name, duration_sec, traffic_limit_mb, price_rub) VALUES (?, ?, ?, ?)",
                (name, duration_sec, 0, price_rub),
            )
            conn.commit()
            return c.lastrowid

    def delete_tariff(self, tariff_id: int) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM tariffs WHERE id = ?", (tariff_id,))
            conn.commit()

    def update_tariff(self, tariff_id: int, name: str, duration_sec: int, price_rub: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE tariffs SET name = ?, duration_sec = ?, price_rub = ? WHERE id = ?",
                (name, duration_sec, price_rub, tariff_id),
            )
            conn.commit()



