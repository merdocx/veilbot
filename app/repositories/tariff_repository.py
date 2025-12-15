from __future__ import annotations

from typing import List, Tuple
from app.infra.sqlite_utils import open_connection
from app.settings import settings


class TariffRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def list_tariffs(self, search_query: str | None = None) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            if search_query:
                search_pattern = f"%{search_query}%"
                c.execute(
                    """
                    SELECT 
                        id,
                        name,
                        duration_sec,
                        price_rub,
                        traffic_limit_mb,
                        price_crypto_usd,
                        enable_yookassa,
                        enable_platega,
                        enable_cryptobot
                    FROM tariffs
                    WHERE CAST(id AS TEXT) LIKE ? 
                       OR name LIKE ? 
                       OR CAST(price_rub AS TEXT) LIKE ?
                       OR CAST(traffic_limit_mb AS TEXT) LIKE ?
                    ORDER BY price_rub ASC
                    """,
                    (search_pattern, search_pattern, search_pattern, search_pattern),
                )
            else:
                c.execute(
                    """
                    SELECT 
                        id,
                        name,
                        duration_sec,
                        price_rub,
                        traffic_limit_mb,
                        price_crypto_usd,
                        enable_yookassa,
                        enable_platega,
                        enable_cryptobot
                    FROM tariffs
                    ORDER BY price_rub ASC
                    """
                )
            return c.fetchall()

    def get_tariff(self, tariff_id: int) -> Tuple | None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT 
                    id,
                    name,
                    duration_sec,
                    price_rub,
                    traffic_limit_mb,
                    price_crypto_usd,
                    enable_yookassa,
                    enable_platega,
                    enable_cryptobot
                FROM tariffs
                WHERE id = ?
                """,
                (tariff_id,),
            )
            return c.fetchone()

    def add_tariff(
        self,
        name: str,
        duration_sec: int,
        price_rub: int,
        traffic_limit_mb: int = 0,
        price_crypto_usd: float | None = None,
        enable_yookassa: int = 1,
        enable_platega: int = 1,
        enable_cryptobot: int = 1,
    ) -> int:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO tariffs (
                    name,
                    duration_sec,
                    traffic_limit_mb,
                    price_rub,
                    price_crypto_usd,
                    enable_yookassa,
                    enable_platega,
                    enable_cryptobot
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    duration_sec,
                    traffic_limit_mb,
                    price_rub,
                    price_crypto_usd,
                    enable_yookassa,
                    enable_platega,
                    enable_cryptobot,
                ),
            )
            conn.commit()
            return c.lastrowid

    def delete_tariff(self, tariff_id: int) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM tariffs WHERE id = ?", (tariff_id,))
            conn.commit()

    def update_tariff(
        self,
        tariff_id: int,
        name: str,
        duration_sec: int,
        price_rub: int,
        traffic_limit_mb: int = 0,
        price_crypto_usd: float | None = None,
        enable_yookassa: int | None = None,
        enable_platega: int | None = None,
        enable_cryptobot: int | None = None,
    ) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            fields = [
                ("name", name),
                ("duration_sec", duration_sec),
                ("price_rub", price_rub),
                ("traffic_limit_mb", traffic_limit_mb),
                ("price_crypto_usd", price_crypto_usd),
            ]
            if enable_yookassa is not None:
                fields.append(("enable_yookassa", int(enable_yookassa)))
            if enable_platega is not None:
                fields.append(("enable_platega", int(enable_platega)))
            if enable_cryptobot is not None:
                fields.append(("enable_cryptobot", int(enable_cryptobot)))

            set_clause = ", ".join(f"{name} = ?" for name, _ in fields)
            values = [value for _, value in fields]
            values.append(tariff_id)

            c.execute(
                f"UPDATE tariffs SET {set_clause} WHERE id = ?",
                tuple(values),
            )
            conn.commit()



