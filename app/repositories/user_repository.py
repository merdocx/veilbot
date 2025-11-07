from __future__ import annotations

from typing import List, Tuple, Optional

from app.infra.sqlite_utils import open_connection
from app.settings import settings


class UserRepository:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def count_users(self, query: Optional[str] = None) -> int:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            base_query = (
                "SELECT user_id FROM ("
                "SELECT DISTINCT user_id FROM keys "
                "UNION "
                "SELECT DISTINCT user_id FROM v2ray_keys"
                ")"
            )
            if query:
                like = f"%{query.strip()}%"
                sql = f"SELECT COUNT(*) FROM ({base_query}) AS combined WHERE CAST(user_id AS TEXT) LIKE ?"
                c.execute(sql, (like,))
            else:
                sql = f"SELECT COUNT(*) FROM ({base_query}) AS combined"
                c.execute(sql)
            row = c.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def list_users(self, query: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Tuple[int, int]]:
        """
        Возвращает список (user_id, referral_count) с пагинацией и поиском по user_id.
        Источник пользователей — таблица keys (как в текущей логике админки).
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()

            base_subquery = (
                "SELECT DISTINCT user_id FROM keys "
                "UNION "
                "SELECT DISTINCT user_id FROM v2ray_keys"
            )

            if query:
                like = f"%{query.strip()}%"
                sql = (
                    "SELECT u.user_id, "
                    "       (SELECT COUNT(*) FROM referrals r WHERE r.referrer_id = u.user_id) AS referral_count "
                    "FROM ("
                    f"    SELECT user_id FROM ({base_subquery}) AS combined "
                    "    WHERE CAST(user_id AS TEXT) LIKE ? "
                    "    ORDER BY user_id LIMIT ? OFFSET ?"
                    ") u"
                )
                c.execute(sql, (like, limit, offset))
            else:
                sql = (
                    "SELECT u.user_id, "
                    "       (SELECT COUNT(*) FROM referrals r WHERE r.referrer_id = u.user_id) AS referral_count "
                    "FROM ("
                    f"    SELECT user_id FROM ({base_subquery}) AS combined "
                    "    ORDER BY user_id LIMIT ? OFFSET ?"
                    ") u"
                )
                c.execute(sql, (limit, offset))

            return c.fetchall()

    def get_user_overview(self, user_id: int) -> dict:
        """Return basic info about user: counts, last activity, email if any."""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*), MAX(created_at) FROM keys WHERE user_id = ?", (user_id,))
            outline_row = c.fetchone() or (0, None)
            c.execute("SELECT COUNT(*), MAX(created_at) FROM v2ray_keys WHERE user_id = ?", (user_id,))
            v2ray_row = c.fetchone() or (0, None)
            
            # Оптимизированный запрос: объединяем 3 запроса в один с UNION ALL
            # Приоритет: payments > keys > v2ray_keys
            c.execute("""
                SELECT email FROM (
                    SELECT email, 1 as priority, created_at as sort_date
                    FROM payments 
                    WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
                    UNION ALL
                    SELECT email, 2 as priority, expiry_at as sort_date
                    FROM keys 
                    WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
                    UNION ALL
                    SELECT email, 3 as priority, expiry_at as sort_date
                    FROM v2ray_keys 
                    WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
                ) ORDER BY priority ASC, sort_date DESC LIMIT 1
            """, (user_id, user_id, user_id))
            email_row = c.fetchone()
            
            c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
            ref_cnt = c.fetchone()
            return {
                "user_id": user_id,
                "outline_count": int(outline_row[0] or 0),
                "v2ray_count": int(v2ray_row[0] or 0),
                "last_activity": max(int(outline_row[1] or 0), int(v2ray_row[1] or 0)),
                "email": email_row[0] if email_row else "",
                "referrals": int((ref_cnt or [0])[0] or 0),
            }

    def list_user_keys(self, user_id: int, limit: int = 50, offset: int = 0) -> list[tuple]:
        from app.repositories.key_repository import KeyRepository
        repo = KeyRepository(self.db_path)
        return repo.list_keys_unified(user_id=user_id, limit=limit, offset=offset)

    def count_user_keys(self, user_id: int) -> int:
        from app.repositories.key_repository import KeyRepository
        repo = KeyRepository(self.db_path)
        return repo.count_keys_unified(user_id=user_id)


