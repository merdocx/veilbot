#!/usr/bin/env python3
"""
Скрипт для исправления подписок, где лимит трафика не увеличился с 9 ГБ до 100 ГБ.

Устанавливает traffic_limit_mb = NULL для подписок с тарифом "1 месяц (100 Гб)",
чтобы использовался лимит из тарифа (100 ГБ).
"""
import sqlite3
import sys
import time
from pathlib import Path

# Добавляем корневую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.settings import settings


def fix_subscriptions():
    """Исправляет подписки, устанавливая traffic_limit_mb = NULL"""
    db_path = settings.DATABASE_PATH
    
    print("=" * 80)
    print("Исправление подписок: установка traffic_limit_mb = NULL")
    print("=" * 80)
    print(f"База данных: {db_path}")
    print()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Находим подписки, где тариф имеет лимит 100 ГБ, но подписка имеет лимит 9 ГБ
    nine_gb_variants = [9, 9000, 9216, 9217, 9215]
    
    cursor.execute("""
        SELECT 
            s.id,
            s.user_id,
            s.traffic_limit_mb,
            s.tariff_id,
            t.name as tariff_name,
            t.traffic_limit_mb as tariff_traffic_limit_mb
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE t.traffic_limit_mb = 102400
          AND s.traffic_limit_mb IN ({})
        ORDER BY s.id
    """.format(','.join('?' * len(nine_gb_variants))), nine_gb_variants)
    
    subscriptions_to_fix = cursor.fetchall()
    
    if not subscriptions_to_fix:
        print("✅ Подписок, требующих исправления, не найдено")
        conn.close()
        return
    
    print(f"Найдено подписок для исправления: {len(subscriptions_to_fix)}")
    print()
    
    # Показываем, что будет исправлено
    print("Подписки, которые будут исправлены:")
    print("-" * 80)
    for sub in subscriptions_to_fix:
        sub_id, user_id, sub_limit, tariff_id, tariff_name, tariff_limit = sub
        print(f"  Подписка #{sub_id} (пользователь {user_id}):")
        print(f"    Текущий лимит подписки: {sub_limit} МБ (9 ГБ)")
        print(f"    Лимит тарифа: {tariff_limit} МБ (100 ГБ)")
        print(f"    После исправления: будет использоваться лимит из тарифа (100 ГБ)")
        print()
    
    # Подтверждение
    print("=" * 80)
    response = input("Продолжить исправление? (yes/no): ").strip().lower()
    
    if response not in ['yes', 'y', 'да', 'д']:
        print("Отменено пользователем")
        conn.close()
        return
    
    # Исправляем подписки
    print()
    print("Исправление подписок...")
    print("-" * 80)
    
    now = int(time.time())
    fixed_count = 0
    
    for sub in subscriptions_to_fix:
        sub_id, user_id, sub_limit, tariff_id, tariff_name, tariff_limit = sub
        try:
            # Устанавливаем traffic_limit_mb = NULL и обновляем last_updated_at
            cursor.execute("""
                UPDATE subscriptions
                SET traffic_limit_mb = NULL,
                    last_updated_at = ?
                WHERE id = ?
            """, (now, sub_id))
            
            fixed_count += 1
            print(f"✅ Подписка #{sub_id} (пользователь {user_id}): исправлена")
            
        except Exception as e:
            print(f"❌ Ошибка при исправлении подписки #{sub_id}: {e}")
    
    conn.commit()
    
    print()
    print("=" * 80)
    print(f"Исправлено подписок: {fixed_count} из {len(subscriptions_to_fix)}")
    
    # Проверяем результат
    print()
    print("Проверка результата:")
    print("-" * 80)
    
    for sub in subscriptions_to_fix:
        sub_id = sub[0]
        cursor.execute("""
            SELECT 
                s.id,
                s.traffic_limit_mb,
                t.traffic_limit_mb as tariff_traffic_limit_mb,
                COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) as effective_limit_mb
            FROM subscriptions s
            LEFT JOIN tariffs t ON s.tariff_id = t.id
            WHERE s.id = ?
        """, (sub_id,))
        
        result = cursor.fetchone()
        if result:
            sub_id_check, sub_limit_check, tariff_limit_check, effective = result
            print(f"  Подписка #{sub_id_check}:")
            print(f"    Лимит подписки: {sub_limit_check if sub_limit_check is not None else 'NULL (используется тариф)'}")
            print(f"    Лимит тарифа: {tariff_limit_check} МБ")
            print(f"    Эффективный лимит: {effective} МБ ({effective / 1024:.2f} ГБ)")
            if effective == 102400:
                print(f"    ✅ Успешно исправлено!")
            else:
                print(f"    ⚠️  Лимит не соответствует ожидаемому (100 ГБ)")
            print()
    
    conn.close()
    print("=" * 80)
    print("✅ Готово!")


if __name__ == "__main__":
    try:
        fix_subscriptions()
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
