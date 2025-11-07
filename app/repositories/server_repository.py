from __future__ import annotations

from typing import List, Tuple
from app.infra.sqlite_utils import open_connection
from app.settings import settings


class ServerRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def list_servers(self) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
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

    def delete_server(self, server_id: int) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM servers WHERE id = ?", (server_id,))
            conn.commit()

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



