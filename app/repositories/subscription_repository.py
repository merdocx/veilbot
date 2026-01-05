"""
Репозиторий для работы с подписками V2Ray
Поддерживает как синхронные, так и асинхронные методы
"""
from __future__ import annotations

import sqlite3
import time
from typing import List, Tuple, Optional
from app.settings import settings
from app.infra.sqlite_utils import open_connection, open_async_connection


class SubscriptionRepository:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.DATABASE_PATH

    def create_subscription(
        self,
        user_id: int,
        subscription_token: str,
        expires_at: int,
        tariff_id: Optional[int] = None,
    ) -> int:
        """Создать новую подписку"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            c.execute(
                """
                INSERT INTO subscriptions (user_id, subscription_token, created_at, expires_at, tariff_id, is_active, notified)
                VALUES (?, ?, ?, ?, ?, 1, 0)
                """,
                (user_id, subscription_token, now, expires_at, tariff_id),
            )
            conn.commit()
            return c.lastrowid

    def get_subscription_by_token(self, token: str) -> Optional[Tuple]:
        """Получить подписку по токену"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE subscription_token = ?
                """,
                (token,),
            )
            return c.fetchone()

    def get_active_subscription(self, user_id: int) -> Optional[Tuple]:
        """Получить активную подписку пользователя"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            c.execute(
                """
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, now),
            )
            return c.fetchone()

    def update_subscription_last_updated(self, subscription_id: int) -> None:
        """Обновить last_updated_at для подписки"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            c.execute(
                "UPDATE subscriptions SET last_updated_at = ? WHERE id = ?",
                (now, subscription_id),
            )
            conn.commit()

    def deactivate_subscription(self, subscription_id: int) -> None:
        """
        Деактивировать подписку
        
        ВНИМАНИЕ: Этот метод только деактивирует подписку в БД.
        Перед вызовом этого метода необходимо удалить все ключи подписки с серверов через V2Ray API!
        Используйте get_subscription_keys_for_deletion() для получения списка ключей.
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE subscriptions SET is_active = 0 WHERE id = ?",
                (subscription_id,),
            )
            conn.commit()

    def extend_subscription(self, subscription_id: int, new_expires_at: int, tariff_id: Optional[int] = None) -> None:
        """Продлить подписку
        
        Args:
            subscription_id: ID подписки
            new_expires_at: Новый срок действия (timestamp)
            tariff_id: Опционально - обновить tariff_id подписки. Если None, сохраняется текущее значение.
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            
            # Если tariff_id не передан, сохраняем текущее значение из БД
            if tariff_id is None:
                c.execute("SELECT tariff_id FROM subscriptions WHERE id = ?", (subscription_id,))
                row = c.fetchone()
                tariff_id = row[0] if row else None
            
            # Обновляем все поля, включая tariff_id
            c.execute(
                "UPDATE subscriptions SET expires_at = ?, last_updated_at = ?, purchase_notification_sent = 0, tariff_id = ? WHERE id = ?",
                (new_expires_at, now, tariff_id, subscription_id),
            )
            conn.commit()

    def get_expired_subscriptions(self, grace_threshold: int) -> List[Tuple]:
        """Получить истекшие подписки (для grace period) - включая деактивированные"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            # Ищем все подписки, которые истекли более 24 часов назад
            # Включаем деактивированные, так как они тоже должны быть удалены
            c.execute(
                """
                SELECT id, user_id, subscription_token
                FROM subscriptions
                WHERE expires_at <= ? AND expires_at > 0
                """,
                (grace_threshold,),
            )
            return c.fetchall()

    def get_expiring_subscriptions(self, now: int) -> List[Tuple]:
        """Получить активные подписки для проверки уведомлений"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT id, user_id, subscription_token, expires_at, created_at, COALESCE(notified, 0) as notified
                FROM subscriptions
                WHERE expires_at > ? AND is_active = 1
                """,
                (now,),
            )
            return c.fetchall()

    def update_subscription_notified(self, subscription_id: int, notified: int) -> None:
        """Обновить флаги уведомлений для подписки"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE subscriptions SET notified = ? WHERE id = ?",
                (notified, subscription_id),
            )
            conn.commit()

    def mark_purchase_notification_sent(self, subscription_id: int) -> None:
        """Пометить уведомление о покупке как отправленное"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE subscriptions SET purchase_notification_sent = 1 WHERE id = ?",
                (subscription_id,),
            )
            conn.commit()

    def get_subscriptions_without_purchase_notification(self, limit: int = 50, max_age_days: int = 7) -> List[Tuple]:
        """Получить подписки без отправленного уведомления о покупке
        
        Включает как новые подписки (по created_at), так и продленные (по last_updated_at)
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            max_age_seconds = max_age_days * 86400
            c.execute("""
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, last_updated_at
                FROM subscriptions
                WHERE purchase_notification_sent = 0 
                  AND is_active = 1
                  AND (
                      -- Новые подписки (созданные недавно)
                      created_at > ?
                      OR
                      -- Продленные подписки (обновленные недавно)
                      (last_updated_at IS NOT NULL AND last_updated_at > ?)
                  )
                ORDER BY COALESCE(last_updated_at, created_at) ASC
                LIMIT ?
            """, (now - max_age_seconds, now - max_age_seconds, limit))
            return c.fetchall()

    def get_subscription_keys(self, subscription_id: int, user_id: int, now: int) -> List[Tuple]:
        """Получить все активные ключи подписки
        
        Примечание: Лимиты трафика и времени контролируются на уровне подписки,
        а не отдельных ключей. Срок действия берется из subscriptions.expires_at.
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT k.v2ray_uuid, k.client_config, s.domain, s.api_url, s.api_key, s.country, s.name as server_name
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.subscription_id = ? 
                  AND k.user_id = ?
                  AND sub.expires_at > ?
                  AND s.active = 1
                ORDER BY s.country, s.name
                """,
                (subscription_id, user_id, now),
            )
            return c.fetchall()

    def get_subscription_keys_for_deletion(self, subscription_id: int) -> List[Tuple]:
        """Получить все ключи подписки для удаления (v2ray и outline)
        Returns: List of tuples: (key_id, api_url, api_key/cert_sha256, protocol)"""
        result = []
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            # V2Ray ключи
            c.execute(
                """
                SELECT k.v2ray_uuid, s.api_url, s.api_key, 'v2ray' as protocol
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id = ?
                """,
                (subscription_id,),
            )
            v2ray_keys = c.fetchall()
            result.extend(v2ray_keys)
            
            # Outline ключи
            c.execute(
                """
                SELECT k.key_id, s.api_url, s.cert_sha256, 'outline' as protocol
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id = ? AND k.protocol = 'outline'
                """,
                (subscription_id,),
            )
            outline_keys = c.fetchall()
            result.extend(outline_keys)
            
        return result
    
    def get_subscription_keys_with_server_info(self, subscription_id: int) -> List[Tuple]:
        """Получить все ключи подписки с информацией о серверах для получения трафика из API
        Returns: List of (key_id, v2ray_uuid, server_id, api_url, api_key) tuples"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT k.id, k.v2ray_uuid, k.server_id, s.api_url, s.api_key
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.subscription_id = ?
                  AND sub.expires_at > ?
                """,
                (subscription_id, int(time.time())),
            )
            return c.fetchall()

    def delete_subscription_keys(self, subscription_id: int) -> int:
        """Удалить все ключи подписки из БД (v2ray и outline)"""
        from app.infra.foreign_keys import safe_foreign_keys_off
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            with safe_foreign_keys_off(c):
                c.execute(
                    "DELETE FROM v2ray_keys WHERE subscription_id = ?",
                    (subscription_id,),
                )
                v2ray_deleted = c.rowcount
                c.execute(
                    "DELETE FROM keys WHERE subscription_id = ?",
                    (subscription_id,),
                )
                outline_deleted = c.rowcount
                deleted_count = v2ray_deleted + outline_deleted
            conn.commit()
            return deleted_count

    # ========== Асинхронные методы ==========
    
    async def create_subscription_async(
        self,
        user_id: int,
        subscription_token: str,
        expires_at: int,
        tariff_id: Optional[int] = None,
        traffic_limit_mb: Optional[int] = None,
    ) -> int:
        """Создать новую подписку (асинхронная версия)"""
        import logging
        logger = logging.getLogger(__name__)
        
        async with open_async_connection(self.db_path) as conn:
            now = int(time.time())
            # Если traffic_limit_mb не передан, пытаемся получить из тарифа
            if traffic_limit_mb is None and tariff_id:
                try:
                    async with conn.execute(
                        "SELECT traffic_limit_mb FROM tariffs WHERE id = ?",
                        (tariff_id,)
                    ) as tariff_cursor:
                        tariff_row = await tariff_cursor.fetchone()
                        if tariff_row:
                            traffic_limit_mb = tariff_row[0] or 0
                except Exception:
                    traffic_limit_mb = 0
            if traffic_limit_mb is None:
                traffic_limit_mb = 0
            
            # ВАЛИДАЦИЯ: Проверяем, что expires_at больше created_at (не нулевая длительность)
            if expires_at <= now:
                error_msg = f"Subscription expires_at must be greater than created_at for user {user_id}: expires_at={expires_at}, created_at={now}"
                logger.error(f"[SUBSCRIPTION_VALIDATION] {error_msg}")
                raise ValueError(error_msg)
            
            # ВАЛИДАЦИЯ: Проверяем только базовую валидность (не слишком далеко в будущем)
            # НЕ проверяем соответствие created_at + duration_sec, так как подписка может быть продлена
            MAX_REASONABLE_EXPIRY = now + (10 * 365 * 24 * 3600)  # 10 лет
            if expires_at > MAX_REASONABLE_EXPIRY:
                logger.error(
                    f"[SUBSCRIPTION_VALIDATION] Subscription expires_at is too far in future for user {user_id}: "
                    f"expires_at={expires_at} (more than 10 years). Rejecting creation."
                )
                raise ValueError(f"expires_at is too far in future: {expires_at}")
            
            cursor = await conn.execute(
                """
                INSERT INTO subscriptions (user_id, subscription_token, created_at, expires_at, tariff_id, is_active, notified, traffic_limit_mb)
                VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (user_id, subscription_token, now, expires_at, tariff_id, traffic_limit_mb),
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_subscription_by_token_async(self, token: str) -> Optional[Tuple]:
        """Получить подписку по токену (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE subscription_token = ?
                """,
                (token,),
            ) as cursor:
                row = await cursor.fetchone()
                return row

    async def get_active_subscription_async(self, user_id: int) -> Optional[Tuple]:
        """Получить активную подписку пользователя (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            now = int(time.time())
            async with conn.execute(
                """
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE user_id = ? AND is_active = 1 AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, now),
            ) as cursor:
                row = await cursor.fetchone()
                return row

    async def update_subscription_last_updated_async(self, subscription_id: int) -> None:
        """Обновить last_updated_at для подписки (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            now = int(time.time())
            await conn.execute(
                "UPDATE subscriptions SET last_updated_at = ? WHERE id = ?",
                (now, subscription_id),
            )
            await conn.commit()

    async def deactivate_subscription_async(self, subscription_id: int) -> None:
        """
        Деактивировать подписку (асинхронная версия)
        
        ВНИМАНИЕ: Этот метод только деактивирует подписку в БД.
        Перед вызовом этого метода необходимо удалить все ключи подписки с серверов через V2Ray API!
        Используйте get_subscription_keys_for_deletion_async() для получения списка ключей.
        """
        async with open_async_connection(self.db_path) as conn:
            await conn.execute(
                "UPDATE subscriptions SET is_active = 0 WHERE id = ?",
                (subscription_id,),
            )
            await conn.commit()

    async def extend_subscription_async(self, subscription_id: int, new_expires_at: int, tariff_id: Optional[int] = None) -> None:
        """Продлить подписку (асинхронная версия)
        
        Args:
            subscription_id: ID подписки
            new_expires_at: Новый срок действия (timestamp)
            tariff_id: Опционально - обновить tariff_id подписки. Если None, сохраняется текущее значение.
        """
        async with open_async_connection(self.db_path) as conn:
            now = int(time.time())
            
            # Если tariff_id не передан, сохраняем текущее значение из БД
            if tariff_id is None:
                async with conn.execute("SELECT tariff_id FROM subscriptions WHERE id = ?", (subscription_id,)) as cursor:
                    row = await cursor.fetchone()
                    tariff_id = row[0] if row else None
            
            # Обновляем все поля, включая tariff_id
            await conn.execute(
                "UPDATE subscriptions SET expires_at = ?, last_updated_at = ?, purchase_notification_sent = 0, tariff_id = ? WHERE id = ?",
                (new_expires_at, now, tariff_id, subscription_id),
            )
            await conn.commit()

    async def get_expired_subscriptions_async(self, grace_threshold: int) -> List[Tuple]:
        """Получить истекшие подписки (для grace period) (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT id, user_id, subscription_token
                FROM subscriptions
                WHERE expires_at <= ? AND is_active = 1
                """,
                (grace_threshold,),
            ) as cursor:
                rows = await cursor.fetchall()
                return rows

    async def get_expiring_subscriptions_async(self, now: int) -> List[Tuple]:
        """Получить активные подписки для проверки уведомлений (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT id, user_id, subscription_token, expires_at, created_at, COALESCE(notified, 0) as notified
                FROM subscriptions
                WHERE expires_at > ? AND is_active = 1
                """,
                (now,),
            ) as cursor:
                rows = await cursor.fetchall()
                return rows

    async def update_subscription_notified_async(self, subscription_id: int, notified: int) -> None:
        """Обновить флаги уведомлений для подписки (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            await conn.execute(
                "UPDATE subscriptions SET notified = ? WHERE id = ?",
                (notified, subscription_id),
            )
            await conn.commit()

    async def mark_purchase_notification_sent_async(self, subscription_id: int) -> None:
        """Пометить уведомление о покупке как отправленное (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            await conn.execute(
                "UPDATE subscriptions SET purchase_notification_sent = 1 WHERE id = ?",
                (subscription_id,),
            )
            await conn.commit()

    async def get_subscription_keys_async(self, subscription_id: int, user_id: int, now: int) -> List[Tuple]:
        """Получить все активные ключи подписки (асинхронная версия)
        
        Примечание: Лимиты трафика и времени контролируются на уровне подписки,
        а не отдельных ключей. Поэтому проверка лимита на уровне ключа не выполняется.
        """
        async with open_async_connection(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT k.v2ray_uuid, k.client_config, s.domain, s.api_url, s.api_key, s.country, s.name as server_name
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                JOIN subscriptions sub ON k.subscription_id = sub.id
                WHERE k.subscription_id = ? 
                  AND k.user_id = ?
                  AND sub.expires_at > ?
                  AND s.active = 1
                ORDER BY s.country, s.name
                """,
                (subscription_id, user_id, now),
            ) as cursor:
                rows = await cursor.fetchall()
                return rows

    async def get_subscription_keys_for_deletion_async(self, subscription_id: int) -> List[Tuple]:
        """Получить все ключи подписки для удаления (асинхронная версия, v2ray и outline)
        Returns: List of tuples: (key_id, api_url, api_key/cert_sha256, protocol)"""
        result = []
        async with open_async_connection(self.db_path) as conn:
            # V2Ray ключи
            async with conn.execute(
                """
                SELECT k.v2ray_uuid, s.api_url, s.api_key, 'v2ray' as protocol
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id = ?
                """,
                (subscription_id,),
            ) as cursor:
                v2ray_keys = await cursor.fetchall()
                result.extend(v2ray_keys)
            
            # Outline ключи
            async with conn.execute(
                """
                SELECT k.key_id, s.api_url, s.cert_sha256, 'outline' as protocol
                FROM keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id = ? AND k.protocol = 'outline'
                """,
                (subscription_id,),
            ) as cursor:
                outline_keys = await cursor.fetchall()
                result.extend(outline_keys)
        
        return result

    async def delete_subscription_keys_async(self, subscription_id: int) -> int:
        """Удалить все ключи подписки из БД (асинхронная версия, v2ray и outline)"""
        async with open_async_connection(self.db_path) as conn:
            # Отключаем foreign keys для асинхронного соединения
            await conn.execute("PRAGMA foreign_keys=OFF")
            try:
                cursor = await conn.execute(
                    "DELETE FROM v2ray_keys WHERE subscription_id = ?",
                    (subscription_id,),
                )
                v2ray_deleted = cursor.rowcount
                cursor = await conn.execute(
                    "DELETE FROM keys WHERE subscription_id = ?",
                    (subscription_id,),
                )
                outline_deleted = cursor.rowcount
                deleted_count = v2ray_deleted + outline_deleted
                await conn.commit()
                return deleted_count
            finally:
                # Восстанавливаем foreign keys
                await conn.execute("PRAGMA foreign_keys=ON")

    def list_subscriptions(self, query: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Tuple]:
        """Получить список всех активных подписок с информацией о ключах"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            
            if query:
                like = f"%{query.strip()}%"
                sql = """
                SELECT 
                    s.id,
                    s.user_id,
                    s.subscription_token,
                    s.created_at,
                    s.expires_at,
                    s.tariff_id,
                    s.is_active,
                    s.last_updated_at,
                    s.notified,
                    t.name as tariff_name,
                    COALESCE(vk.v2ray_count, 0) + COALESCE(ok.outline_count, 0) as keys_count,
                    s.traffic_limit_mb
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                LEFT JOIN (
                    SELECT subscription_id, COUNT(*) as v2ray_count
                    FROM v2ray_keys
                    GROUP BY subscription_id
                ) vk ON vk.subscription_id = s.id
                LEFT JOIN (
                    SELECT subscription_id, COUNT(*) as outline_count
                    FROM keys
                    WHERE protocol = 'outline'
                    GROUP BY subscription_id
                ) ok ON ok.subscription_id = s.id
                WHERE s.is_active = 1
                  AND (CAST(s.id AS TEXT) LIKE ?
                    OR CAST(s.user_id AS TEXT) LIKE ?
                    OR s.subscription_token LIKE ?
                    OR t.name LIKE ?)
                ORDER BY s.created_at DESC
                LIMIT ? OFFSET ?
                """
                c.execute(sql, (like, like, like, like, limit, offset))
            else:
                sql = """
                SELECT 
                    s.id,
                    s.user_id,
                    s.subscription_token,
                    s.created_at,
                    s.expires_at,
                    s.tariff_id,
                    s.is_active,
                    s.last_updated_at,
                    s.notified,
                    t.name as tariff_name,
                    COALESCE(vk.v2ray_count, 0) + COALESCE(ok.outline_count, 0) as keys_count,
                    s.traffic_limit_mb
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                LEFT JOIN (
                    SELECT subscription_id, COUNT(*) as v2ray_count
                    FROM v2ray_keys
                    GROUP BY subscription_id
                ) vk ON vk.subscription_id = s.id
                LEFT JOIN (
                    SELECT subscription_id, COUNT(*) as outline_count
                    FROM keys
                    WHERE protocol = 'outline'
                    GROUP BY subscription_id
                ) ok ON ok.subscription_id = s.id
                WHERE s.is_active = 1
                ORDER BY s.created_at DESC
                LIMIT ? OFFSET ?
                """
                c.execute(sql, (limit, offset))
            return c.fetchall()

    def count_subscriptions(self, query: Optional[str] = None) -> int:
        """Получить общее количество активных подписок"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            if query:
                like = f"%{query.strip()}%"
                sql = """
                SELECT COUNT(*)
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                WHERE s.is_active = 1
                  AND (CAST(s.id AS TEXT) LIKE ?
                    OR CAST(s.user_id AS TEXT) LIKE ?
                    OR s.subscription_token LIKE ?
                    OR t.name LIKE ?)
                """
                c.execute(sql, (like, like, like, like))
            else:
                c.execute("SELECT COUNT(*) FROM subscriptions WHERE is_active = 1")
            row = c.fetchone()
            return row[0] if row else 0

    def get_subscription_by_id(self, subscription_id: int) -> Optional[Tuple]:
        """Получить подписку по ID с информацией о тарифе и количестве ключей"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT 
                    s.id,
                    s.user_id,
                    s.subscription_token,
                    s.created_at,
                    s.expires_at,
                    s.tariff_id,
                    s.is_active,
                    s.last_updated_at,
                    s.notified,
                    t.name as tariff_name,
                    COALESCE(vk.v2ray_count, 0) + COALESCE(ok.outline_count, 0) as keys_count,
                    s.traffic_limit_mb
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                LEFT JOIN (
                    SELECT subscription_id, COUNT(*) as v2ray_count
                    FROM v2ray_keys
                    GROUP BY subscription_id
                ) vk ON vk.subscription_id = s.id
                LEFT JOIN (
                    SELECT subscription_id, COUNT(*) as outline_count
                    FROM keys
                    WHERE protocol = 'outline'
                    GROUP BY subscription_id
                ) ok ON ok.subscription_id = s.id
                WHERE s.id = ?
                """,
                (subscription_id,),
            )
            return c.fetchone()

    def get_subscription_keys_list(self, subscription_id: int) -> List[Tuple]:
        """Получить список всех ключей подписки с информацией о серверах"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT 
                    key_id,
                    protocol,
                    identifier,
                    email,
                    created_at,
                    expiry_at,
                    server_name,
                    country,
                    traffic_limit_mb,
                    traffic_usage_bytes
                FROM (
                    SELECT 
                        vk.id AS key_id,
                        'v2ray' AS protocol,
                        vk.v2ray_uuid AS identifier,
                        vk.email,
                        vk.created_at,
                        COALESCE(sub.expires_at, 0) as expiry_at,
                        s.name AS server_name,
                        s.country,
                        vk.traffic_limit_mb,
                        vk.traffic_usage_bytes
                    FROM v2ray_keys vk
                    JOIN servers s ON vk.server_id = s.id
                    LEFT JOIN subscriptions sub ON vk.subscription_id = sub.id
                    WHERE vk.subscription_id = ?
                    
                    UNION ALL
                    
                    SELECT 
                        k.id AS key_id,
                        'outline' AS protocol,
                        k.key_id AS identifier,
                        k.email,
                        k.created_at,
                        COALESCE(sub.expires_at, 0) as expiry_at,
                        s.name AS server_name,
                        s.country,
                        k.traffic_limit_mb,
                        0 AS traffic_usage_bytes
                    FROM keys k
                    JOIN servers s ON k.server_id = s.id
                    LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                    WHERE k.subscription_id = ? AND k.protocol = 'outline'
                )
                ORDER BY server_name, country, protocol
                """,
                (subscription_id, subscription_id),
            )
            return c.fetchall()

    def get_subscription_traffic_sum(self, subscription_id: int) -> int:
        """Получить суммарный трафик всех ключей подписки"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT COALESCE(SUM(traffic_usage_bytes), 0)
                FROM v2ray_keys
                WHERE subscription_id = ?
            """, (subscription_id,))
            result = c.fetchone()
            return int(result[0] or 0) if result else 0
    
    def get_all_subscriptions_traffic_sum(self, subscription_ids: list[int]) -> dict[int, int]:
        """Получить суммарный трафик всех ключей для списка подписок (batch-операция)
        
        Returns:
            dict: {subscription_id: total_usage_bytes}
        """
        if not subscription_ids:
            return {}
        
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            # Используем IN для получения всех сумм одним запросом
            placeholders = ','.join('?' * len(subscription_ids))
            c.execute(f"""
                SELECT 
                    subscription_id,
                    COALESCE(SUM(traffic_usage_bytes), 0) as total_usage
                FROM v2ray_keys
                WHERE subscription_id IN ({placeholders})
                GROUP BY subscription_id
            """, subscription_ids)
            results = c.fetchall()
            # Создаем словарь, включая подписки с нулевым трафиком
            traffic_map = {sub_id: 0 for sub_id in subscription_ids}
            for row in results:
                traffic_map[row[0]] = int(row[1] or 0)
            return traffic_map
    
    def get_subscription_traffic_limit(self, subscription_id: int) -> int:
        """Получить лимит трафика подписки (в байтах)
        Логика:
        - Если traffic_limit_mb установлен (не NULL), используется он (0 = безлимит)
        - Если traffic_limit_mb NULL, используется лимит из тарифа
        - Если и там 0/NULL, пробуем взять единый лимит из ключей подписки (fallback)"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            # Сначала проверяем индивидуальный лимит подписки
            c.execute("""
                SELECT traffic_limit_mb
                FROM subscriptions
                WHERE id = ?
            """, (subscription_id,))
            result = c.fetchone()
            # Если значение установлено (не NULL), используем его (даже если 0 = безлимит)
            if result and result[0] is not None:
                return int(result[0]) * 1024 * 1024  # Конвертация МБ в байты
            
            # Если индивидуального лимита нет (NULL), берем из тарифа
            c.execute("""
                SELECT COALESCE(t.traffic_limit_mb, 0)
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                WHERE s.id = ?
            """, (subscription_id,))
            result = c.fetchone()
            if result and result[0]:
                return int(result[0]) * 1024 * 1024  # Конвертация МБ в байты

            # Fallback: если ни подписка, ни тариф не задали лимит, пробуем взять его из ключей.
            # Это покрывает старые данные, где лимит выставлялся только на уровне ключей.
            c.execute("""
                SELECT DISTINCT traffic_limit_mb
                FROM v2ray_keys
                WHERE subscription_id = ?
                  AND traffic_limit_mb IS NOT NULL
                  AND traffic_limit_mb > 0
            """, (subscription_id,))
            key_limits = {int(row[0]) for row in c.fetchall() if row and row[0] is not None}
            if len(key_limits) == 1:
                # Все ключи подписки используют одинаковый положительный лимит — считаем его лимитом подписки
                return next(iter(key_limits)) * 1024 * 1024

            return 0
    
    def update_subscription_traffic(self, subscription_id: int, usage_bytes: int) -> None:
        """Обновить трафик подписки"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            c.execute("""
                UPDATE subscriptions
                SET traffic_usage_bytes = ?,
                    last_updated_at = ?
                WHERE id = ?
            """, (usage_bytes, now, subscription_id))
            conn.commit()
    
    def batch_update_subscriptions_traffic(self, traffic_updates: list[tuple[int, int]]) -> None:
        """Batch-обновление трафика для нескольких подписок
        
        Args:
            traffic_updates: список кортежей (subscription_id, usage_bytes)
        """
        if not traffic_updates:
            return
        
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            # Подготавливаем данные с добавлением timestamp
            updates_with_time = [(usage_bytes, now, sub_id) for sub_id, usage_bytes in traffic_updates]
            c.executemany("""
                UPDATE subscriptions
                SET traffic_usage_bytes = ?,
                    last_updated_at = ?
                WHERE id = ?
            """, updates_with_time)
            conn.commit()
    
    def update_subscription_traffic_limit(self, subscription_id: int, traffic_limit_mb: int | None) -> None:
        """Обновить лимит трафика подписки (в МБ)
        Если traffic_limit_mb = None, поле не обновляется
        Если traffic_limit_mb = 0, устанавливается безлимит (0 байт)
        Если traffic_limit_mb > 0, устанавливается конкретный лимит
        Если нужно использовать лимит из тарифа, нужно установить NULL (не 0)"""
        import logging
        logger = logging.getLogger(__name__)
        if traffic_limit_mb is None:
            logger.info(f"Skipping traffic_limit_mb update for subscription {subscription_id} (None value)")
            return
        
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            now = int(time.time())
            # Сохраняем значение как есть, даже если 0 (0 означает использовать лимит из тарифа)
            logger.info(f"Updating subscription {subscription_id} traffic_limit_mb: input={traffic_limit_mb}, saving={traffic_limit_mb}")
            c.execute("""
                UPDATE subscriptions
                SET traffic_limit_mb = ?,
                    last_updated_at = ?
                WHERE id = ?
            """, (traffic_limit_mb, now, subscription_id))
            conn.commit()
            # Проверяем, что значение действительно обновилось
            c.execute("SELECT traffic_limit_mb FROM subscriptions WHERE id = ?", (subscription_id,))
            result = c.fetchone()
            logger.info(f"Verified subscription {subscription_id} traffic_limit_mb in DB: {result[0] if result else 'None'}")
    
    async def update_subscription_traffic_limit_async(self, subscription_id: int, traffic_limit_mb: int | None) -> None:
        """Обновить лимит трафика подписки (в МБ) - асинхронная версия
        Если traffic_limit_mb = None, поле не обновляется
        Если traffic_limit_mb = 0, устанавливается безлимит (0 байт)
        Если traffic_limit_mb > 0, устанавливается конкретный лимит"""
        import logging
        logger = logging.getLogger(__name__)
        if traffic_limit_mb is None:
            logger.info(f"Skipping traffic_limit_mb update for subscription {subscription_id} (None value)")
            return
        
        async with open_async_connection(self.db_path) as conn:
            now = int(time.time())
            logger.info(f"Updating subscription {subscription_id} traffic_limit_mb: input={traffic_limit_mb}, saving={traffic_limit_mb}")
            await conn.execute(
                """
                UPDATE subscriptions
                SET traffic_limit_mb = ?,
                    last_updated_at = ?
                WHERE id = ?
                """,
                (traffic_limit_mb, now, subscription_id)
            )
            await conn.commit()
            # Проверяем, что значение действительно обновилось
            async with conn.execute(
                "SELECT traffic_limit_mb FROM subscriptions WHERE id = ?",
                (subscription_id,)
            ) as cursor:
                result = await cursor.fetchone()
                logger.info(f"Verified subscription {subscription_id} traffic_limit_mb in DB: {result[0] if result else 'None'}")
    
    def get_subscriptions_with_traffic_limits(self, now: int) -> List[Tuple]:
        """Получить активные подписки с лимитами трафика
        Логика:
        - Если traffic_limit_mb установлен (не NULL), используется он (0 = безлимит)
        - Если traffic_limit_mb NULL, используется лимит из тарифа
        Возвращает только подписки с лимитом > 0 (безлимитные не включаются)
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT 
                    s.id,
                    s.user_id,
                    s.traffic_usage_bytes,
                    s.traffic_over_limit_at,
                    s.traffic_over_limit_notified,
                    s.expires_at,
                    s.tariff_id,
                    COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) AS traffic_limit_mb,
                    t.name AS tariff_name
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                WHERE s.is_active = 1
                  AND s.expires_at > ?
                  AND (COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) > 0)
            """, (now,))
            return c.fetchall()
    
    def update_subscription_keys_expiry(self, subscription_id: int, new_expires_at: int) -> int:
        """
        Обновить срок действия всех ключей подписки.
        
        ПРИМЕЧАНИЕ: После миграции expiry_at удален из таблиц keys и v2ray_keys.
        Срок действия ключей теперь берется из subscriptions.expires_at через JOIN.
        Этот метод оставлен для обратной совместимости, но не выполняет никаких действий.
        
        Returns:
            Количество ключей в подписке (для обратной совместимости)
        """
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            # Подсчитываем количество ключей в подписке
            c.execute("SELECT COUNT(*) FROM v2ray_keys WHERE subscription_id = ?", (subscription_id,))
            v2ray_count = c.fetchone()[0] or 0
            c.execute("SELECT COUNT(*) FROM keys WHERE subscription_id = ?", (subscription_id,))
            outline_count = c.fetchone()[0] or 0
            return v2ray_count + outline_count
    
    def update_subscription_keys_traffic_limit(self, subscription_id: int, traffic_limit_mb: int) -> int:
        """Обновить лимит трафика всех ключей подписки (в МБ)
        
        ВНИМАНИЕ: Этот метод обновляет значение в БД, но не влияет на логику проверки лимитов.
        Лимиты трафика для ключей подписки контролируются на уровне подписки, а не отдельных ключей.
        Этот метод оставлен для обратной совместимости и синхронизации данных в БД.
        """
        import logging
        logger = logging.getLogger(__name__)
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            logger.info(f"Updating traffic_limit_mb for all keys in subscription {subscription_id}: {traffic_limit_mb} MB")
            c.execute(
                """
                UPDATE v2ray_keys 
                SET traffic_limit_mb = ?
                WHERE subscription_id = ?
                """,
                (traffic_limit_mb, subscription_id),
            )
            updated_count = c.rowcount
            conn.commit()
            logger.info(f"Updated traffic_limit_mb for {updated_count} keys in subscription {subscription_id}")
            return updated_count

