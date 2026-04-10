from __future__ import annotations

from typing import List, Tuple
from app.settings import settings
from app.infra.sqlite_utils import open_connection
from app.infra.foreign_keys import safe_foreign_keys_off


class KeyRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def list_v2ray_keys_with_server(self) -> List[Tuple]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT k.id, k.v2ray_uuid as key_id,
                       s.domain, COALESCE(s.v2ray_path, '/v2ray'),
                       k.created_at, COALESCE(sub.expires_at, 0) as expiry_at, 
                       s.name, k.email, t.name as tariff_name,
                       s.api_url, s.api_key
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                LEFT JOIN tariffs t ON k.tariff_id = t.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                """
            )
            return c.fetchall()

    def get_v2ray_key_brief(self, key_pk: int) -> Tuple | None:
        """Return (user_id, v2ray_uuid, server_id) for v2ray key id or None."""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, v2ray_uuid, server_id FROM v2ray_keys WHERE id = ?", (key_pk,))
            return c.fetchone()

    def get_key_unified_by_id(self, key_pk: int) -> Tuple | None:
        """Единая строка ключа V2Ray для админки."""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT k.id || '_v2ray' as id, k.v2ray_uuid as key_id,
                       COALESCE(k.client_config, '') as access_url,
                       k.created_at, COALESCE(sub.expires_at, 0) as expiry_at,
                       IFNULL(s.name,''), k.email, k.user_id, IFNULL(t.name,''), 'v2ray' as protocol,
                       0, IFNULL(s.api_url,''), IFNULL(s.api_key,''),
                       COALESCE(k.traffic_usage_bytes, 0), NULL AS traffic_over_limit_at,
                       0 AS traffic_over_limit_notified, k.subscription_id
                FROM v2ray_keys k
                LEFT JOIN servers s ON k.server_id = s.id
                LEFT JOIN tariffs t ON k.tariff_id = t.id
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.id = ?
                """,
                (key_pk,),
            )
            return c.fetchone()

    def delete_v2ray_key_by_id(self, key_pk: int) -> None:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            # Используем контекстный менеджер для безопасного отключения foreign keys
            with safe_foreign_keys_off(c):
                c.execute("DELETE FROM v2ray_keys WHERE id = ?", (key_pk,))
            conn.commit()

    def get_expired_v2ray_keys(self, now_ts: int) -> List[Tuple]:
        """Получить истекшие V2Ray ключи (срок берется из subscriptions)"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT k.id, k.v2ray_uuid, k.server_id 
                FROM v2ray_keys k
                JOIN subscriptions s ON k.subscription_id = s.id
                WHERE s.expires_at <= ?
            """, (now_ts,))
            return c.fetchall()

    def v2ray_key_exists(self, key_pk: int) -> bool:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM v2ray_keys WHERE id = ?", (key_pk,))
            return c.fetchone() is not None

    def update_v2ray_key_expiry(self, key_pk: int, new_expiry_ts: int, traffic_limit_mb: int | None = None) -> None:
        """
        Обновить срок действия V2Ray ключа.
        
        ПРИМЕЧАНИЕ: После миграции expiry_at удален из таблиц ключей.
        Для обновления срока нужно обновить подписку через SubscriptionRepository.extend_subscription()
        ВАЖНО: traffic_limit_mb не используется на уровне ключей, вся информация берется из подписки
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            # Получаем subscription_id ключа
            c.execute("SELECT subscription_id FROM v2ray_keys WHERE id = ?", (key_pk,))
            result = c.fetchone()
            if not result or not result[0]:
                raise ValueError(f"V2Ray key {key_pk} does not have subscription_id")
            
            subscription_id = result[0]
            # Обновляем подписку
            from app.repositories.subscription_repository import SubscriptionRepository
            sub_repo = SubscriptionRepository(self.db_path)
            sub_repo.extend_subscription(subscription_id, new_expiry_ts)
            
            # ВАЖНО: traffic_limit_mb не обновляется на уровне ключа
            # Вся информация о трафике берется из подписки

    def insert_v2ray_key(
        self,
        server_id: int,
        user_id: int,
        v2ray_uuid: str,
        email: str | None,
        created_at: int,
        expiry_at: int,  # Оставлен для обратной совместимости, но не используется (берется из subscription)
        tariff_id: int | None,
        *,
        client_config: str | None = None,
        traffic_limit_mb: int = 0,
        traffic_usage_bytes: int = 0,
        traffic_over_limit_at: int | None = None,
        traffic_over_limit_notified: int = 0,
        subscription_id: int | None = None,
    ) -> int:
        """
        Вставка V2Ray ключа.
        expiry_at параметр оставлен для обратной совместимости, но не сохраняется в БД.
        Срок действия ключа определяется подпиской (subscription_id).
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            # ИСПРАВЛЕНИЕ: Колонки traffic_over_limit_at и traffic_over_limit_notified удалены из таблицы v2ray_keys
            # expiry_at также удален - срок берется из subscriptions
            c.execute(
                """
                INSERT INTO v2ray_keys (
                    server_id, user_id, v2ray_uuid, email, created_at, tariff_id, client_config,
                    traffic_limit_mb, traffic_usage_bytes, subscription_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    user_id,
                    v2ray_uuid,
                    email,
                    created_at,
                    tariff_id,
                    client_config,
                    traffic_limit_mb,
                    traffic_usage_bytes,
                    subscription_id,
                ),
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
        search_query: str | None = None,
    ) -> int:
        total = 0
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            def apply_common_conditions(base_sql: str, params: list) -> tuple[str, list]:
                needs_join = search_query is not None
                if needs_join:
                    base_sql += " LEFT JOIN servers s ON k.server_id=s.id LEFT JOIN tariffs t ON k.tariff_id=t.id"

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
                if search_query:
                    search_pattern = f"%{search_query}%"
                    search_conditions = [
                        "CAST(k.id AS TEXT) LIKE ?",
                        "k.email LIKE ?",
                        "IFNULL(s.name,'') LIKE ?",
                        "IFNULL(t.name,'') LIKE ?",
                        "CAST(k.user_id AS TEXT) LIKE ?",
                        "k.v2ray_uuid LIKE ?",
                        "(k.id || '_v2ray') LIKE ?",
                        "CAST(k.subscription_id AS TEXT) LIKE ?",
                    ]
                    where.append("(" + " OR ".join(search_conditions) + ")")
                    params.extend([search_pattern] * len(search_conditions))
                where_sql = (" WHERE " + " AND ".join(where)) if where else ""
                return base_sql + where_sql, params

            if protocol not in (None, "", "v2ray"):
                return 0
            if protocol in (None, "", "v2ray"):
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
        cursor: str | None = None,
        search_query: str | None = None,
    ) -> list[tuple]:
        # Columns: id, key_id, access_url, created_at, expiry_at, server_name, email, user_id, tariff_name, protocol, traffic_limit_mb, api_url, api_key, traffic_usage_bytes, traffic_over_limit_at, traffic_over_limit_notified, subscription_id
        sort_map = {
            'created_at': 3,
            'expiry_at': 4,
            'server_name': 5,
            'email': 6,
            'tariff_name': 7,
        }
        sort_map.get(sort_by.lower(), 3)
        order_dir = 'ASC' if str(sort_order).upper() == 'ASC' else 'DESC'

        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            parts = []
            params: list = []
            
            # Keyset pagination support
            keyset_where = ""
            keyset_params = []
            if cursor and sort_by == "created_at":
                from app.infra.pagination import KeysetPagination
                # ИСПРАВЛЕНИЕ: Не используем алиас "k" для keyset, т.к. это может вызвать проблемы с колонками
                # Используем пустой алиас, т.к. WHERE будет применяться к подзапросу после UNION
                keyset_where, keyset_params = KeysetPagination.build_keyset_where_clause(
                    sort_by, sort_order, cursor, ""
                )

            def add_v2ray():
                sql = (
                    "SELECT k.id || '_v2ray' as id, k.v2ray_uuid as key_id, "
                    "COALESCE(k.client_config, '') as access_url, "
                    "k.created_at, COALESCE(sub.expires_at, 0) as expiry_at, "
                    "IFNULL(s.name,''), k.email, k.user_id, IFNULL(t.name,''), 'v2ray' as protocol, "
                    "0 AS traffic_limit_mb, IFNULL(s.api_url,''), IFNULL(s.api_key,''), "
                    "COALESCE(k.traffic_usage_bytes, 0) AS traffic_usage_bytes, NULL AS traffic_over_limit_at, "
                    "0 AS traffic_over_limit_notified, k.subscription_id "
                    "FROM v2ray_keys k "
                    "LEFT JOIN servers s ON k.server_id=s.id "
                    "LEFT JOIN tariffs t ON k.tariff_id=t.id "
                    "LEFT JOIN subscriptions sub ON k.subscription_id = sub.id"
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
                if search_query:
                    search_pattern = f"%{search_query}%"
                    # Поиск по всем столбцам: id, email, v2ray_uuid, server_name, tariff_name, user_id, subscription_id
                    search_conditions = [
                        "CAST(k.id AS TEXT) LIKE ?",  # Поиск по числовому ID
                        "k.email LIKE ?",
                        "k.v2ray_uuid LIKE ?",
                        "IFNULL(s.name,'') LIKE ?",
                        "IFNULL(t.name,'') LIKE ?",
                        "CAST(k.user_id AS TEXT) LIKE ?",  # Поиск по user_id
                        # Поиск по полному ID с протоколом (например, "206_v2ray")
                        "(k.id || '_v2ray') LIKE ?",
                        # Поиск по subscription_id для V2Ray ключей
                        "CAST(k.subscription_id AS TEXT) LIKE ?",
                    ]
                    where.append("(" + " OR ".join(search_conditions) + ")")
                    params.extend([search_pattern] * len(search_conditions))
                # ИСПРАВЛЕНИЕ: Не применяем keyset_where здесь, т.к. он использует алиас "k" который может вызвать проблемы
                # keyset_where будет применен к обернутому запросу после UNION
                if where:
                    sql += " WHERE " + " AND ".join(where)
                parts.append(sql)

            if protocol not in (None, "", "v2ray"):
                return []
            if protocol in (None, "", "v2ray"):
                add_v2ray()

            union_sql = " UNION ALL ".join(parts) if parts else "SELECT 0, '', '', 0, 0, '', '', '', '' WHERE 0"
            
            # ИСПРАВЛЕНИЕ: Обертка в подзапрос для правильной сортировки после UNION ALL
            # Создаем алиасы для столбцов, чтобы ORDER BY работал корректно
            wrapped_union = f"SELECT * FROM ( {union_sql} ) AS combined_keys"
            
            # ИСПРАВЛЕНИЕ: Применяем keyset WHERE к обернутому запросу, а не к отдельным частям UNION
            # После UNION ALL столбцы имеют имена из первого SELECT, используем эти имена
            if keyset_where and cursor:
                # Заменяем имена столбцов на имена из результата UNION (без алиаса "k.")
                keyset_where_fixed = keyset_where.replace("k.created_at", "created_at").replace("k.id", "id").replace(".created_at", ".created_at").replace(".id", ".id")
                wrapped_union = f"SELECT * FROM ( {union_sql} ) AS combined_keys WHERE {keyset_where_fixed}"
            
            # Use keyset pagination for better performance
            if cursor and sort_by == "created_at":
                from app.infra.pagination import KeysetPagination
                # Используем имена столбцов из результата UNION (без алиаса)
                order_clause = KeysetPagination.build_keyset_order_clause(sort_by, sort_order, "")
                final_sql = f"SELECT * FROM ( {wrapped_union} ) ORDER BY {order_clause} LIMIT ?"
                params.extend([limit + 1])  # Fetch one extra to check if there are more
            else:
                # Fallback to offset pagination
                # ИСПРАВЛЕНИЕ: Сортировка только по created_at (столбец 3 в результате), тип ключа не используется
                # Используем номер столбца для гарантированной работы с UNION ALL
                final_sql = f"SELECT * FROM ( {wrapped_union} ) ORDER BY created_at {order_dir}, id {order_dir} LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            
            c.execute(final_sql, params)
            return c.fetchall()


