from __future__ import annotations

from typing import List, Tuple, Optional

from app.infra.sqlite_utils import open_connection
from app.settings import settings


class UserRepository:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.DATABASE_PATH

    @staticmethod
    def _resolve_user_email(cursor, user_id: int) -> str:
        cursor.execute("""
            SELECT email FROM (
                SELECT email, 1 AS priority, created_at AS sort_date
                FROM payments
                WHERE user_id = ? AND email IS NOT NULL AND email != '' AND email NOT LIKE 'user_%@veilbot.com'
                UNION ALL
                SELECT k.email, 2 AS priority, COALESCE(sub.expires_at, k.created_at) AS sort_date
                FROM keys k
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND k.email IS NOT NULL AND k.email != '' AND k.email NOT LIKE 'user_%@veilbot.com'
                UNION ALL
                SELECT k.email, 3 AS priority, COALESCE(sub.expires_at, k.created_at) AS sort_date
                FROM v2ray_keys k
                LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.user_id = ? AND k.email IS NOT NULL AND k.email != '' AND k.email NOT LIKE 'user_%@veilbot.com'
            ) ORDER BY priority ASC, sort_date DESC LIMIT 1
        """, (user_id, user_id, user_id))
        row = cursor.fetchone()
        return row[0] if row else ""

    def count_users(self, query: Optional[str] = None) -> int:
        """Подсчет всех пользователей из таблицы users"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            if query:
                like = f"%{query.strip()}%"
                sql = "SELECT COUNT(*) FROM users WHERE CAST(user_id AS TEXT) LIKE ?"
                c.execute(sql, (like,))
            else:
                sql = "SELECT COUNT(*) FROM users"
                c.execute(sql)
            row = c.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def list_users(self, query: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Tuple[int, int, int]]:
        """
        Возвращает список (user_id, referral_count, is_vip) с пагинацией и поиском по user_id.
        Источник пользователей — таблица users (все пользователи, которые когда-либо нажали /start).
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()

            if query:
                like = f"%{query.strip()}%"
                sql = (
                    "SELECT u.user_id, "
                    "       (SELECT COUNT(*) FROM referrals r WHERE r.referrer_id = u.user_id) AS referral_count, "
                    "       COALESCE(u.is_vip, 0) as is_vip "
                    "FROM users u "
                    "WHERE CAST(u.user_id AS TEXT) LIKE ? "
                    "ORDER BY u.user_id LIMIT ? OFFSET ?"
                )
                c.execute(sql, (like, limit, offset))
            else:
                sql = (
                    "SELECT u.user_id, "
                    "       (SELECT COUNT(*) FROM referrals r WHERE r.referrer_id = u.user_id) AS referral_count, "
                    "       COALESCE(u.is_vip, 0) as is_vip "
                    "FROM users u "
                    "ORDER BY u.user_id LIMIT ? OFFSET ?"
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
            email_value = self._resolve_user_email(c, user_id)
            c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
            ref_cnt = c.fetchone()
            outline_last = int(outline_row[1] or 0)
            v2ray_last = int(v2ray_row[1] or 0)
            return {
                "user_id": user_id,
                "outline_count": int(outline_row[0] or 0),
                "v2ray_count": int(v2ray_row[0] or 0),
                "last_activity": max(outline_last, v2ray_last),
                "email": email_value,
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

    def count_total_referrals(self) -> int:
        """Подсчет общего количества рефералов для всех пользователей"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM referrals")
            row = c.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def is_user_vip(self, user_id: int) -> bool:
        """Проверить, является ли пользователь VIP"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COALESCE(is_vip, 0) FROM users WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            return bool(row[0] if row else 0)
    
    def set_user_vip_status(self, user_id: int, is_vip: bool) -> None:
        """Установить VIP статус пользователя"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET is_vip = ? WHERE user_id = ?", (1 if is_vip else 0, user_id))
            conn.commit()
    
    def count_active_users(self) -> int:
        """Подсчет активных пользователей (с активными подписками)"""
        import time
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            c.execute("""
                SELECT COUNT(DISTINCT user_id) FROM (
                    SELECT k.user_id
                    FROM keys k
                    JOIN subscriptions s ON k.subscription_id = s.id
                    WHERE s.expires_at > ? AND s.is_active = 1
                    UNION
                    SELECT k.user_id
                    FROM v2ray_keys k
                    JOIN subscriptions s ON k.subscription_id = s.id
                    WHERE s.expires_at > ? AND s.is_active = 1
                )
            """, (now, now))
            row = c.fetchone()
            return int(row[0] if row and row[0] is not None else 0)

    def list_referrals(self, referrer_id: int) -> list[dict]:
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT referred_id, created_at, bonus_issued
                FROM referrals
                WHERE referrer_id = ?
                ORDER BY created_at DESC, referred_id ASC
                """,
                (referrer_id,),
            )
            rows = c.fetchall()
            results: list[dict] = []
            for referred_id, created_at, bonus_issued in rows:
                email_value = self._resolve_user_email(c, referred_id)
                results.append(
                    {
                        "user_id": referred_id,
                        "email": email_value,
                        "created_at": created_at,
                        "bonus_issued": bool(bonus_issued),
                    }
                )
            return results


