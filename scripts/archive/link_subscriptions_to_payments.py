#!/usr/bin/env python3
"""
Скрипт для связывания активных подписок с платежами
Показывает список связок для согласования перед обновлением subscription_id
"""
import sys
import os
from datetime import datetime
from typing import List, Dict, Tuple, Optional

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import settings
from app.infra.sqlite_utils import open_connection


def get_active_subscriptions(db_path: str) -> List[Tuple]:
    """Получить все активные подписки"""
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT s.id, s.user_id, s.created_at, s.expires_at, s.tariff_id,
                   t.name as tariff_name, t.duration_sec
            FROM subscriptions s
            LEFT JOIN tariffs t ON s.tariff_id = t.id
            WHERE s.is_active = 1
            ORDER BY s.user_id, s.created_at
        ''')
        return cursor.fetchall()


def has_subscription_id_column(db_path: str) -> bool:
    """Проверить, есть ли колонка subscription_id в таблице payments"""
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(payments)")
        columns = [row[1] for row in cursor.fetchall()]
        return 'subscription_id' in columns


def get_payments_for_subscription(
    db_path: str,
    subscription_id: int,
    user_id: int,
    tariff_id: Optional[int],
    sub_created_at: int,
    sub_expires_at: int
) -> List[Tuple]:
    """Получить платежи, которые должны быть связаны с подпиской"""
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        has_sub_id_col = has_subscription_id_column(db_path)
        
        if has_sub_id_col:
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
        
        if has_sub_id_col:
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
        else:
            # Если колонки subscription_id нет, просто ищем по user_id и tariff_id
            cursor.execute('''
                SELECT id, payment_id, created_at, status, amount, NULL as subscription_id, tariff_id
                FROM payments
                WHERE user_id = ?
                  AND tariff_id = ?
                  AND status = 'completed'
                  AND protocol = 'v2ray'
                  AND metadata LIKE '%subscription%'
                  AND created_at >= ?
                  AND created_at <= ?
                ORDER BY created_at ASC
            ''', (user_id, tariff_id or 4, window_start, window_end))
        
        return cursor.fetchall()


def format_timestamp(ts: int) -> str:
    """Форматировать timestamp в читаемый вид"""
    if ts is None or ts == 0:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return str(ts)


def format_duration(seconds: int) -> str:
    """Форматировать длительность"""
    if seconds is None:
        return "N/A"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    if days > 0:
        return f"{days}д {hours}ч"
    return f"{hours}ч"


def analyze_subscription_payments(db_path: str) -> List[Dict]:
    """Анализировать подписки и их платежи"""
    subscriptions = get_active_subscriptions(db_path)
    results = []
    
    for sub_id, user_id, sub_created_at, sub_expires_at, tariff_id, tariff_name, duration_sec in subscriptions:
        payments = get_payments_for_subscription(
            db_path, sub_id, user_id, tariff_id, sub_created_at, sub_expires_at
        )
        
        # Подсчитываем статистику
        payments_with_sub_id = [p for p in payments if p[5] == sub_id]
        payments_without_sub_id = [p for p in payments if p[5] is None or p[5] == 0]
        payments_with_other_sub_id = [p for p in payments if p[5] is not None and p[5] != 0 and p[5] != sub_id]
        
        # Проверяем, нужно ли обновление
        needs_update = len(payments_without_sub_id) > 0
        
        results.append({
            'subscription_id': sub_id,
            'user_id': user_id,
            'sub_created_at': sub_created_at,
            'sub_expires_at': sub_expires_at,
            'tariff_id': tariff_id,
            'tariff_name': tariff_name or 'N/A',
            'duration_sec': duration_sec,
            'payments': payments,
            'payments_with_sub_id': payments_with_sub_id,
            'payments_without_sub_id': payments_without_sub_id,
            'payments_with_other_sub_id': payments_with_other_sub_id,
            'needs_update': needs_update,
            'total_payments': len(payments),
        })
    
    return results


def print_analysis_report(results: List[Dict]):
    """Вывести отчет об анализе"""
    print("=" * 120)
    print("АНАЛИЗ СВЯЗКИ АКТИВНЫХ ПОДПИСОК С ПЛАТЕЖАМИ")
    print("=" * 120)
    print()
    
    total_subscriptions = len(results)
    subscriptions_needing_update = sum(1 for r in results if r['needs_update'])
    total_payments = sum(r['total_payments'] for r in results)
    payments_without_sub_id = sum(len(r['payments_without_sub_id']) for r in results)
    payments_with_other_sub_id = sum(len(r['payments_with_other_sub_id']) for r in results)
    
    print(f"📊 Общая статистика:")
    print(f"   Всего активных подписок: {total_subscriptions}")
    print(f"   Подписок, требующих обновления: {subscriptions_needing_update}")
    print(f"   Всего платежей найдено: {total_payments}")
    print(f"   Платежей без subscription_id: {payments_without_sub_id}")
    print(f"   Платежей с другим subscription_id: {payments_with_other_sub_id}")
    print()
    print("=" * 120)
    print()
    
    # Группируем по статусу
    needs_update_list = [r for r in results if r['needs_update']]
    already_linked_list = [r for r in results if not r['needs_update'] and r['total_payments'] > 0]
    no_payments_list = [r for r in results if r['total_payments'] == 0]
    
    if needs_update_list:
        print(f"⚠️  ПОДПИСКИ, ТРЕБУЮЩИЕ ОБНОВЛЕНИЯ ({len(needs_update_list)}):")
        print("-" * 120)
        for r in needs_update_list:
            print(f"\nПодписка #{r['subscription_id']} (User {r['user_id']})")
            print(f"  Тариф: {r['tariff_name']} (ID: {r['tariff_id']})")
            print(f"  Создана: {format_timestamp(r['sub_created_at'])}")
            print(f"  Истекает: {format_timestamp(r['sub_expires_at'])}")
            print(f"  Платежей найдено: {r['total_payments']}")
            print(f"  - С правильным subscription_id: {len(r['payments_with_sub_id'])}")
            print(f"  - Без subscription_id (нужно обновить): {len(r['payments_without_sub_id'])}")
            if r['payments_with_other_sub_id']:
                print(f"  - С другим subscription_id: {len(r['payments_with_other_sub_id'])}")
            
            if r['payments_without_sub_id']:
                print(f"\n  Платежи для обновления:")
                for p in r['payments_without_sub_id']:
                    payment_id, p_id, created_at, status, amount, sub_id, p_tariff_id = p
                    print(f"    - Payment ID: {p_id}, создан: {format_timestamp(created_at)}, "
                          f"сумма: {amount/100:.2f} руб, tariff_id: {p_tariff_id}")
            print()
        print()
    
    if already_linked_list:
        print(f"✅ ПОДПИСКИ, УЖЕ СВЯЗАННЫЕ С ПЛАТЕЖАМИ ({len(already_linked_list)}):")
        print("-" * 120)
        for r in already_linked_list[:10]:  # Показываем первые 10
            print(f"Подписка #{r['subscription_id']} (User {r['user_id']}): "
                  f"{r['total_payments']} платежей уже связаны")
        if len(already_linked_list) > 10:
            print(f"... и еще {len(already_linked_list) - 10} подписок")
        print()
    
    if no_payments_list:
        print(f"❌ ПОДПИСКИ БЕЗ ПЛАТЕЖЕЙ ({len(no_payments_list)}):")
        print("-" * 120)
        for r in no_payments_list[:10]:  # Показываем первые 10
            print(f"Подписка #{r['subscription_id']} (User {r['user_id']}): нет платежей")
        if len(no_payments_list) > 10:
            print(f"... и еще {len(no_payments_list) - 10} подписок")
        print()
    
    print("=" * 120)
    print()
    
    # Детальная таблица для согласования
    if needs_update_list:
        print("📋 ДЕТАЛЬНАЯ ТАБЛИЦА ДЛЯ СОГЛАСОВАНИЯ:")
        print("-" * 120)
        print(f"{'Sub ID':<8} {'User ID':<10} {'Тариф':<20} {'Платежей':<10} {'Нужно обновить':<15} {'Статус':<20}")
        print("-" * 120)
        
        for r in needs_update_list:
            status = "⚠️ Требует обновления"
            if r['payments_with_other_sub_id']:
                status = "⚠️ Конфликт subscription_id"
            
            print(f"{r['subscription_id']:<8} {r['user_id']:<10} {r['tariff_name'][:20]:<20} "
                  f"{r['total_payments']:<10} {len(r['payments_without_sub_id']):<15} {status:<20}")
        
        print("-" * 120)
        print()


def main():
    """Главная функция"""
    db_path = settings.DATABASE_PATH
    
    if not os.path.exists(db_path):
        print(f"❌ База данных не найдена: {db_path}")
        return 1
    
    print(f"📂 База данных: {db_path}")
    
    # Проверяем наличие колонки subscription_id
    has_sub_id = has_subscription_id_column(db_path)
    if has_sub_id:
        print("✅ Колонка subscription_id найдена в таблице payments")
    else:
        print("⚠️  Колонка subscription_id НЕ найдена - будет использован fallback по tariff_id")
    print()
    
    try:
        results = analyze_subscription_payments(db_path)
        print_analysis_report(results)
        
        # Сохраняем результаты в файл для дальнейшего анализа
        output_file = "subscription_payments_analysis.txt"
        with open(output_file, 'w', encoding='utf-8') as f:
            import sys
            old_stdout = sys.stdout
            sys.stdout = f
            print_analysis_report(results)
            sys.stdout = old_stdout
        
        print(f"💾 Полный отчет сохранен в: {output_file}")
        print()
        
        return 0
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
