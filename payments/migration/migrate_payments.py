"""
Скрипт миграции платежных данных из старой структуры в новую

Этот скрипт переносит существующие платежи из старой таблицы в новую структуру.
"""

import sqlite3
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any

from ..models.payment import Payment, PaymentStatus
from ..models.enums import PaymentProvider, PaymentCurrency
from ..repositories.payment_repository import PaymentRepository

logger = logging.getLogger(__name__)


class PaymentMigration:
    """Класс для миграции платежных данных"""
    
    def __init__(self, old_db_path: str, new_db_path: str):
        self.old_db_path = old_db_path
        self.new_db_path = new_db_path
        self.payment_repo = PaymentRepository(new_db_path)
    
    def get_old_payments(self) -> List[Dict[str, Any]]:
        """Получение платежей из старой БД"""
        try:
            conn = sqlite3.connect(self.old_db_path)
            cursor = conn.cursor()
            
            # Получаем все платежи из старой таблицы
            cursor.execute("""
                SELECT id, payment_id, user_id, tariff_id, amount, currency, 
                       email, status, country, protocol, provider, method, 
                       description, created_at, updated_at, paid_at, metadata
                FROM payments
                ORDER BY created_at ASC
            """)
            
            rows = cursor.fetchall()
            payments = []
            
            for row in rows:
                payment = {
                    'id': row[0],
                    'payment_id': row[1],
                    'user_id': row[2],
                    'tariff_id': row[3],
                    'amount': row[4],
                    'currency': row[5] or 'RUB',
                    'email': row[6],
                    'status': row[7] or 'pending',
                    'country': row[8],
                    'protocol': row[9] or 'outline',
                    'provider': row[10] or 'yookassa',
                    'method': row[11],
                    'description': row[12],
                    'created_at': row[13],
                    'updated_at': row[14],
                    'paid_at': row[15],
                    'metadata': row[16]
                }
                payments.append(payment)
            
            conn.close()
            logger.info(f"Found {len(payments)} payments in old database")
            return payments
            
        except Exception as e:
            logger.error(f"Error reading old payments: {e}")
            return []
    
    def convert_payment_status(self, old_status: str) -> PaymentStatus:
        """Конвертация статуса платежа"""
        status_mapping = {
            'pending': PaymentStatus.PENDING,
            'paid': PaymentStatus.PAID,
            'failed': PaymentStatus.FAILED,
            'cancelled': PaymentStatus.CANCELLED,
            'refunded': PaymentStatus.REFUNDED,
            'expired': PaymentStatus.EXPIRED
        }
        return status_mapping.get(old_status.lower(), PaymentStatus.PENDING)
    
    def convert_provider(self, old_provider: str) -> PaymentProvider:
        """Конвертация провайдера платежей"""
        provider_mapping = {
            'yookassa': PaymentProvider.YOOKASSA,
            'stripe': PaymentProvider.STRIPE,
            'paypal': PaymentProvider.PAYPAL
        }
        return provider_mapping.get(old_provider.lower(), PaymentProvider.YOOKASSA)
    
    def convert_currency(self, old_currency: str) -> PaymentCurrency:
        """Конвертация валюты"""
        currency_mapping = {
            'RUB': PaymentCurrency.RUB,
            'USD': PaymentCurrency.USD,
            'EUR': PaymentCurrency.EUR
        }
        return currency_mapping.get(old_currency.upper(), PaymentCurrency.RUB)
    
    def parse_metadata(self, metadata_str: str) -> Dict[str, Any]:
        """Парсинг метаданных без выполнения кода.

        Порядок: JSON -> literal_eval (безопасный) -> raw string.
        """
        if not metadata_str:
            return {}

        # Сначала пробуем JSON
        try:
            import json
            return json.loads(metadata_str)
        except Exception:
            pass

        # Затем безопасный literal_eval
        try:
            import ast
            value = ast.literal_eval(metadata_str)
            if isinstance(value, dict):
                return value
            return {"value": value}
        except Exception:
            # Возвращаем как строку
            return {"raw_metadata": metadata_str}
    
    def convert_timestamp(self, timestamp: int) -> datetime:
        """Конвертация временной метки"""
        if timestamp:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return datetime.now(timezone.utc)
    
    def create_new_payment(self, old_payment: Dict[str, Any]) -> Payment:
        """Создание нового объекта Payment из старого"""
        try:
            # Конвертируем метаданные
            metadata = self.parse_metadata(old_payment.get('metadata', ''))
            
            # Создаем новый объект Payment
            payment = Payment(
                payment_id=old_payment['payment_id'],
                user_id=old_payment['user_id'],
                tariff_id=old_payment['tariff_id'],
                amount=old_payment['amount'],
                currency=self.convert_currency(old_payment['currency']),
                email=old_payment['email'],
                status=self.convert_payment_status(old_payment['status']),
                country=old_payment['country'],
                protocol=old_payment['protocol'],
                provider=self.convert_provider(old_payment['provider']),
                method=old_payment['method'],
                description=old_payment['description'],
                created_at=self.convert_timestamp(old_payment['created_at']),
                updated_at=self.convert_timestamp(old_payment['updated_at']),
                paid_at=self.convert_timestamp(old_payment['paid_at']),
                metadata=metadata
            )
            
            return payment
            
        except Exception as e:
            logger.error(f"Error converting payment {old_payment.get('payment_id', 'unknown')}: {e}")
            raise
    
    async def migrate_payments(self, dry_run: bool = True) -> Dict[str, int]:
        """Миграция платежей"""
        try:
            # Получаем старые платежи
            old_payments = self.get_old_payments()
            
            if not old_payments:
                logger.warning("No payments found to migrate")
                return {'total': 0, 'success': 0, 'failed': 0}
            
            # Статистика миграции
            stats = {
                'total': len(old_payments),
                'success': 0,
                'failed': 0
            }
            
            logger.info(f"Starting migration of {len(old_payments)} payments (dry_run={dry_run})")
            
            for old_payment in old_payments:
                try:
                    # Конвертируем платеж
                    new_payment = self.create_new_payment(old_payment)
                    
                    if not dry_run:
                        # Сохраняем в новую БД
                        await self.payment_repo.create(new_payment)
                        logger.info(f"Migrated payment: {new_payment.payment_id}")
                    else:
                        logger.info(f"Would migrate payment: {new_payment.payment_id}")
                    
                    stats['success'] += 1
                    
                except Exception as e:
                    logger.error(f"Failed to migrate payment {old_payment.get('payment_id', 'unknown')}: {e}")
                    stats['failed'] += 1
            
            logger.info(f"Migration completed: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"Error during migration: {e}")
            return {'total': 0, 'success': 0, 'failed': 1}
    
    async def validate_migration(self) -> Dict[str, Any]:
        """Валидация миграции"""
        try:
            # Получаем старые платежи
            old_payments = self.get_old_payments()
            
            # Получаем новые платежи
            new_payments = await self.payment_repo.list(limit=1000)
            
            # Сравниваем количество
            old_count = len(old_payments)
            new_count = len(new_payments)
            
            # Проверяем уникальность payment_id
            old_payment_ids = {p['payment_id'] for p in old_payments if p['payment_id']}
            new_payment_ids = {p.payment_id for p in new_payments if p.payment_id}
            
            # Находим дубликаты
            duplicates = old_payment_ids & new_payment_ids
            
            validation_result = {
                'old_count': old_count,
                'new_count': new_count,
                'duplicates': len(duplicates),
                'missing': old_count - new_count,
                'is_valid': old_count == new_count and len(duplicates) == 0
            }
            
            logger.info(f"Migration validation: {validation_result}")
            return validation_result
            
        except Exception as e:
            logger.error(f"Error validating migration: {e}")
            return {'is_valid': False, 'error': str(e)}


async def run_migration(
    old_db_path: str = "vpn.db",
    new_db_path: str = "vpn_new.db",
    dry_run: bool = True
):
    """Запуск миграции"""
    try:
        logger.info("Starting payment migration...")
        
        # Создаем экземпляр миграции
        migration = PaymentMigration(old_db_path, new_db_path)
        
        # Запускаем миграцию
        stats = await migration.migrate_payments(dry_run=dry_run)
        
        if not dry_run:
            # Валидируем миграцию
            validation = await migration.validate_migration()
            
            if validation['is_valid']:
                logger.info("✅ Migration completed successfully!")
            else:
                logger.warning("⚠️ Migration completed with issues")
                logger.warning(f"Validation result: {validation}")
        else:
            logger.info("✅ Dry run completed successfully!")
        
        return stats
        
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        return {'total': 0, 'success': 0, 'failed': 1}


if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Запуск миграции
    asyncio.run(run_migration(dry_run=True))
