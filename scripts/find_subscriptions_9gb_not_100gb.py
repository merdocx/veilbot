#!/usr/bin/env python3
"""
Скрипт для поиска подписок, где лимит трафика не увеличился с 9 ГБ до 100 ГБ.

Ищет подписки с лимитом трафика около 9 ГБ (9, 9000, 9216 МБ) вместо 100 ГБ (102400 МБ).
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Добавляем корневую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.settings import settings


def format_bytes(bytes_value: int) -> str:
    """Форматирует байты в читаемый формат"""
    if bytes_value is None:
        return "—"
    if bytes_value == 0:
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


def find_subscriptions_with_9gb_limit():
    """Находит все подписки с лимитом трафика около 9 ГБ"""
    db_path = settings.DATABASE_PATH
    
    # Значения, которые могут означать 9 ГБ
    # 9 ГБ = 9 * 1024 = 9216 МБ
    # Но могут быть и другие варианты: 9, 9000, 9216
    nine_gb_variants = [9, 9000, 9216, 9217, 9215]  # Небольшой диапазон для учета округлений
    
    print("=" * 80)
    print("Поиск подписок с лимитом трафика 9 ГБ вместо 100 ГБ")
    print("=" * 80)
    print(f"База данных: {db_path}")
    print()
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Сначала проверим тарифы с лимитом 9 ГБ
    print("Проверка тарифов с лимитом около 9 ГБ:")
    print("-" * 80)
    cursor.execute("""
        SELECT id, name, traffic_limit_mb, price_rub, duration_sec
        FROM tariffs
        WHERE traffic_limit_mb IN ({})
        ORDER BY id
    """.format(','.join('?' * len(nine_gb_variants))), nine_gb_variants)
    
    tariffs_9gb = cursor.fetchall()
    if tariffs_9gb:
        print(f"Найдено тарифов с лимитом 9 ГБ: {len(tariffs_9gb)}")
        for tariff in tariffs_9gb:
            tariff_id, name, limit_mb, price, duration = tariff
            print(f"  Тариф #{tariff_id}: {name}")
            print(f"    Лимит: {limit_mb} МБ ({format_bytes(limit_mb * 1024 * 1024)})")
            print(f"    Цена: {price} руб, Длительность: {duration} сек")
    else:
        print("  Тарифов с лимитом 9 ГБ не найдено")
    print()
    
    # Теперь ищем подписки с лимитом 9 ГБ
    print("Поиск подписок с лимитом трафика около 9 ГБ:")
    print("-" * 80)
    
    # Ищем подписки, где traffic_limit_mb установлен и равен одному из вариантов 9 ГБ
    cursor.execute("""
        SELECT 
            s.id,
            s.user_id,
            s.traffic_limit_mb,
            s.tariff_id,
            s.created_at,
            s.expires_at,
            s.is_active,
            t.name as tariff_name,
            t.traffic_limit_mb as tariff_traffic_limit_mb,
            COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) as effective_limit_mb
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE s.traffic_limit_mb IN ({})
           OR (s.traffic_limit_mb IS NULL AND t.traffic_limit_mb IN ({}))
        ORDER BY s.id
    """.format(
        ','.join('?' * len(nine_gb_variants)),
        ','.join('?' * len(nine_gb_variants))
    ), nine_gb_variants + nine_gb_variants)
    
    subscriptions = cursor.fetchall()
    
    # Отдельно ищем подписки, где лимит должен был быть 100 ГБ, но остался 9 ГБ
    print()
    print("Проверка подписок, где лимит должен был быть 100 ГБ, но остался 9 ГБ:")
    print("-" * 80)
    
    # Ищем подписки, где тариф имеет лимит 100 ГБ, но подписка имеет лимит 9 ГБ
    cursor.execute("""
        SELECT 
            s.id,
            s.user_id,
            s.traffic_limit_mb,
            s.tariff_id,
            s.created_at,
            s.expires_at,
            s.is_active,
            t.name as tariff_name,
            t.traffic_limit_mb as tariff_traffic_limit_mb,
            COALESCE(s.traffic_limit_mb, t.traffic_limit_mb, 0) as effective_limit_mb
        FROM subscriptions s
        LEFT JOIN tariffs t ON s.tariff_id = t.id
        WHERE t.traffic_limit_mb = 102400
          AND s.traffic_limit_mb IN ({})
        ORDER BY s.id
    """.format(','.join('?' * len(nine_gb_variants))), nine_gb_variants)
    
    subscriptions_mismatch = cursor.fetchall()
    if subscriptions_mismatch:
        print(f"⚠️  Найдено подписок с проблемой: {len(subscriptions_mismatch)}")
        print("   (Тариф имеет лимит 100 ГБ, но подписка имеет лимит 9 ГБ)")
        print()
        for sub in subscriptions_mismatch:
            sub_id, user_id, sub_limit, tariff_id, created_at, expires_at, is_active, tariff_name, tariff_limit, effective = sub
            print(f"  Подписка #{sub_id} (пользователь {user_id}):")
            print(f"    Лимит подписки: {sub_limit} МБ ({format_bytes(sub_limit * 1024 * 1024)}) ❌")
            print(f"    Лимит тарифа: {tariff_limit} МБ ({format_bytes(tariff_limit * 1024 * 1024)}) ✅")
            print(f"    Эффективный лимит: {effective} МБ ({format_bytes(effective * 1024 * 1024)}) ❌")
            print(f"    Тариф: {tariff_name} (ID: {tariff_id})")
            print(f"    Создана: {format_timestamp(created_at)}")
            print(f"    Истекает: {format_timestamp(expires_at)}")
            print(f"    Активна: {'Да' if is_active else 'Нет'}")
            print()
    else:
        print("  Подписок с несоответствием не найдено")
    
    if subscriptions:
        print()
        print(f"Всего найдено подписок с лимитом 9 ГБ: {len(subscriptions)}")
        print("(Включая пробные подписки с тарифом 'Пробные 3 дня')")
    
    print()
    print("=" * 80)
    
    # Статистика
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN traffic_limit_mb IN ({}) THEN 1 END) as with_9gb_direct,
            COUNT(CASE WHEN traffic_limit_mb IS NULL AND tariff_id IN (
                SELECT id FROM tariffs WHERE traffic_limit_mb IN ({})
            ) THEN 1 END) as with_9gb_via_tariff
        FROM subscriptions
    """.format(
        ','.join('?' * len(nine_gb_variants)),
        ','.join('?' * len(nine_gb_variants))
    ), nine_gb_variants + nine_gb_variants)
    
    stats = cursor.fetchone()
    total_subs, direct_9gb, via_tariff_9gb = stats
    
    print(f"Статистика:")
    print(f"  Всего подписок: {total_subs}")
    print(f"  С лимитом 9 ГБ (прямо): {direct_9gb}")
    print(f"  С лимитом 9 ГБ (через тариф): {via_tariff_9gb}")
    print()
    
    conn.close()
    
    return subscriptions if subscriptions else []


if __name__ == "__main__":
    try:
        subscriptions = find_subscriptions_with_9gb_limit()
        if subscriptions:
            print(f"\n✅ Найдено {len(subscriptions)} подписок с лимитом 9 ГБ")
            sys.exit(0)
        else:
            print("\n✅ Подписок с лимитом 9 ГБ не найдено")
            sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
