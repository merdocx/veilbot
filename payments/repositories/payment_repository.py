import aiosqlite
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from ..models.payment import Payment, PaymentStatus, PaymentFilter

logger = logging.getLogger(__name__)


class PaymentRepository:
    """Асинхронный репозиторий для работы с платежами в БД"""
    
    def __init__(self, db_path: str = "vpn.db"):
        self.db_path = db_path
        # Инициализация таблицы будет выполнена при первом обращении к БД
    
    async def _ensure_table_exists(self):
        """Создание таблицы платежей если не существует"""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("""
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
            """)
            await conn.commit()
            logger.info("Payments table ensured")
    
    def _payment_from_row(self, row: tuple) -> Payment:
        """Создание объекта Payment из строки БД"""
        try:
            # Безопасное преобразование timestamp
            def safe_timestamp(value):
                if value is None:
                    return None
                try:
                    return datetime.fromtimestamp(int(value))
                except (ValueError, TypeError):
                    return None
            
            # Безопасное преобразование metadata
            def safe_metadata(value):
                if value is None:
                    return {}
                try:
                    return eval(value) if value else {}
                except:
                    return {}
            
            # Проверяем, является ли это старым платежом (где данные перепутаны)
            # В старых платежах: user_id содержит payment_id, tariff_id содержит user_id, payment_id содержит tariff_id
            if len(row) >= 4:
                # Проверяем, является ли user_id (row[2]) похожим на payment_id (UUID)
                user_id_value = str(row[2]) if row[2] else ""
                payment_id_value = str(row[1]) if row[1] else ""
                
                # Если user_id похож на UUID, а payment_id похож на число, то это старый формат
                if (len(user_id_value) > 20 and '-' in user_id_value and 
                    payment_id_value.isdigit() and len(payment_id_value) < 20):
                    # Это старый формат, исправляем
                    return Payment(
                        id=row[0],
                        payment_id=row[2],  # user_id содержит payment_id
                        user_id=row[3],    # tariff_id содержит user_id
                        tariff_id=row[1],  # payment_id содержит tariff_id
                        amount=row[4] if len(row) > 4 else 0,
                        currency=row[5] if len(row) > 5 and row[5] else 'RUB',
                        email=row[6] if len(row) > 6 else None,
                        status=row[7] if len(row) > 7 and row[7] else 'pending',
                        country=row[8] if len(row) > 8 else None,
                        protocol=row[9] if len(row) > 9 and row[9] else 'outline',
                        provider=row[10] if len(row) > 10 and row[10] else 'yookassa',
                        method=row[11] if len(row) > 11 else None,
                        description=row[12] if len(row) > 12 else None,
                        created_at=safe_timestamp(row[13]) if len(row) > 13 else None,
                        updated_at=safe_timestamp(row[14]) if len(row) > 14 else None,
                        paid_at=safe_timestamp(row[15]) if len(row) > 15 else None,
                        metadata=safe_metadata(row[16]) if len(row) > 16 else {}
                    )
            
            # Новый формат
            return Payment(
                id=row[0],
                payment_id=row[1],
                user_id=row[2],
                tariff_id=row[3],
                amount=row[4],
                currency=row[5] if row[5] else 'RUB',
                email=row[6],
                status=row[7] if row[7] else 'pending',
                country=row[8],
                protocol=row[9] if row[9] else 'outline',
                provider=row[10] if row[10] else 'yookassa',
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
            str(payment.metadata) if payment.metadata else None
        )
    
    async def create(self, payment: Payment) -> Payment:
        """Создание платежа в БД"""
        try:
            await self._ensure_table_exists()
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute("""
                    INSERT INTO payments (
                        payment_id, user_id, tariff_id, amount, currency, email, 
                        status, country, protocol, provider, method, description,
                        created_at, updated_at, paid_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, self._payment_to_row(payment))
                
                payment.id = cursor.lastrowid
                await conn.commit()
                
                logger.info(f"Payment created: {payment.payment_id}")
                return payment
                
        except Exception as e:
            logger.error(f"Error creating payment: {e}")
            raise
    
    async def get_by_id(self, payment_id: int) -> Optional[Payment]:
        """Получение платежа по ID"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
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
            async with aiosqlite.connect(self.db_path) as conn:
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
            async with aiosqlite.connect(self.db_path) as conn:
                row_data = self._payment_to_row(payment)
                # Исключаем payment_id из данных для UPDATE (он идет в WHERE)
                update_data = row_data[1:] + (payment.payment_id,)
                
                await conn.execute("""
                    UPDATE payments SET 
                        user_id = ?, tariff_id = ?, amount = ?, currency = ?, 
                        email = ?, status = ?, country = ?, protocol = ?, 
                        provider = ?, method = ?, description = ?, 
                        created_at = ?, updated_at = ?, paid_at = ?, metadata = ?
                    WHERE payment_id = ?
                """, update_data)
                
                await conn.commit()
                
                logger.info(f"Payment updated: {payment.payment_id}")
                return payment
                
        except Exception as e:
            logger.error(f"Error updating payment: {e}")
            raise
    
    async def update_status(self, payment_id: str, status: PaymentStatus) -> bool:
        """Обновление статуса платежа"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute(
                    "UPDATE payments SET status = ?, updated_at = ? WHERE payment_id = ?",
                    (status.value, int(datetime.utcnow().timestamp()), payment_id)
                )
                await conn.commit()
                
                success = cursor.rowcount > 0
                if success:
                    logger.info(f"Payment status updated: {payment_id} -> {status.value}")
                return success
                
        except Exception as e:
            logger.error(f"Error updating payment status: {e}")
            return False
    
    async def delete(self, payment_id: int) -> bool:
        """Удаление платежа"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
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
            async with aiosqlite.connect(self.db_path) as conn:
                async with conn.execute(
                    "SELECT * FROM payments ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset)
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [self._payment_from_row(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Error listing payments: {e}")
            return []
    
    async def filter(self, filter_obj: PaymentFilter) -> List[Payment]:
        """Фильтрация платежей"""
        try:
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
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            params.extend([filter_obj.limit, filter_obj.offset])
            
            async with aiosqlite.connect(self.db_path) as conn:
                async with conn.execute(
                    f"SELECT * FROM payments WHERE {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    params
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [self._payment_from_row(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Error filtering payments: {e}")
            return []
    
    async def get_user_payments(self, user_id: int, limit: int = 100) -> List[Payment]:
        """Получение платежей пользователя"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
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
            async with aiosqlite.connect(self.db_path) as conn:
                async with conn.execute(
                    "SELECT * FROM payments WHERE status = 'pending' ORDER BY created_at ASC"
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [self._payment_from_row(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Error getting pending payments: {e}")
            return []
    
    async def get_paid_payments_without_keys(self) -> List[Payment]:
        """Получение оплаченных платежей без ключей"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                async with conn.execute("""
                    SELECT p.* FROM payments p
                    WHERE p.status = 'paid' 
                    AND p.user_id NOT IN (
                        SELECT user_id FROM keys WHERE expiry_at > ?
                    )
                    ORDER BY p.created_at ASC
                """, (int(datetime.utcnow().timestamp()),)) as cursor:
                    rows = await cursor.fetchall()
                    return [self._payment_from_row(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Error getting paid payments without keys: {e}")
            return []
    
    async def count(self) -> int:
        """Получение количества платежей"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                async with conn.execute("SELECT COUNT(*) FROM payments") as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
                    
        except Exception as e:
            logger.error(f"Error counting payments: {e}")
            return 0
    
    async def exists(self, payment_id: str) -> bool:
        """Проверка существования платежа"""
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                async with conn.execute(
                    "SELECT 1 FROM payments WHERE payment_id = ?", 
                    (payment_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    return row is not None
                    
        except Exception as e:
            logger.error(f"Error checking payment existence: {e}")
            return False
