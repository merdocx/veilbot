#!/usr/bin/env python3
"""
Скрипт для связывания активных подписок с платежами
Обновляет subscription_id в платежах на основе временного окна и tariff_id
"""
import sys
import os
import asyncio
from typing import List, Tuple, Optional

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import settings
from app.infra.sqlite_utils import open_connection
from payments.repositories.payment_repository import PaymentRepository


def get_active_subscriptions(db_path: str) -> List[Tuple]:
    """Получить все активные подписки"""
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT s.id, s.user_id, s.created_at, s.expires_at, s.tariff_id,
                   t.name as tariff_name
            FROM subscriptions s
            LEFT JOIN tariffs t ON s.tariff_id = t.id
            WHERE s.is_active = 1
            ORDER BY s.user_id, s.created_at
        ''')
        return cursor.fetchall()


def get_payments_for_subscription_fallback(
    db_path: str,
    subscription_id: int,
    user_id: int,
    tariff_id: Optional[int],
    sub_created_at: int,
    sub_expires_at: int
) -> List[Tuple]:
    """Получить платежи для подписки (fallback для платежей без subscription_id)"""
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Сначала проверяем, есть ли уже платежи с subscription_id
        cursor.execute('''
            SELECT id, payment_id, created_at, status, amount, subscription_id, tariff_id
            FROM payments
            WHERE subscription_id = ?
              AND status = 'completed'
              AND protocol = 'v2ray'
              AND metadata LIKE '%subscription%'
            ORDER BY created_at ASC
        ''', (subscription_id,))
        
        payments_with_sub_id = cursor.fetchall()
        
        # Если есть платежи с subscription_id, возвращаем их
        if payments_with_sub_id:
            return payments_with_sub_id
        
        # Ищем платежи без subscription_id, которые могут относиться к этой подписке
        # Используем временное окно ±7 дней от created_at подписки
        GRACE_WINDOW = 7 * 24 * 3600  # 7 дней
        window_start = sub_created_at - GRACE_WINDOW
        window_end = sub_expires_at + GRACE_WINDOW
        
        cursor.execute('''
            SELECT id, payment_id, created_at, status, amount, subscription_id, tariff_id
            FROM payments
            WHERE user_id = ?
              AND tariff_id = ?
              AND status = 'completed'
              AND protocol = 'v2ray'
              AND metadata LIKE '%subscription%'
              AND (subscription_id IS NULL OR subscription_id = 0)
              AND created_at >= ?
              AND created_at <= ?
            ORDER BY created_at ASC
        ''', (user_id, tariff_id or 4, window_start, window_end))
        
        return cursor.fetchall()


async def link_subscription_to_payments(db_path: str, dry_run: bool = True) -> dict:
    """Связать подписки с платежами"""
    subscriptions = get_active_subscriptions(db_path)
    payment_repo = PaymentRepository(db_path)
    
    results = {
        'total_subscriptions': len(subscriptions),
        'linked_subscriptions': 0,
        'linked_payments': 0,
        'skipped_subscriptions': 0,
        'skipped_payments': 0,
        'errors': []
    }
    
    for sub_id, user_id, sub_created_at, sub_expires_at, tariff_id, tariff_name in subscriptions:
        try:
            # Получаем платежи для этой подписки
            payments = get_payments_for_subscription_fallback(
                db_path, sub_id, user_id, tariff_id, sub_created_at, sub_expires_at
            )
            
            if not payments:
                results['skipped_subscriptions'] += 1
                continue
            
            # Проверяем, какие платежи нужно обновить
            payments_to_link = [p for p in payments if p[5] is None or p[5] == 0]
            
            if not payments_to_link:
                # Все платежи уже связаны
                results['skipped_subscriptions'] += 1
                continue
            
            results['linked_subscriptions'] += 1
            
            # Обновляем subscription_id для каждого платежа
            for payment in payments_to_link:
                payment_id = payment[1]  # payment_id из платежа
                
                try:
                    if not dry_run:
                        success = await payment_repo.update_subscription_id(payment_id, sub_id)
                        if success:
                            results['linked_payments'] += 1
                        else:
                            results['errors'].append(f"Failed to link payment {payment_id} to subscription {sub_id}")
                            results['skipped_payments'] += 1
                    else:
                        # В режиме dry_run просто считаем
                        results['linked_payments'] += 1
                        
                except Exception as e:
                    error_msg = f"Error linking payment {payment_id} to subscription {sub_id}: {e}"
                    results['errors'].append(error_msg)
                    results['skipped_payments'] += 1
                    
        except Exception as e:
            error_msg = f"Error processing subscription {sub_id}: {e}"
            results['errors'].append(error_msg)
            results['skipped_subscriptions'] += 1
    
    return results


def main():
    """Главная функция"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Связать активные подписки с платежами')
    parser.add_argument('--apply', action='store_true', help='Применить изменения (по умолчанию dry-run)')
    args = parser.parse_args()
    
    db_path = settings.DATABASE_PATH
    dry_run = not args.apply
    
    if not os.path.exists(db_path):
        print(f"❌ База данных не найдена: {db_path}")
        return 1
    
    print("=" * 80)
    if dry_run:
        print("РЕЖИМ DRY-RUN (изменения не будут применены)")
    else:
        print("РЕЖИМ ПРИМЕНЕНИЯ ИЗМЕНЕНИЙ")
    print("=" * 80)
    print()
    print(f"📂 База данных: {db_path}")
    print()
    
    try:
        results = asyncio.run(link_subscription_to_payments(db_path, dry_run=dry_run))
        
        print("📊 Результаты:")
        print(f"   Всего активных подписок: {results['total_subscriptions']}")
        print(f"   Подписок с платежами для связывания: {results['linked_subscriptions']}")
        print(f"   Платежей связано: {results['linked_payments']}")
        print(f"   Подписок пропущено: {results['skipped_subscriptions']}")
        print(f"   Платежей пропущено: {results['skipped_payments']}")
        print(f"   Ошибок: {len(results['errors'])}")
        print()
        
        if results['errors']:
            print("⚠️  Ошибки:")
            for error in results['errors'][:10]:  # Показываем первые 10 ошибок
                print(f"   - {error}")
            if len(results['errors']) > 10:
                print(f"   ... и еще {len(results['errors']) - 10} ошибок")
            print()
        
        if dry_run:
            print("💡 Для применения изменений запустите скрипт с флагом --apply")
        else:
            print("✅ Изменения применены успешно!")
        
        print()
        return 0
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
