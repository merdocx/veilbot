#!/usr/bin/env python3
"""
Скрипт для ручной обработки платежа и выдачи подписки/ключей
"""
import sys
import os
import asyncio
import time
from datetime import datetime

_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root_dir)

from app.infra.sqlite_utils import open_connection
from app.settings import settings
from payments.repositories.payment_repository import PaymentRepository
from payments.services.subscription_purchase_service import SubscriptionPurchaseService
from payments.services.payment_processor import PaymentProcessor
from payments.models.enums import PaymentStatus


async def process_payment(payment_id: str):
    """Обработать платеж и выдать подписку/ключи"""
    db_path = settings.DATABASE_PATH
    payment_repo = PaymentRepository(db_path)
    
    print("=" * 80)
    print(f"ОБРАБОТКА ПЛАТЕЖА {payment_id}")
    print("=" * 80)
    print()
    
    # 1. Получаем информацию о платеже
    print("1️⃣ ПОЛУЧЕНИЕ ИНФОРМАЦИИ О ПЛАТЕЖЕ")
    print("-" * 80)
    payment = await payment_repo.get_by_payment_id(payment_id)
    
    if not payment:
        print(f"❌ Платеж {payment_id} не найден в базе данных")
        return False
    
    # Конвертируем копейки в рубли
    amount_rub = payment.amount / 100.0
    
    print(f"  Payment ID:      {payment.payment_id}")
    print(f"  User ID:         {payment.user_id}")
    print(f"  Email:           {payment.email or 'N/A'}")
    print(f"  Сумма:           {amount_rub:.2f} {payment.currency}")
    print(f"  Провайдер:       {payment.provider}")
    print(f"  Тариф ID:        {payment.tariff_id}")
    print(f"  Протокол:        {payment.protocol}")
    print(f"  Страна:          {payment.country or 'N/A'}")
    print(f"  Статус:          {payment.status.value}")
    if payment.paid_at:
        if isinstance(payment.paid_at, datetime):
            print(f"  Оплачен:         {payment.paid_at}")
        else:
            print(f"  Оплачен:         {datetime.fromtimestamp(payment.paid_at)}")
    if payment.created_at:
        if isinstance(payment.created_at, datetime):
            print(f"  Создан:          {payment.created_at}")
        else:
            print(f"  Создан:          {datetime.fromtimestamp(payment.created_at)}")
    else:
        print(f"  Создан:          N/A")
    if payment.metadata:
        print(f"  Metadata:        {payment.metadata}")
    print()
    
    # 2. Проверяем текущий статус
    print("2️⃣ ПРОВЕРКА СТАТУСА")
    print("-" * 80)
    if payment.status == PaymentStatus.COMPLETED:
        print("  ⚠️  Платеж уже обработан (статус: completed)")
        print("  Подписка и ключи уже должны быть выданы")
        
        # Проверяем наличие подписки
        with open_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, subscription_token, expires_at, is_active
                FROM subscriptions
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (payment.user_id,))
            sub = cursor.fetchone()
            if sub:
                print(f"  ✓ Подписка найдена: ID={sub[0]}, токен={sub[1][:30]}...")
                print(f"    Истекает: {datetime.fromtimestamp(sub[2]) if sub[2] else 'N/A'}")
                print(f"    Активна: {'Да' if sub[3] else 'Нет'}")
            else:
                print("  ⚠️  Подписка не найдена, возможно она была удалена")
        
        return True
    
    # 3. Обновляем статус на PAID если нужно
    print("3️⃣ ОБНОВЛЕНИЕ СТАТУСА НА PAID")
    print("-" * 80)
    if payment.status != PaymentStatus.PAID:
        now_ts = int(time.time())
        try:
            # Обновляем статус через репозиторий
            payment.mark_as_paid()
            payment.paid_at = now_ts
            await payment_repo.update(payment)
            print(f"  ✓ Статус обновлен на PAID")
            print(f"  ✓ Время оплаты установлено: {datetime.fromtimestamp(now_ts)}")
        except Exception as e:
            print(f"  ❌ Ошибка при обновлении статуса: {e}")
            # Попробуем прямой SQL
            try:
                with open_connection(db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE payments 
                        SET status = ?, paid_at = ?
                        WHERE payment_id = ?
                    """, ('paid', now_ts, payment_id))
                    conn.commit()
                print(f"  ✓ Статус обновлен через прямой SQL")
            except Exception as e2:
                print(f"  ❌ Критическая ошибка при обновлении статуса: {e2}")
                return False
    else:
        print(f"  ✓ Статус уже PAID")
    print()
    
    # 4. Определяем тип платежа и обрабатываем
    print("4️⃣ ОБРАБОТКА ПЛАТЕЖА")
    print("-" * 80)
    
    # Обновляем платеж после изменения статуса
    payment = await payment_repo.get_by_payment_id(payment_id)
    
    key_type = payment.metadata.get('key_type') if payment.metadata else None
    is_subscription = key_type == 'subscription' and payment.protocol == 'v2ray'
    
    if is_subscription:
        print("  Тип: Подписка V2Ray")
        print("  Обработка через SubscriptionPurchaseService...")
        
        try:
            subscription_service = SubscriptionPurchaseService()
            success, error_msg = await subscription_service.process_subscription_purchase(payment_id)
            
            if success:
                print("  ✓ Подписка успешно создана и ключи выданы")
                
                # Проверяем результат
                with open_connection(db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, subscription_token, expires_at, is_active
                        FROM subscriptions
                        WHERE user_id = ?
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (payment.user_id,))
                    sub = cursor.fetchone()
                    if sub:
                        print(f"  ✓ Подписка: ID={sub[0]}")
                        print(f"    Токен: {sub[1][:50]}...")
                        print(f"    Истекает: {datetime.fromtimestamp(sub[2]) if sub[2] else 'N/A'}")
                        
                        # Проверяем ключи
                        cursor.execute("""
                            SELECT COUNT(*) FROM v2ray_keys
                            WHERE user_id = ? AND subscription_id = ?
                        """, (payment.user_id, sub[0]))
                        key_count = cursor.fetchone()[0]
                        print(f"    V2Ray ключей: {key_count}")
                        
                        cursor.execute("""
                            SELECT COUNT(*) FROM keys
                            WHERE user_id = ? AND subscription_id = ?
                        """, (payment.user_id, sub[0]))
                        outline_count = cursor.fetchone()[0]
                        print(f"    Outline ключей: {outline_count}")
                return True
            else:
                print(f"  ❌ Ошибка при создании подписки: {error_msg}")
                return False
                
        except Exception as e:
            print(f"  ❌ Исключение при обработке подписки: {e}")
            import traceback
            traceback.print_exc()
            return False
    else:
        print("  Тип: Обычный ключ")
        print("  Обработка через PaymentProcessor...")
        
        try:
            processor = PaymentProcessor()
            success, error_msg = await processor.process_payment(payment_id)
            
            if success:
                print("  ✓ Платеж успешно обработан")
                # Дополнительно обработаем через process_paid_payments_without_keys
                from payments.services.payment_service import PaymentService
                payment_service = PaymentService()
                processed = await payment_service.process_paid_payments_without_keys()
                print(f"  ✓ Обработано {processed} платежей")
                return True
            else:
                print(f"  ❌ Ошибка при обработке платежа: {error_msg}")
                return False
                
        except Exception as e:
            print(f"  ❌ Исключение при обработке платежа: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python3 process_payment_manual.py <payment_id>")
        print()
        print("Пример:")
        print("  python3 process_payment_manual.py 30d4bb66-000f-5001-8000-16eb00a973f7")
        sys.exit(1)
    
    payment_id = sys.argv[1]
    
    try:
        success = asyncio.run(process_payment(payment_id))
        if success:
            print()
            print("=" * 80)
            print("✅ ОБРАБОТКА ЗАВЕРШЕНА УСПЕШНО")
            print("=" * 80)
            sys.exit(0)
        else:
            print()
            print("=" * 80)
            print("❌ ОБРАБОТКА ЗАВЕРШИЛАСЬ С ОШИБКОЙ")
            print("=" * 80)
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

