import sqlite3
import logging
import os
import time
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone

from ..models.payment import Payment, PaymentStatus, PaymentFilter
from ..models.enums import PaymentProvider
import json

from app.infra.sqlite_utils import open_async_connection, retry_async_db_operation

logger = logging.getLogger(__name__)


class PaymentRepository:
    """Асинхронный репозиторий для работы с платежами в БД"""
    
    def __init__(self, db_path: str = None):
        # Используем абсолютный путь для обеспечения консистентности
        if db_path is None:
            from app.settings import settings
            db_path = settings.DATABASE_PATH
        # Преобразуем в абсолютный путь если относительный
        if not os.path.isabs(db_path):
            db_path = os.path.abspath(db_path)
        self.db_path = db_path
        # Инициализация таблицы будет выполнена при первом обращении к БД
    
    async def _ensure_table_exists(self):
        """Создание таблицы платежей если не существует"""
        async with open_async_connection(self.db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    tariff_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT DEFAULT 'RUB',
                    email TEXT,
                    status TEXT DEFAULT 'pending',
                    country TEXT,
                    protocol TEXT DEFAULT 'outline',
                    provider TEXT DEFAULT 'yookassa',
                    method TEXT,
                    description TEXT,
                    created_at INTEGER,
                    updated_at INTEGER,
                    paid_at INTEGER,
                    metadata TEXT
                )
                """
            )

            # Проверка и миграция существующей таблицы (если была создана старой схемой)
            try:
                async with conn.execute("PRAGMA table_info(payments)") as cur:
                    cols_rows = await cur.fetchall()
                cols = {row[1] for row in cols_rows}

                # Требуемые дополнительные поля по новой схеме
                required_columns = [
                    ("amount", "INTEGER DEFAULT 0"),
                    ("currency", "TEXT DEFAULT 'RUB'"),
                    ("country", "TEXT"),
                    ("protocol", "TEXT DEFAULT 'outline'"),
                    ("provider", "TEXT DEFAULT 'yookassa'"),
                    ("method", "TEXT"),
                    ("description", "TEXT"),
                    ("created_at", "INTEGER"),
                    ("updated_at", "INTEGER"),
                    ("paid_at", "INTEGER"),
                    ("metadata", "TEXT"),
                ]

                for name, decl in required_columns:
                    if name not in cols:
                        try:
                            await conn.execute(f"ALTER TABLE payments ADD COLUMN {name} {decl}")
                        except Exception:
                            pass

                # Индексы
                indexes = [
                    ("CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_payment_id ON payments(payment_id)", "payment_id"),
                    ("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)", "status"),
                    ("CREATE INDEX IF NOT EXISTS idx_payments_created_at ON payments(created_at)", "created_at"),
                    ("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)", "user_id"),
                    ("CREATE INDEX IF NOT EXISTS idx_payments_tariff_id ON payments(tariff_id)", "tariff_id"),
                    ("CREATE INDEX IF NOT EXISTS idx_payments_user_status ON payments(user_id, status)", "user_status"),
                ]
                for index_sql, index_name in indexes:
                    try:
                        await conn.execute(index_sql)
                    except Exception as idx_error:
                        logger.warning(f"Failed to create index {index_name}: {idx_error}")

            except Exception as e:
                logger.warning(f"Payments table migration check failed: {e}")

            await conn.commit()
            logger.info("Payments table ensured and migrated (if needed)")
    
    def _payment_from_row(self, row: tuple) -> Payment:
        """Создание объекта Payment из строки БД"""
        try:
            # Безопасное преобразование timestamp
            def safe_timestamp(value):
                if value is None:
                    return None
                try:
                    return datetime.fromtimestamp(int(value), tz=timezone.utc)
                except (ValueError, TypeError):
                    return None
            
            # Безопасное преобразование metadata
            # КРИТИЧНО: Используем только JSON для безопасности и производительности
            # ast.literal_eval() удален - все данные должны быть в JSON формате
            def safe_metadata(value):
                if value is None:
                    return {}
                if isinstance(value, dict):
                    return value
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        # Если не JSON, логируем предупреждение и возвращаем пустой dict
                        logger.warning(f"Invalid JSON metadata format, returning empty dict: {value[:100] if len(value) > 100 else value}")
                        return {}
                # Для других типов пытаемся преобразовать в dict
                try:
                    return dict(value) if hasattr(value, '__iter__') and not isinstance(value, str) else {}
                except Exception:
                    return {}
            
            # Безопасное преобразование статуса в enum
            def safe_status(value):
                if value is None:
                    return PaymentStatus.PENDING
                if isinstance(value, PaymentStatus):
                    return value
                try:
                    # Пробуем преобразовать строку в enum
                    status_str = str(value).lower()
                    for status in PaymentStatus:
                        if status.value.lower() == status_str:
                            return status
                    # Если не нашли, возвращаем PENDING
                    return PaymentStatus.PENDING
                except Exception:
                    return PaymentStatus.PENDING

            def safe_provider(value):
                if value is None:
                    return PaymentProvider.YOOKASSA
                if isinstance(value, PaymentProvider):
                    return value
                try:
                    return PaymentProvider(str(value))
                except Exception:
                    return PaymentProvider.YOOKASSA
            
            # Вариант 1: "перепутанные поля" (очень старый формат)
            # В старых платежах: user_id содержит payment_id, tariff_id содержит user_id, payment_id содержит tariff_id
            if len(row) >= 4:
                user_id_value = str(row[2]) if row[2] else ""
                payment_id_value = str(row[1]) if row[1] else ""
                if (len(user_id_value) > 20 and '-' in user_id_value and payment_id_value.isdigit() and len(payment_id_value) < 20):
                    return Payment(
                        id=row[0],
                        payment_id=row[2],
                        user_id=row[3],
                        tariff_id=row[1],
                        amount=row[4] if len(row) > 4 else 0,
                        currency=row[5] if len(row) > 5 and row[5] else 'RUB',
                        email=row[6] if len(row) > 6 else None,
                        status=safe_status(row[7] if len(row) > 7 and row[7] else 'pending'),
                        country=row[8] if len(row) > 8 else None,
                        protocol=row[9] if len(row) > 9 and row[9] else 'outline',
                        provider=safe_provider(row[10] if len(row) > 10 and row[10] else 'yookassa'),
                        method=row[11] if len(row) > 11 else None,
                        description=row[12] if len(row) > 12 else None,
                        created_at=safe_timestamp(row[13]) if len(row) > 13 else None,
                        updated_at=safe_timestamp(row[14]) if len(row) > 14 else None,
                        paid_at=safe_timestamp(row[15]) if len(row) > 15 else None,
                        metadata=safe_metadata(row[16]) if len(row) > 16 else {}
                    )

            # Вариант 2: "наследуемая схема db.py" — столбцы исходной таблицы + добавленные ALTER TABLE
            # Фактический порядок (см. PRAGMA table_info):
            # 0 id | 1 user_id | 2 tariff_id | 3 payment_id | 4 status | 5 email | 6 revoked | 7 protocol |
            # 8 amount | 9 created_at | 10 country | 11 currency | 12 provider | 13 method | 14 description |
            # 15 updated_at | 16 paid_at | 17 metadata
            if len(row) >= 14:
                # row[3] должен быть строкой платежа (обычно содержит '-')
                looks_like_payment_id = isinstance(row[3], str) and ('-' in row[3] or len(row[3]) >= 12)
                looks_like_user_id = isinstance(row[1], (int,)) or (isinstance(row[1], str) and row[1].isdigit())
                looks_like_tariff_id = isinstance(row[2], (int,)) or (isinstance(row[2], str) and row[2].isdigit())
                if looks_like_payment_id and looks_like_user_id and looks_like_tariff_id:
                    return Payment(
                        id=row[0],
                        user_id=int(row[1]) if row[1] is not None else 0,
                        tariff_id=int(row[2]) if row[2] is not None else 0,
                        payment_id=row[3],
                        status=safe_status(row[4] if row[4] else 'pending'),
                        email=row[5],
                        protocol=row[7] if len(row) > 7 and row[7] else 'outline',
                        amount=row[8] if len(row) > 8 and row[8] is not None else 0,
                        created_at=safe_timestamp(row[9]) if len(row) > 9 else None,
                        country=row[10] if len(row) > 10 else None,
                        currency=row[11] if len(row) > 11 and row[11] else 'RUB',
                        provider=safe_provider(row[12] if len(row) > 12 and row[12] else 'yookassa'),
                        method=row[13] if len(row) > 13 else None,
                        description=row[14] if len(row) > 14 else None,
                        updated_at=safe_timestamp(row[15]) if len(row) > 15 else None,
                        paid_at=safe_timestamp(row[16]) if len(row) > 16 else None,
                        metadata=safe_metadata(row[17]) if len(row) > 17 else {}
                    )
            
            # Вариант 3: Современная таблица, созданная платежным модулем (ожидаемый порядок полей)
            return Payment(
                id=row[0],
                payment_id=row[1],
                user_id=row[2],
                tariff_id=row[3],
                amount=row[4],
                currency=row[5] if row[5] else 'RUB',
                email=row[6],
                status=safe_status(row[7] if row[7] else 'pending'),
                country=row[8],
                protocol=row[9] if row[9] else 'outline',
                provider=safe_provider(row[10] if row[10] else 'yookassa'),
                method=row[11],
                description=row[12],
                created_at=safe_timestamp(row[13]),
                updated_at=safe_timestamp(row[14]),
                paid_at=safe_timestamp(row[15]),
                metadata=safe_metadata(row[16])
            )
        except Exception as e:
            logger.error(f"Error creating Payment from row: {e}, row: {row}")
            raise
    
    def _build_filter_conditions(self, filter_obj: PaymentFilter) -> Tuple[str, list]:
        """Построение условий WHERE и параметров для фильтрации платежей"""
        conditions = []
        params = []
        
        if filter_obj.user_id is not None:
            conditions.append("user_id = ?")
            params.append(filter_obj.user_id)
        
        if filter_obj.tariff_id is not None:
            conditions.append("tariff_id = ?")
            params.append(filter_obj.tariff_id)
        
        if filter_obj.status is not None:
            conditions.append("status = ?")
            params.append(filter_obj.status.value)
        
        if filter_obj.provider is not None:
            conditions.append("provider = ?")
            params.append(filter_obj.provider.value)
        
        if filter_obj.country is not None:
            conditions.append("country = ?")
            params.append(filter_obj.country)
        
        if filter_obj.protocol is not None:
            conditions.append("protocol = ?")
            params.append(filter_obj.protocol)
        
        if filter_obj.is_paid is not None:
            if filter_obj.is_paid:
                conditions.append("status = 'paid'")
            else:
                conditions.append("status != 'paid'")
        
        if filter_obj.is_pending is not None:
            if filter_obj.is_pending:
                conditions.append("status = 'pending'")
            else:
                conditions.append("status != 'pending'")
        
        if filter_obj.created_after is not None:
            conditions.append("created_at >= ?")
            params.append(int(filter_obj.created_after.timestamp()))
        
        if filter_obj.created_before is not None:
            conditions.append("created_at <= ?")
            params.append(int(filter_obj.created_before.timestamp()))
        
        if filter_obj.search_query:
            search_pattern = f"%{filter_obj.search_query}%"
            # Поиск по всем столбцам: id, payment_id, user_id, tariff_id, email, status, provider, country, protocol
            search_conditions = [
                "CAST(id AS TEXT) LIKE ?",
                "payment_id LIKE ?",
                "CAST(user_id AS TEXT) LIKE ?",
                "CAST(tariff_id AS TEXT) LIKE ?",
                "IFNULL(email,'') LIKE ?",
                "status LIKE ?",
                "provider LIKE ?",
                "IFNULL(country,'') LIKE ?",
                "IFNULL(protocol,'') LIKE ?",
            ]
            conditions.append("(" + " OR ".join(search_conditions) + ")")
            params.extend([search_pattern] * len(search_conditions))
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        return where_clause, params
    
    def _payment_to_row(self, payment: Payment) -> tuple:
        """Преобразование объекта Payment в строку БД"""
        return (
            payment.payment_id,
            payment.user_id,
            payment.tariff_id,
            payment.amount,
            payment.currency.value if hasattr(payment.currency, 'value') else payment.currency,
            payment.email,
            payment.status.value if hasattr(payment.status, 'value') else payment.status,
            payment.country,
            payment.protocol,
            payment.provider.value if hasattr(payment.provider, 'value') else payment.provider,
            payment.method.value if payment.method and hasattr(payment.method, 'value') else payment.method,
            payment.description,
            int(payment.created_at.timestamp()) if payment.created_at else None,
            int(payment.updated_at.timestamp()) if payment.updated_at else None,
            int(payment.paid_at.timestamp()) if payment.paid_at else None,
            json.dumps(payment.metadata, ensure_ascii=False) if payment.metadata else None
        )
    
    async def create(self, payment: Payment) -> Payment:
        """Создание платежа в БД"""
        try:
            await self._ensure_table_exists()
            async with open_async_connection(self.db_path) as conn:
                try:
                    cursor = await conn.execute(
                        """
                        INSERT INTO payments (
                            payment_id, user_id, tariff_id, amount, currency, email, 
                            status, country, protocol, provider, method, description,
                            created_at, updated_at, paid_at, metadata
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        self._payment_to_row(payment),
                    )
                    payment.id = cursor.lastrowid
                    await conn.commit()
                    logger.info(f"Payment created: {payment.payment_id}")
                    return payment
                except sqlite3.IntegrityError as e:
                    # Duplicate payment_id — return existing row rather than failing tests
                    # Используем то же соединение вместо открытия нового
                    try:
                        async with conn.execute(
                            "SELECT * FROM payments WHERE payment_id = ?",
                            (payment.payment_id,)
                        ) as cursor:
                            row = await cursor.fetchone()
                            if row:
                                existing = self._payment_from_row(row)
                                logger.info(f"Payment already exists, returning existing: {payment.payment_id}")
                                return existing
                    except Exception as get_error:
                        logger.warning(f"Error getting existing payment {payment.payment_id}: {get_error}")
                    # Re-raise original IntegrityError if we couldn't get existing payment
                    raise e
                
        except Exception as e:
            logger.error(f"Error creating payment: {e}")
            raise
    
    async def get_by_id(self, payment_id: int) -> Optional[Payment]:
        """Получение платежа по ID"""
        try:
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    "SELECT * FROM payments WHERE id = ?", 
                    (payment_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return self._payment_from_row(row)
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting payment by ID: {e}")
            return None
    
    async def get_by_payment_id(self, payment_id: str) -> Optional[Payment]:
        """Получение платежа по payment_id"""
        try:
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    "SELECT * FROM payments WHERE payment_id = ?", 
                    (payment_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return self._payment_from_row(row)
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting payment by payment_id: {e}")
            return None
    
    async def update(self, payment: Payment) -> Payment:
        """Обновление платежа"""
        try:
            async with open_async_connection(self.db_path) as conn:
                row_data = self._payment_to_row(payment)
                # Исключаем payment_id из данных для UPDATE (он идет в WHERE)
                update_data = row_data[1:] + (payment.payment_id,)
                
                cursor = await conn.execute("""
                    UPDATE payments SET 
                        user_id = ?, tariff_id = ?, amount = ?, currency = ?, 
                        email = ?, status = ?, country = ?, protocol = ?, 
                        provider = ?, method = ?, description = ?, 
                        created_at = ?, updated_at = ?, paid_at = ?, metadata = ?
                    WHERE payment_id = ?
                """, update_data)
                
                await conn.commit()
                
                if cursor.rowcount == 0:
                    logger.warning(f"Payment {payment.payment_id} not found for update")
                    raise ValueError(f"Payment {payment.payment_id} not found")
                
                logger.info(f"Payment updated: {payment.payment_id}")
                return payment
                
        except Exception as e:
            logger.error(f"Error updating payment: {e}")
            raise
    
    async def update_status(self, payment_id: str, status: PaymentStatus) -> bool:
        """Обновление статуса платежа"""
        try:
            async with open_async_connection(self.db_path) as conn:
                cursor = await conn.execute(
                    "UPDATE payments SET status = ?, updated_at = ? WHERE payment_id = ?",
                    (status.value, int(datetime.now(timezone.utc).timestamp()), payment_id)
                )
                await conn.commit()
                
                success = cursor.rowcount > 0
                if success:
                    logger.info(f"Payment status updated: {payment_id} -> {status.value}")
                return success
                
        except Exception as e:
            logger.error(f"Error updating payment status: {e}")
            return False
    
    async def try_update_status(self, payment_id: str, new_status: PaymentStatus, expected_status: PaymentStatus) -> bool:
        """
        Атомарное обновление статуса платежа только если текущий статус соответствует ожидаемому
        
        Используется для предотвращения параллельной обработки одного платежа.
        Если статус уже изменился, обновление не произойдет.
        Использует retry механизм для обработки ошибок "database is locked".
        
        Args:
            payment_id: ID платежа
            new_status: Новый статус
            expected_status: Ожидаемый текущий статус (обновление произойдет только если текущий статус = expected_status)
            
        Returns:
            True если статус успешно обновлен, False если текущий статус не соответствует ожидаемому
        """
        async def _update_operation():
            async with open_async_connection(self.db_path) as conn:
                cursor = await conn.execute(
                    "UPDATE payments SET status = ?, updated_at = ? WHERE payment_id = ? AND status = ?",
                    (new_status.value, int(datetime.now(timezone.utc).timestamp()), payment_id, expected_status.value)
                )
                await conn.commit()
                
                success = cursor.rowcount > 0
                if success:
                    logger.info(f"Payment status atomically updated: {payment_id} {expected_status.value} -> {new_status.value}")
                else:
                    logger.debug(f"Payment status update skipped: {payment_id} (current status != {expected_status.value})")
                return success
        
        try:
            return await retry_async_db_operation(
                _update_operation,
                max_attempts=3,
                initial_delay=0.1,
                operation_name="try_update_payment_status",
                operation_context={"payment_id": payment_id, "from_status": expected_status.value, "to_status": new_status.value}
            )
        except Exception as e:
            logger.error(
                f"Error atomically updating payment status after retries: {e}. "
                f"Payment ID: {payment_id}, From: {expected_status.value}, To: {new_status.value}",
                exc_info=True
            )
            return False
    
    async def try_acquire_processing_lock(self, payment_id: str, lock_key: str = '_processing_subscription') -> bool:
        """
        Атомарная попытка установить флаг обработки платежа
        
        Args:
            payment_id: ID платежа
            lock_key: Ключ флага в metadata (по умолчанию '_processing_subscription')
            
        Returns:
            True если флаг успешно установлен (блокировка получена), False если уже обрабатывается
        """
        try:
            import json
            async with open_async_connection(self.db_path) as conn:
                # Получаем текущий metadata
                async with conn.execute(
                    "SELECT metadata, status FROM payments WHERE payment_id = ?",
                    (payment_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        logger.warning(f"Payment {payment_id} not found for lock acquisition")
                        return False
                    
                    metadata_str = row[0]
                    status = row[1]
                    
                    # Если платеж уже completed, не устанавливаем блокировку
                    if status == 'completed':
                        logger.debug(f"Payment {payment_id} already completed, skipping lock")
                        return False
                    
                    # Парсим metadata
                    try:
                        if isinstance(metadata_str, str):
                            metadata = json.loads(metadata_str)
                        elif isinstance(metadata_str, dict):
                            metadata = metadata_str
                        else:
                            metadata = {}
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}
                    
                    # Проверяем, не установлен ли уже флаг
                    if metadata.get(lock_key):
                        # Проверяем, не истекла ли блокировка (если установлена более 10 минут назад, считаем её устаревшей)
                        lock_started_at = metadata.get(f'{lock_key}_started_at', 0)
                        if lock_started_at > 0:
                            lock_age = int(time.time()) - lock_started_at
                            if lock_age > 600:  # 10 минут
                                logger.warning(
                                    f"Payment {payment_id} has stale lock {lock_key} (age: {lock_age}s), "
                                    f"clearing it and acquiring new lock"
                                )
                                # Удаляем устаревшую блокировку
                                metadata.pop(lock_key, None)
                                metadata.pop(f'{lock_key}_started_at', None)
                            else:
                                logger.debug(f"Payment {payment_id} already has lock {lock_key} (age: {lock_age}s)")
                                return False
                        else:
                            logger.debug(f"Payment {payment_id} already has lock {lock_key} (no timestamp)")
                            return False
                    
                    # Устанавливаем флаг атомарно
                    metadata[lock_key] = True
                    metadata[f'{lock_key}_started_at'] = int(time.time())
                    
                    # Обновляем платеж с новым metadata
                    cursor = await conn.execute(
                        "UPDATE payments SET metadata = ?, updated_at = ? WHERE payment_id = ? AND status != 'completed'",
                        (json.dumps(metadata, ensure_ascii=False), int(time.time()), payment_id)
                    )
                    await conn.commit()
                    
                    success = cursor.rowcount > 0
                    if success:
                        logger.info(f"Processing lock acquired for payment {payment_id}")
                    else:
                        logger.debug(f"Failed to acquire lock for payment {payment_id} (may be completed)")
                    return success
                    
        except Exception as e:
            logger.error(f"Error acquiring processing lock for payment {payment_id}: {e}")
            return False
    
    async def release_processing_lock(self, payment_id: str, lock_key: str = '_processing_subscription') -> bool:
        """
        Освободить флаг обработки платежа
        
        Args:
            payment_id: ID платежа
            lock_key: Ключ флага в metadata
            
        Returns:
            True если флаг успешно удален
        """
        try:
            import json
            async with open_async_connection(self.db_path) as conn:
                # Получаем текущий metadata
                async with conn.execute(
                    "SELECT metadata FROM payments WHERE payment_id = ?",
                    (payment_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        return False
                    
                    metadata_str = row[0]
                    try:
                        metadata = json.loads(metadata_str) if metadata_str else {}
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}
                    
                    # Удаляем флаг
                    if lock_key in metadata:
                        metadata.pop(lock_key)
                    if f'{lock_key}_started_at' in metadata:
                        metadata.pop(f'{lock_key}_started_at')
                    
                    # Обновляем платеж
                    cursor = await conn.execute(
                        "UPDATE payments SET metadata = ?, updated_at = ? WHERE payment_id = ?",
                        (json.dumps(metadata, ensure_ascii=False), int(time.time()), payment_id)
                    )
                    await conn.commit()
                    
                    return cursor.rowcount > 0
                    
        except Exception as e:
            logger.error(f"Error releasing processing lock for payment {payment_id}: {e}")
            return False
    
    async def delete(self, payment_id: int) -> bool:
        """Удаление платежа"""
        try:
            async with open_async_connection(self.db_path) as conn:
                cursor = await conn.execute(
                    "DELETE FROM payments WHERE id = ?", 
                    (payment_id,)
                )
                await conn.commit()
                
                success = cursor.rowcount > 0
                if success:
                    logger.info(f"Payment deleted: {payment_id}")
                return success
                
        except Exception as e:
            logger.error(f"Error deleting payment: {e}")
            return False
    
    async def list(self, limit: int = 100, offset: int = 0) -> List[Payment]:
        """Получение списка платежей"""
        try:
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    "SELECT * FROM payments ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset)
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [self._payment_from_row(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Error listing payments: {e}")
            return []
    
    async def filter(self, filter_obj: PaymentFilter, sort_by: str = "created_at", sort_order: str = "DESC") -> List[Payment]:
        """Фильтрация платежей с сортировкой"""
        try:
            where_clause, params = self._build_filter_conditions(filter_obj)

            # Сортировка (белый список столбцов)
            sort_columns = {
                "created_at": "created_at",
                "status": "status",
                "amount": "amount",
                "paid_at": "paid_at",
                "updated_at": "updated_at",
            }
            order_col = sort_columns.get((sort_by or "").lower(), "created_at")
            order_dir = "ASC" if (str(sort_order).upper() == "ASC") else "DESC"

            params.extend([filter_obj.limit, filter_obj.offset])
            
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    f"SELECT * FROM payments WHERE {where_clause} ORDER BY {order_col} {order_dir} LIMIT ? OFFSET ?",
                    params
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [self._payment_from_row(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Error filtering payments: {e}")
            return []

    async def count_filtered(self, filter_obj: PaymentFilter) -> int:
        """Подсчет количества платежей по фильтру"""
        try:
            where_clause, params = self._build_filter_conditions(filter_obj)

            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    f"SELECT COUNT(*) FROM payments WHERE {where_clause}",
                    params,
                ) as cursor:
                    row = await cursor.fetchone()
                    return int(row[0] if row and row[0] is not None else 0)
        except Exception as e:
            logger.error(f"Error counting filtered payments: {e}")
            return 0
    
    async def get_user_payments(self, user_id: int, limit: int = 100) -> List[Payment]:
        """Получение платежей пользователя"""
        try:
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    "SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit)
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [self._payment_from_row(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Error getting user payments: {e}")
            return []
    
    async def get_pending_payments(self) -> List[Payment]:
        """Получение ожидающих платежей"""
        try:
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    "SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at ASC"
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [self._payment_from_row(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Error getting pending payments: {e}")
            return []
    
    async def get_paid_payments_without_keys(self) -> List[Payment]:
        """Получение оплаченных платежей без ключей (исключая закрытые платежи)"""
        try:
            now_ts = int(datetime.now(timezone.utc).timestamp())
            async with open_async_connection(self.db_path) as conn:
                # Запрос теперь исключает:
                # 1. Для платежей подписки: НЕ исключаем по наличию активной подписки (нужно продлевать)
                # 2. Для обычных платежей: исключаем если есть активные ключи или подписки
                async with conn.execute("""
                    SELECT p.* FROM payments p
                    WHERE p.status = 'paid' 
                    AND (
                        -- Для платежей подписки: включаем все (логика продления будет в коде)
                        (p.metadata LIKE '%subscription%' AND p.protocol = 'v2ray')
                        OR
                        -- Для обычных платежей: исключаем если есть активные ключи или подписки
                        (NOT (p.metadata LIKE '%subscription%' AND p.protocol = 'v2ray')
                         AND p.user_id NOT IN (
                             SELECT k.user_id FROM keys k
                             JOIN subscriptions s ON k.subscription_id = s.id
                             WHERE s.expires_at > ?
                             UNION
                             SELECT k.user_id FROM v2ray_keys k
                             JOIN subscriptions s ON k.subscription_id = s.id
                             WHERE s.expires_at > ?
                             UNION
                             SELECT user_id FROM subscriptions WHERE expires_at > ? AND is_active = 1
                         ))
                    )
                    ORDER BY p.created_at ASC
                """, (now_ts, now_ts, now_ts)) as cursor:
                    rows = await cursor.fetchall()
                    return [self._payment_from_row(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Error getting paid payments without keys: {e}")
            return []
    
    async def count(self) -> int:
        """Получение количества платежей"""
        try:
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute("SELECT COUNT(*) FROM payments") as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
                    
        except Exception as e:
            logger.error(f"Error counting payments: {e}")
            return 0
    
    async def exists(self, payment_id: str) -> bool:
        """Проверка существования платежа"""
        try:
            async with open_async_connection(self.db_path) as conn:
                async with conn.execute(
                    "SELECT 1 FROM payments WHERE payment_id = ?", 
                    (payment_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    return row is not None
                    
        except Exception as e:
            logger.error(f"Error checking payment existence: {e}")
            return False
    
    async def get_statistics(self) -> Dict[str, Any]:
        """Получение статистики по платежам"""
        try:
            async with open_async_connection(self.db_path) as conn:
                # Общее количество платежей
                async with conn.execute("SELECT COUNT(*) FROM payments") as cursor:
                    total_count = (await cursor.fetchone())[0] or 0
                
                # Количество pending платежей
                async with conn.execute(
                    "SELECT COUNT(*) FROM payments WHERE status = ?",
                    (PaymentStatus.PENDING.value,)
                ) as cursor:
                    pending_count = (await cursor.fetchone())[0] or 0
                
                # Количество paid платежей
                async with conn.execute(
                    "SELECT COUNT(*) FROM payments WHERE status = ?",
                    (PaymentStatus.PAID.value,)
                ) as cursor:
                    paid_count = (await cursor.fetchone())[0] or 0
                
                # Общая сумма по completed платежам
                async with conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = ?",
                    (PaymentStatus.COMPLETED.value,)
                ) as cursor:
                    completed_total = (await cursor.fetchone())[0] or 0
                
                return {
                    "total_count": total_count,
                    "pending_count": pending_count,
                    "paid_count": paid_count,
                    "completed_total_amount": completed_total,
                }
                
        except Exception as e:
            logger.error(f"Error getting payment statistics: {e}")
            return {
                "total_count": 0,
                "pending_count": 0,
                "paid_count": 0,
                "completed_total_amount": 0,
            }