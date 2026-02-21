#!/usr/bin/env python3
"""
Анализ процесса покупки и продления подписок на предмет ошибок с лимитом трафика.

Проверяет:
1. Подписки, где лимит подписки не соответствует лимиту тарифа
2. Подписки, где лимит должен был обновиться, но остался старым
3. Потенциальные проблемы в логике обновления лимита
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

# Добавляем корневую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.settings import settings


def format_bytes(bytes_value: int) -> str:
    """Форматирует байты в читаемый формат"""
    if bytes_value is None or bytes_value == 0:
        return "0 Б"
    
    for unit in ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} ПБ"


def format_timestamp(ts: int) -> str:
    """Форматирует timestamp в читаемый формат"""
    if ts is None or ts == 0:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return str(ts)


def analyze_subscription_traffic_limits():
    """Анализирует подписки на предмет проблем с лимитом трафика"""
    db_path = settings.DATABASE_PATH
    
    print("=" * 80)
    print("Анализ процесса покупки и продления подписок")
    print("Проверка на ошибки с лимитом трафика")
    print("=" * 80)
    print(f"База данных: {db_path}")
    print()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Проверка: подписки, где лимит подписки не соответствует лимиту тарифа
    print("1. ПОДПИСКИ С НЕСООТВЕТСТВИЕМ ЛИМИТА ПОДПИСКИ И ТАРИФА")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            s.id,
            s.user_id,
            s.traffic_limit_mb,
            s.tariff_id,
            s.created_at,
            s.last_updated_at,
            s.expires_at,
            s.is_active,
            t.name as tariff_name,
            t.traffic_limit_mb as tariff_traffic_limit_mb,
            COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) as effective_limit_mb
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE s.is_active = 1
          AND t.traffic_limit_mb IS NOT NULL
          AND t.traffic_limit_mb > 0
          AND (
              -- Лимит подписки установлен и не равен лимиту тарифа
              (s.traffic_limit_mb IS NOT NULL AND s.traffic_limit_mb != t.traffic_limit_mb)
              OR
              -- Лимит подписки NULL, но тариф имеет лимит (это нормально, но проверим)
              (s.traffic_limit_mb IS NULL)
          )
        ORDER BY s.id
    """)
    
    mismatches = cursor.fetchall()
    
    # Разделяем на проблемные и нормальные
    problematic = []
    normal = []
    
    for sub in mismatches:
        sub_id, user_id, sub_limit, tariff_id, created_at, last_updated, expires_at, is_active, tariff_name, tariff_limit, effective = sub
        
        # Если лимит подписки установлен и не равен лимиту тарифа - это проблема
        if sub_limit is not None and sub_limit != tariff_limit:
            problematic.append(sub)
        # Если лимит подписки NULL, но тариф имеет лимит - это нормально (используется лимит из тарифа)
        elif sub_limit is None and tariff_limit is not None:
            normal.append(sub)
    
    if problematic:
        print(f"⚠️  Найдено проблемных подписок: {len(problematic)}")
        print("   (Лимит подписки установлен и не соответствует лимиту тарифа)")
        print()
        
        for sub in problematic:
            sub_id, user_id, sub_limit, tariff_id, created_at, last_updated, expires_at, is_active, tariff_name, tariff_limit, effective = sub
            print(f"  Подписка #{sub_id} (пользователь {user_id}):")
            print(f"    Лимит подписки: {sub_limit} МБ ({format_bytes(sub_limit * 1024 * 1024)}) ❌")
            print(f"    Лимит тарифа: {tariff_limit} МБ ({format_bytes(tariff_limit * 1024 * 1024)}) ✅")
            print(f"    Эффективный лимит: {effective} МБ ({format_bytes(effective * 1024 * 1024)}) ❌")
            print(f"    Тариф: {tariff_name} (ID: {tariff_id})")
            print(f"    Создана: {format_timestamp(created_at)}")
            print(f"    Обновлена: {format_timestamp(last_updated)}")
            print(f"    Истекает: {format_timestamp(expires_at)}")
            print(f"    Активна: {'Да' if is_active else 'Нет'}")
            print()
    else:
        print("✅ Проблемных подписок не найдено")
        print()
    
    if normal:
        print(f"ℹ️  Подписок с NULL лимитом (используется лимит из тарифа): {len(normal)}")
        print("   (Это нормально - лимит берется из тарифа)")
        print()
    
    # 2. Проверка: подписки, где лимит подписки = 0, но тариф имеет лимит
    print("2. ПОДПИСКИ С БЕЗЛИМИТНЫМ ЛИМИТОМ (0), НО ТАРИФ ИМЕЕТ ЛИМИТ")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            s.id,
            s.user_id,
            s.traffic_limit_mb,
            s.tariff_id,
            s.created_at,
            s.expires_at,
            t.name as tariff_name,
            t.traffic_limit_mb as tariff_traffic_limit_mb
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE s.is_active = 1
          AND s.traffic_limit_mb = 0
          AND t.traffic_limit_mb IS NOT NULL
          AND t.traffic_limit_mb > 0
        ORDER BY s.id
    """)
    
    unlimited_with_tariff_limit = cursor.fetchall()
    
    if unlimited_with_tariff_limit:
        print(f"⚠️  Найдено подписок: {len(unlimited_with_tariff_limit)}")
        print("   (Лимит подписки = 0 (безлимит), но тариф имеет лимит)")
        print("   Это может быть проблемой, если безлимит не был намеренным")
        print()
        
        for sub in unlimited_with_tariff_limit:
            sub_id, user_id, sub_limit, tariff_id, created_at, expires_at, tariff_name, tariff_limit = sub
            print(f"  Подписка #{sub_id} (пользователь {user_id}):")
            print(f"    Лимит подписки: 0 (безлимит)")
            print(f"    Лимит тарифа: {tariff_limit} МБ ({format_bytes(tariff_limit * 1024 * 1024)})")
            print(f"    Тариф: {tariff_name} (ID: {tariff_id})")
            print(f"    Создана: {format_timestamp(created_at)}")
            print()
    else:
        print("✅ Проблемных подписок не найдено")
        print()
    
    # 3. Проверка: подписки, где лимит подписки установлен, но тариф NULL или не задан
    print("3. ПОДПИСКИ С ЛИМИТОМ, НО БЕЗ ТАРИФА")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            s.id,
            s.user_id,
            s.traffic_limit_mb,
            s.tariff_id,
            s.created_at,
            s.expires_at,
            t.name as tariff_name
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE s.is_active = 1
          AND s.traffic_limit_mb IS NOT NULL
          AND s.traffic_limit_mb > 0
          AND (s.tariff_id IS NULL OR t.id IS NULL)
        ORDER BY s.id
    """)
    
    subs_without_tariff = cursor.fetchall()
    
    if subs_without_tariff:
        print(f"ℹ️  Найдено подписок: {len(subs_without_tariff)}")
        print("   (Лимит подписки установлен, но тариф не задан)")
        print()
        
        for sub in subs_without_tariff:
            sub_id, user_id, sub_limit, tariff_id, created_at, expires_at, tariff_name = sub
            print(f"  Подписка #{sub_id} (пользователь {user_id}):")
            print(f"    Лимит подписки: {sub_limit} МБ ({format_bytes(sub_limit * 1024 * 1024)})")
            print(f"    Тариф: {tariff_name or 'не задан'} (ID: {tariff_id})")
            print(f"    Создана: {format_timestamp(created_at)}")
            print()
    else:
        print("✅ Все подписки с лимитом имеют тариф")
        print()
    
    # 4. Статистика по всем подпискам
    print("4. СТАТИСТИКА")
    print("-" * 80)
    
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN s.traffic_limit_mb IS NULL THEN 1 END) as null_limit,
            COUNT(CASE WHEN s.traffic_limit_mb = 0 THEN 1 END) as unlimited,
            COUNT(CASE WHEN s.traffic_limit_mb > 0 THEN 1 END) as with_limit,
            COUNT(CASE WHEN s.traffic_limit_mb IS NOT NULL 
                       AND t.traffic_limit_mb IS NOT NULL 
                       AND s.traffic_limit_mb != t.traffic_limit_mb THEN 1 END) as mismatched
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE s.is_active = 1
    """)
    
    stats = cursor.fetchone()
    total, null_limit, unlimited, with_limit, mismatched = stats
    
    print(f"Всего активных подписок: {total}")
    print(f"  - С NULL лимитом (используется тариф): {null_limit}")
    print(f"  - С безлимитом (0): {unlimited}")
    print(f"  - С установленным лимитом: {with_limit}")
    print(f"  - С несоответствием лимита подписки и тарифа: {mismatched}")
    print()
    
    # 5. Проверка тарифов
    print("5. ТАРИФЫ")
    print("-" * 80)
    
    cursor.execute("""
        SELECT id, name, traffic_limit_mb, price_rub, duration_sec
        FROM tariffs
        ORDER BY id
    """)
    
    tariffs = cursor.fetchall()
    
    print(f"Всего тарифов: {len(tariffs)}")
    print()
    
    for tariff in tariffs:
        tariff_id, name, limit_mb, price, duration = tariff
        limit_display = f"{limit_mb} МБ ({format_bytes(limit_mb * 1024 * 1024)})" if limit_mb else "не задан"
        print(f"  Тариф #{tariff_id}: {name}")
        print(f"    Лимит: {limit_display}")
        print(f"    Цена: {price} руб, Длительность: {duration} сек")
        print()
    
    conn.close()
    
    print("=" * 80)
    print("✅ Анализ завершен")
    
    return {
        'problematic': len(problematic) if problematic else 0,
        'unlimited_with_tariff': len(unlimited_with_tariff_limit) if unlimited_with_tariff_limit else 0,
        'mismatched': mismatched
    }


if __name__ == "__main__":
    try:
        results = analyze_subscription_traffic_limits()
        
        if results['problematic'] > 0 or results['mismatched'] > 0:
            print()
            print("⚠️  Обнаружены проблемы, требующие внимания!")
            sys.exit(1)
        else:
            print()
            print("✅ Проблем не обнаружено")
            sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
