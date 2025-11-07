from __future__ import annotations

from typing import List, Tuple
from app.infra.sqlite_utils import open_connection
from app.settings import settings


class TariffRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def list_tariffs(self) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id, name, duration_sec, price_rub, traffic_limit_mb, price_crypto_usd FROM tariffs ORDER BY price_rub ASC")
            return c.fetchall()

    def get_tariff(self, tariff_id: int) -> Tuple | None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id, name, duration_sec, price_rub, traffic_limit_mb, price_crypto_usd FROM tariffs WHERE id = ?", (tariff_id,))
            return c.fetchone()

    def add_tariff(self, name: str, duration_sec: int, price_rub: int, traffic_limit_mb: int = 0, price_crypto_usd: float | None = None) -> int:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO tariffs (name, duration_sec, traffic_limit_mb, price_rub, price_crypto_usd) VALUES (?, ?, ?, ?, ?)",
                (name, duration_sec, traffic_limit_mb, price_rub, price_crypto_usd),
            )
            conn.commit()
            return c.lastrowid

    def delete_tariff(self, tariff_id: int) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM tariffs WHERE id = ?", (tariff_id,))
            conn.commit()

    def update_tariff(self, tariff_id: int, name: str, duration_sec: int, price_rub: int, traffic_limit_mb: int = 0, price_crypto_usd: float | None = None) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE tariffs SET name = ?, duration_sec = ?, price_rub = ?, traffic_limit_mb = ?, price_crypto_usd = ? WHERE id = ?",
                (name, duration_sec, price_rub, traffic_limit_mb, price_crypto_usd, tariff_id),
            )
            conn.commit()



