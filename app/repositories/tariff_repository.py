from __future__ import annotations

from typing import List, Tuple
from app.infra.sqlite_utils import open_connection
from app.settings import settings


class TariffRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def list_tariffs(self, search_query: str | None = None, include_archived: bool = True) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            where_clauses = []
            params = []
            
            if search_query:
                search_pattern = f"%{search_query}%"
                where_clauses.append("(CAST(id AS TEXT) LIKE ? OR name LIKE ? OR CAST(price_rub AS TEXT) LIKE ? OR CAST(traffic_limit_mb AS TEXT) LIKE ?)")
                params.extend([search_pattern, search_pattern, search_pattern, search_pattern])
            
            if not include_archived:
                where_clauses.append("(is_archived IS NULL OR is_archived = 0)")
            
            where_clause = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            
            c.execute(
                f"""
                SELECT 
                    id,
                    name,
                    duration_sec,
                    price_rub,
                    traffic_limit_mb,
                    price_crypto_usd,
                    enable_yookassa,
                    enable_platega,
                    enable_cryptobot,
                    is_archived
                FROM tariffs
                {where_clause}
                ORDER BY price_rub ASC
                """,
                tuple(params),
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
                    enable_cryptobot,
                    is_archived
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
        is_archived: int = 0,
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
                    enable_cryptobot,
                    is_archived
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    is_archived,
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
        is_archived: int | None = None,
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
            # ВАЖНО: Всегда обновляем is_archived, даже если значение 0
            # Это нужно для правильной работы деактивации тарифов
            # Если значение не передано, используем 0 (не архивирован)
            is_archived_value = int(is_archived) if is_archived is not None else 0
            fields.append(("is_archived", is_archived_value))

            set_clause = ", ".join(f"{name} = ?" for name, _ in fields)
            values = [value for _, value in fields]
            values.append(tariff_id)

            c.execute(
                f"UPDATE tariffs SET {set_clause} WHERE id = ?",
                tuple(values),
            )
            conn.commit()



