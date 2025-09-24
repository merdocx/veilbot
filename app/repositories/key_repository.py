from __future__ import annotations

import sqlite3
from typing import List, Tuple
from app.settings import settings
from app.infra.sqlite_utils import open_connection


class KeyRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def list_outline_keys(self) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT k.id, k.key_id, k.access_url, k.created_at, k.expiry_at, s.name, k.email, t.name as tariff_name
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN tariffs t ON k.tariff_id = t.id
                """
            )
            return c.fetchall()

    def list_v2ray_keys_with_server(self) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT k.id, k.v2ray_uuid as key_id,
                       s.domain, COALESCE(s.v2ray_path, '/v2ray'),
                       k.created_at, k.expiry_at, s.name, k.email, t.name as tariff_name,
                       s.api_url, s.api_key
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN tariffs t ON k.tariff_id = t.id
                """
            )
            return c.fetchall()

    def get_outline_key_brief(self, key_pk: int) -> Tuple | None:
        """Return (user_id, key_id, server_id) for outline key id or None."""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, key_id, server_id FROM keys WHERE id = ?", (key_pk,))
            return c.fetchone()

    def get_v2ray_key_brief(self, key_pk: int) -> Tuple | None:
        """Return (user_id, v2ray_uuid, server_id) for v2ray key id or None."""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, v2ray_uuid, server_id FROM v2ray_keys WHERE id = ?", (key_pk,))
            return c.fetchone()

    def delete_outline_key_by_id(self, key_pk: int) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM keys WHERE id = ?", (key_pk,))
            conn.commit()

    def delete_v2ray_key_by_id(self, key_pk: int) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_pk,))
            conn.commit()

    def get_expired_outline_keys(self, now_ts: int) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id, key_id, server_id FROM keys WHERE expiry_at <= ?", (now_ts,))
            return c.fetchall()

    def get_expired_v2ray_keys(self, now_ts: int) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT id, v2ray_uuid, server_id FROM v2ray_keys WHERE expiry_at <= ?", (now_ts,))
            return c.fetchall()

    def outline_key_exists(self, key_pk: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM keys WHERE id = ?", (key_pk,))
            return c.fetchone() is not None

    def v2ray_key_exists(self, key_pk: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM v2ray_keys WHERE id = ?", (key_pk,))
            return c.fetchone() is not None

    def update_outline_key_expiry(self, key_pk: int, new_expiry_ts: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("UPDATE keys SET expiry_at = ? WHERE id = ?", (new_expiry_ts, key_pk))
            conn.commit()

    def update_v2ray_key_expiry(self, key_pk: int, new_expiry_ts: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("UPDATE v2ray_keys SET expiry_at = ? WHERE id = ?", (new_expiry_ts, key_pk))
            conn.commit()

    # Insert methods
    def insert_outline_key(
        self,
        server_id: int,
        user_id: int,
        access_url: str,
        expiry_at: int,
        key_id: str,
        email: str | None,
        tariff_id: int | None,
    ) -> int:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO keys (server_id, user_id, access_url, expiry_at, traffic_limit_mb, notified, key_id, email, tariff_id)
                VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)
                """,
                (server_id, user_id, access_url, expiry_at, key_id, email, tariff_id),
            )
            conn.commit()
            return c.lastrowid

    def insert_v2ray_key(
        self,
        server_id: int,
        user_id: int,
        v2ray_uuid: str,
        email: str | None,
        created_at: int,
        expiry_at: int,
        tariff_id: int | None,
    ) -> int:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO v2ray_keys (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (server_id, user_id, v2ray_uuid, email, created_at, expiry_at, tariff_id),
            )
            conn.commit()
            return c.lastrowid

    # Unified listing with filters/pagination
    def count_keys_unified(
        self,
        user_id: int | None = None,
        email: str | None = None,
        tariff_id: int | None = None,
        protocol: str | None = None,
        server_id: int | None = None,
    ) -> int:
        total = 0
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            def apply_common_conditions(base_sql: str, params: list) -> tuple[str, list]:
                where = []
                if user_id is not None:
                    where.append("k.user_id = ?")
                    params.append(user_id)
                if email:
                    where.append("k.email LIKE ?")
                    params.append(f"%{email}%")
                if tariff_id is not None:
                    where.append("k.tariff_id = ?")
                    params.append(tariff_id)
                if server_id is not None:
                    where.append("k.server_id = ?")
                    params.append(server_id)
                where_sql = (" WHERE " + " AND ".join(where)) if where else ""
                return base_sql + where_sql, params

            if protocol in (None, '', 'outline'):
                sql, params = apply_common_conditions("SELECT COUNT(*) FROM keys k", [])
                c.execute(sql, params)
                row = c.fetchone()
                total += int(row[0] or 0)
            if protocol in (None, '', 'v2ray'):
                sql, params = apply_common_conditions("SELECT COUNT(*) FROM v2ray_keys k", [])
                c.execute(sql, params)
                row = c.fetchone()
                total += int(row[0] or 0)
        return total

    def list_keys_unified(
        self,
        user_id: int | None = None,
        email: str | None = None,
        tariff_id: int | None = None,
        protocol: str | None = None,
        server_id: int | None = None,
        sort_by: str = "created_at",
        sort_order: str = "DESC",
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple]:
        # Columns: id, key_id, access_url, created_at, expiry_at, server_name, email, tariff_name, protocol, traffic_limit_mb, api_url, api_key
        sort_map = {
            'created_at': 3,
            'expiry_at': 4,
            'server_name': 5,
            'email': 6,
            'tariff_name': 7,
        }
        order_idx = sort_map.get(sort_by.lower(), 3)
        order_dir = 'ASC' if str(sort_order).upper() == 'ASC' else 'DESC'

        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            parts = []
            params: list = []

            def add_outline():
                sql = (
                    "SELECT k.id, k.key_id, k.access_url, k.created_at, k.expiry_at, IFNULL(s.name,''), k.email, IFNULL(t.name,''), 'outline' as protocol, IFNULL(k.traffic_limit_mb,''), '' as api_url, '' as api_key "
                    "FROM keys k LEFT JOIN servers s ON k.server_id=s.id LEFT JOIN tariffs t ON k.tariff_id=t.id"
                )
                where = []
                if user_id is not None:
                    where.append("k.user_id = ?"); params.append(user_id)
                if email:
                    where.append("k.email LIKE ?"); params.append(f"%{email}%")
                if tariff_id is not None:
                    where.append("k.tariff_id = ?"); params.append(tariff_id)
                if server_id is not None:
                    where.append("k.server_id = ?"); params.append(server_id)
                if where:
                    sql += " WHERE " + " AND ".join(where)
                parts.append(sql)

            def add_v2ray():
                sql = (
                    "SELECT k.id, k.v2ray_uuid as key_id, "
                    "('vless://' || k.v2ray_uuid || '@' || IFNULL(s.domain,'') || ':443?path=' || IFNULL(s.v2ray_path,'/v2ray') || '&security=tls&type=ws#VeilBot-V2Ray') as access_url, "
                    "k.created_at, k.expiry_at, IFNULL(s.name,''), k.email, IFNULL(t.name,''), 'v2ray' as protocol, '' as traffic_limit_mb, IFNULL(s.api_url,''), IFNULL(s.api_key,'') "
                    "FROM v2ray_keys k LEFT JOIN servers s ON k.server_id=s.id LEFT JOIN tariffs t ON k.tariff_id=t.id"
                )
                where = []
                if user_id is not None:
                    where.append("k.user_id = ?"); params.append(user_id)
                if email:
                    where.append("k.email LIKE ?"); params.append(f"%{email}%")
                if tariff_id is not None:
                    where.append("k.tariff_id = ?"); params.append(tariff_id)
                if server_id is not None:
                    where.append("k.server_id = ?"); params.append(server_id)
                if where:
                    sql += " WHERE " + " AND ".join(where)
                parts.append(sql)

            if protocol in (None, '', 'outline'):
                add_outline()
            if protocol in (None, '', 'v2ray'):
                add_v2ray()

            union_sql = " UNION ALL ".join(parts) if parts else "SELECT 0, '', '', 0, 0, '', '', '', '' WHERE 0"
            # Используем имена колонок для сортировки вместо индексов
            sort_column = sort_by if sort_by in ['created_at', 'expiry_at', 'server_name', 'email', 'tariff_name'] else 'created_at'
            final_sql = f"SELECT * FROM ( {union_sql} ) ORDER BY {sort_column} {order_dir} LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            c.execute(final_sql, params)
            return c.fetchall()


