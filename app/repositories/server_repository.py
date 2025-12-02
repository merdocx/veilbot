from __future__ import annotations

from typing import Dict, List, Tuple
from app.infra.sqlite_utils import open_connection
from app.infra.foreign_keys import safe_foreign_keys_off
from app.settings import settings


class ServerRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def list_servers(self, search_query: str | None = None) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            if search_query:
                search_pattern = f"%{search_query}%"
                c.execute(
                    """
                    SELECT id, name, api_url, cert_sha256, max_keys, active, country, protocol, domain, api_key, v2ray_path, available_for_purchase 
                    FROM servers
                    WHERE CAST(id AS TEXT) LIKE ? 
                       OR name LIKE ? 
                       OR country LIKE ? 
                       OR protocol LIKE ? 
                       OR domain LIKE ?
                       OR api_url LIKE ?
                    ORDER BY id
                    """,
                    (search_pattern, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern)
                )
            else:
                c.execute(
                    "SELECT id, name, api_url, cert_sha256, max_keys, active, country, protocol, domain, api_key, v2ray_path, available_for_purchase FROM servers"
                )
            return c.fetchall()

    def add_server(
        self, name: str, api_url: str, cert_sha256: str, max_keys: int, country: str, protocol: str, domain: str, api_key: str, v2ray_path: str, available_for_purchase: int = 1
    ) -> int:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO servers (name, api_url, cert_sha256, max_keys, country, protocol, domain, api_key, v2ray_path, available_for_purchase)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, api_url, cert_sha256, max_keys, country, protocol, domain, api_key, v2ray_path, available_for_purchase),
            )
            conn.commit()
            return c.lastrowid

    def get_server(self, server_id: int) -> Tuple | None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id, name, api_url, cert_sha256, max_keys, active, country, protocol, domain, api_key, v2ray_path, available_for_purchase FROM servers WHERE id = ?",
                (server_id,),
            )
            return c.fetchone()

    def update_server(
        self,
        server_id: int,
        name: str,
        api_url: str,
        cert_sha256: str,
        max_keys: int,
        active: int,
        country: str,
        protocol: str,
        domain: str,
        api_key: str,
        v2ray_path: str,
        available_for_purchase: int = 1,
    ) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                UPDATE servers
                SET name = ?, api_url = ?, cert_sha256 = ?, max_keys = ?, active = ?, country = ?,
                    protocol = ?, domain = ?, api_key = ?, v2ray_path = ?, available_for_purchase = ?
                WHERE id = ?
                """,
                (name, api_url, cert_sha256, max_keys, active, country, protocol, domain, api_key, v2ray_path, available_for_purchase, server_id),
            )
            conn.commit()

    def delete_server(self, server_id: int) -> Dict[str, int]:
        """
        Полностью удалить сервер вместе со всеми ключами.

        Возвращает статистику, чтобы маршруты могли логировать результат.
        """
        stats = {
            "outline_keys_deleted": 0,
            "v2ray_keys_deleted": 0,
            "subscriptions_affected": 0,
        }
        affected_subscriptions: set[int] = set()

        with open_connection(self.db_path) as conn:
            c = conn.cursor()

            # Сохраняем затронутые подписки до удаления ключей
            c.execute(
                "SELECT DISTINCT subscription_id FROM v2ray_keys WHERE server_id = ? AND subscription_id IS NOT NULL",
                (server_id,),
            )
            affected_subscriptions.update(sub_id for (sub_id,) in c.fetchall() if sub_id)

            c.execute(
                "SELECT DISTINCT subscription_id FROM keys WHERE server_id = ? AND subscription_id IS NOT NULL",
                (server_id,),
            )
            affected_subscriptions.update(sub_id for (sub_id,) in c.fetchall() if sub_id)

            with safe_foreign_keys_off(c):
                c.execute("DELETE FROM v2ray_keys WHERE server_id = ?", (server_id,))
                stats["v2ray_keys_deleted"] = c.rowcount

                c.execute("DELETE FROM keys WHERE server_id = ?", (server_id,))
                stats["outline_keys_deleted"] = c.rowcount

                c.execute("DELETE FROM servers WHERE id = ?", (server_id,))

            conn.commit()

        stats["subscriptions_affected"] = len(affected_subscriptions)
        return stats

    def outline_key_counts(self, server_ids: List[int]) -> dict:
        if not server_ids:
            return {}
        q_marks = ",".join(["?"] * len(server_ids))
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(f"SELECT server_id, COUNT(*) FROM keys GROUP BY server_id HAVING server_id IN ({q_marks})", server_ids)
            return dict(c.fetchall())

    def v2ray_key_counts(self, server_ids: List[int]) -> dict:
        if not server_ids:
            return {}
        q_marks = ",".join(["?"] * len(server_ids))
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(f"SELECT server_id, COUNT(*) FROM v2ray_keys GROUP BY server_id HAVING server_id IN ({q_marks})", server_ids)
            return dict(c.fetchall())



