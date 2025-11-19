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

    def extend_subscription(self, subscription_id: int, new_expires_at: int) -> None:
        """Продлить подписку"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE subscriptions SET expires_at = ? WHERE id = ?",
                (new_expires_at, subscription_id),
            )
            conn.commit()

    def get_expired_subscriptions(self, grace_threshold: int) -> List[Tuple]:
        """Получить истекшие подписки (для grace period)"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT id, user_id, subscription_token
                FROM subscriptions
                WHERE expires_at <= ? AND is_active = 1
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

    def get_subscription_keys(self, subscription_id: int, user_id: int, now: int) -> List[Tuple]:
        """Получить все активные ключи подписки"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT k.v2ray_uuid, k.client_config, s.domain, s.api_url, s.api_key, s.country, s.name as server_name
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id = ? 
                  AND k.user_id = ?
                  AND k.expiry_at > ?
                  AND (k.traffic_limit_mb = 0 OR k.traffic_usage_bytes < k.traffic_limit_mb * 1024 * 1024)
                  AND s.active = 1
                ORDER BY s.country, s.name
                """,
                (subscription_id, user_id, now),
            )
            return c.fetchall()

    def get_subscription_keys_for_deletion(self, subscription_id: int) -> List[Tuple]:
        """Получить все ключи подписки для удаления"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT k.v2ray_uuid, s.api_url, s.api_key
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id = ?
                """,
                (subscription_id,),
            )
            return c.fetchall()

    def delete_subscription_keys(self, subscription_id: int) -> int:
        """Удалить все ключи подписки из БД"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "DELETE FROM v2ray_keys WHERE subscription_id = ?",
                (subscription_id,),
            )
            deleted_count = c.rowcount
            conn.commit()
            return deleted_count

    # ========== Асинхронные методы ==========
    
    async def create_subscription_async(
        self,
        user_id: int,
        subscription_token: str,
        expires_at: int,
        tariff_id: Optional[int] = None,
    ) -> int:
        """Создать новую подписку (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            now = int(time.time())
            cursor = await conn.execute(
                """
                INSERT INTO subscriptions (user_id, subscription_token, created_at, expires_at, tariff_id, is_active, notified)
                VALUES (?, ?, ?, ?, ?, 1, 0)
                """,
                (user_id, subscription_token, now, expires_at, tariff_id),
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

    async def extend_subscription_async(self, subscription_id: int, new_expires_at: int) -> None:
        """Продлить подписку (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            await conn.execute(
                "UPDATE subscriptions SET expires_at = ? WHERE id = ?",
                (new_expires_at, subscription_id),
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

    async def get_subscription_keys_async(self, subscription_id: int, user_id: int, now: int) -> List[Tuple]:
        """Получить все активные ключи подписки (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT k.v2ray_uuid, k.client_config, s.domain, s.api_url, s.api_key, s.country, s.name as server_name
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id = ? 
                  AND k.user_id = ?
                  AND k.expiry_at > ?
                  AND (k.traffic_limit_mb = 0 OR k.traffic_usage_bytes < k.traffic_limit_mb * 1024 * 1024)
                  AND s.active = 1
                ORDER BY s.country, s.name
                """,
                (subscription_id, user_id, now),
            ) as cursor:
                rows = await cursor.fetchall()
                return rows

    async def get_subscription_keys_for_deletion_async(self, subscription_id: int) -> List[Tuple]:
        """Получить все ключи подписки для удаления (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT k.v2ray_uuid, s.api_url, s.api_key
                FROM v2ray_keys k
                JOIN servers s ON k.server_id = s.id
                WHERE k.subscription_id = ?
                """,
                (subscription_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return rows

    async def delete_subscription_keys_async(self, subscription_id: int) -> int:
        """Удалить все ключи подписки из БД (асинхронная версия)"""
        async with open_async_connection(self.db_path) as conn:
            cursor = await conn.execute(
                "DELETE FROM v2ray_keys WHERE subscription_id = ?",
                (subscription_id,),
            )
            deleted_count = cursor.rowcount
            await conn.commit()
            return deleted_count

    def list_subscriptions(self, limit: int = 50, offset: int = 0) -> List[Tuple]:
        """Получить список всех подписок с информацией о ключах"""
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
                    COUNT(vk.id) as keys_count
                FROM subscriptions s
                LEFT JOIN tariffs t ON s.tariff_id = t.id
                LEFT JOIN v2ray_keys vk ON vk.subscription_id = s.id
                GROUP BY s.id
                ORDER BY s.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            return c.fetchall()

    def count_subscriptions(self) -> int:
        """Получить общее количество подписок"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM subscriptions")
            return c.fetchone()[0]

    def get_subscription_by_id(self, subscription_id: int) -> Optional[Tuple]:
        """Получить подписку по ID"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT id, user_id, subscription_token, created_at, expires_at, tariff_id, is_active, last_updated_at, notified
                FROM subscriptions
                WHERE id = ?
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
                    vk.id,
                    vk.v2ray_uuid,
                    vk.email,
                    vk.created_at,
                    vk.expiry_at,
                    s.name as server_name,
                    s.country,
                    vk.traffic_limit_mb,
                    vk.traffic_usage_bytes
                FROM v2ray_keys vk
                JOIN servers s ON vk.server_id = s.id
                WHERE vk.subscription_id = ?
                ORDER BY s.country, s.name
                """,
                (subscription_id,),
            )
            return c.fetchall()

    def update_subscription_keys_expiry(self, subscription_id: int, new_expires_at: int) -> int:
        """Обновить срок действия всех ключей подписки"""
        with open_connection(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                """
                UPDATE v2ray_keys 
                SET expiry_at = ? 
                WHERE subscription_id = ?
                """,
                (new_expires_at, subscription_id),
            )
            updated_count = c.rowcount
            conn.commit()
            return updated_count

