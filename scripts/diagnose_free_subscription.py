#!/usr/bin/env python3
"""
Диагностика проблемы с выдачей бесплатной подписки при /start
"""
import sys
import os
from datetime import datetime

_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root_dir)

from app.infra.sqlite_utils import open_connection
from app.settings import settings
from bot.services.free_tariff import check_free_tariff_limit_by_protocol_and_country
from config import FREE_V2RAY_TARIFF_ID, FREE_V2RAY_COUNTRY


def format_timestamp(ts):
    if not ts or ts == 0:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return str(ts)


def diagnose_free_subscription_issue(user_id: int):
    """Диагностика проблемы с бесплатной подпиской"""
    db_path = settings.DATABASE_PATH
    
    print("=" * 80)
    print(f"ДИАГНОСТИКА ВЫДАЧИ БЕСПЛАТНОЙ ПОДПИСКИ ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}")
    print("=" * 80)
    print()
    
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # 1. Проверка лимита бесплатного тарифа
        print("1️⃣ ПРОВЕРКА ЛИМИТА БЕСПЛАТНОГО ТАРИФА")
        print("-" * 80)
        
        # Проверка enforce_global
        cursor.execute("SELECT user_id, protocol, country, created_at FROM free_key_usage WHERE user_id = ?", (user_id,))
        free_usage = cursor.fetchall()
        if free_usage:
            print("  ⚠️  ПРОБЛЕМА: Пользователь уже получал бесплатный тариф!")
            print("  Записи в free_key_usage:")
            for row in free_usage:
                uid, protocol, country, created_at = row
                print(f"    - Протокол: {protocol}, Страна: {country or 'любая'}, Дата: {format_timestamp(created_at)}")
            print()
            print("  Результат: check_free_tariff_limit_by_protocol_and_country вернет True (already_issued)")
        else:
            print("  ✓ Нет записей в free_key_usage - лимит не достигнут")
        
        # Проверка по протоколу v2ray
        cursor.execute("""
            SELECT created_at FROM free_key_usage 
            WHERE user_id = ? AND protocol = ?
        """, (user_id, "v2ray"))
        v2ray_usage = cursor.fetchone()
        if v2ray_usage:
            print(f"  ⚠️  Пользователь получал бесплатный V2Ray ключ: {format_timestamp(v2ray_usage[0])}")
        else:
            print("  ✓ Нет записей для протокола v2ray")
        
        # Проверка в таблицах ключей (обратная совместимость)
        cursor.execute("""
            SELECT k.id, k.created_at, t.name, t.price_rub
            FROM v2ray_keys k
            JOIN tariffs t ON k.tariff_id = t.id
            WHERE k.user_id = ? AND t.price_rub = 0
            ORDER BY k.created_at DESC LIMIT 1
        """, (user_id,))
        free_v2ray_key = cursor.fetchone()
        if free_v2ray_key:
            key_id, created_at, tariff_name, price = free_v2ray_key
            print(f"  ⚠️  Найден бесплатный V2Ray ключ: ID={key_id}, Тариф={tariff_name}, Дата={format_timestamp(created_at)}")
        
        print()
        
        # 2. Проверка наличия тарифа
        print("2️⃣ ПРОВЕРКА НАЛИЧИЯ БЕСПЛАТНОГО ТАРИФА")
        print("-" * 80)
        cursor.execute("""
            SELECT id, name, duration_sec, traffic_limit_mb, price_rub 
            FROM tariffs WHERE id = ?
        """, (FREE_V2RAY_TARIFF_ID,))
        tariff = cursor.fetchone()
        if tariff:
            tariff_id, name, duration_sec, traffic_limit_mb, price_rub = tariff
            print(f"  ✓ Тариф найден:")
            print(f"    ID: {tariff_id}")
            print(f"    Название: {name}")
            print(f"    Длительность: {duration_sec} сек ({duration_sec // 86400} дней)")
            print(f"    Трафик: {traffic_limit_mb} MB")
            print(f"    Цена: {price_rub} руб")
            if price_rub != 0:
                print(f"    ⚠️  ВНИМАНИЕ: Цена тарифа не равна 0! Это может влиять на проверки.")
        else:
            print(f"  ❌ ПРОБЛЕМА: Тариф с ID {FREE_V2RAY_TARIFF_ID} не найден в базе!")
            print(f"  Это приведет к статусу 'tariff_missing'")
        print()
        
        # 3. Проверка наличия активных V2Ray серверов
        print("3️⃣ ПРОВЕРКА НАЛИЧИЯ АКТИВНЫХ V2RAY СЕРВЕРОВ")
        print("-" * 80)
        cursor.execute("""
            SELECT id, name, country, protocol, active, domain 
            FROM servers 
            WHERE protocol = 'v2ray' AND active = 1
        """)
        servers = cursor.fetchall()
        if servers:
            print(f"  ✓ Найдено {len(servers)} активных V2Ray серверов:")
            for server in servers:
                srv_id, name, country, protocol, active, domain = server
                print(f"    - {name} (ID: {srv_id}, Страна: {country or 'N/A'}, Домен: {domain or 'N/A'})")
        else:
            print(f"  ❌ ПРОБЛЕМА: Нет активных V2Ray серверов!")
            print(f"  Это приведет к статусу 'no_server'")
        print()
        
        # 4. Проверка подписок пользователя
        print("4️⃣ ПРОВЕРКА ПОДПИСОК ПОЛЬЗОВАТЕЛЯ")
        print("-" * 80)
        cursor.execute("""
            SELECT s.id, s.subscription_token, s.created_at, s.expires_at, 
                   s.tariff_id, s.is_active, t.name, t.price_rub
            FROM subscriptions s
            LEFT JOIN tariffs t ON s.tariff_id = t.id
            WHERE s.user_id = ?
            ORDER BY s.created_at DESC
        """, (user_id,))
        subscriptions = cursor.fetchall()
        if subscriptions:
            print(f"  Найдено {len(subscriptions)} подписок:")
            for sub in subscriptions:
                sub_id, token, created_at, expires_at, tariff_id, is_active, tariff_name, price = sub
                now = int(datetime.now().timestamp())
                is_expired = expires_at < now if expires_at else False
                print(f"    Подписка #{sub_id}:")
                print(f"      Токен: {token[:30]}...")
                print(f"      Тариф: {tariff_name or f'ID {tariff_id}'} (цена: {price or 0} руб)")
                print(f"      Создана: {format_timestamp(created_at)}")
                print(f"      Истекает: {format_timestamp(expires_at)}")
                print(f"      Активна: {'Да' if is_active else 'Нет'}")
                print(f"      Статус: {'Истекла' if is_expired else 'Действует'}")
                print()
        else:
            print("  ✓ Нет подписок")
        print()
        
        # 5. Проверка ключей пользователя
        print("5️⃣ ПРОВЕРКА КЛЮЧЕЙ ПОЛЬЗОВАТЕЛЯ")
        print("-" * 80)
        cursor.execute("""
            SELECT COUNT(*) FROM v2ray_keys WHERE user_id = ?
        """, (user_id,))
        v2ray_count = cursor.fetchone()[0]
        print(f"  V2Ray ключей: {v2ray_count}")
        print()
        
        # 6. Тест функции check_free_tariff_limit_by_protocol_and_country
        print("6️⃣ ТЕСТ ФУНКЦИИ ПРОВЕРКИ ЛИМИТА")
        print("-" * 80)
        result_enforce_global = check_free_tariff_limit_by_protocol_and_country(
            cursor,
            user_id,
            protocol="v2ray",
            country=FREE_V2RAY_COUNTRY,
            enforce_global=True,
        )
        print(f"  enforce_global=True: {result_enforce_global}")
        if result_enforce_global:
            print("  ⚠️  Функция вернет True -> статус 'already_issued'")
        else:
            print("  ✓ Функция вернет False -> можно выдавать подписку")
        print()
        
        # 7. Резюме
        print("=" * 80)
        print("📋 РЕЗЮМЕ")
        print("=" * 80)
        
        issues = []
        if free_usage or free_v2ray_key:
            issues.append("❌ Пользователь уже получал бесплатный тариф (лимит исчерпан)")
        if not tariff:
            issues.append("❌ Бесплатный тариф не найден в базе")
        elif tariff and tariff[4] != 0:
            issues.append("⚠️  Цена бесплатного тарифа не равна 0")
        if not servers:
            issues.append("❌ Нет активных V2Ray серверов")
        if result_enforce_global:
            issues.append("❌ check_free_tariff_limit_by_protocol_and_country вернет True")
        
        if issues:
            print("Обнаруженные проблемы:")
            for issue in issues:
                print(f"  {issue}")
            print()
            print("Ожидаемый результат выдачи:")
            if result_enforce_global:
                print("  Статус: 'already_issued'")
                print("  Сообщение: 'Нажмите «Получить доступ» для получения доступа'")
            elif not tariff:
                print("  Статус: 'tariff_missing'")
                print("  Сообщение: 'Нажмите «Получить доступ» для получения доступа'")
            elif not servers:
                print("  Статус: 'no_server'")
                print("  Сообщение: 'Нажмите «Получить доступ» для получения доступа'")
        else:
            print("✓ Все проверки пройдены успешно")
            print("Подписка должна быть выдана успешно")
            print()
            print("Если подписка все равно не выдалась, возможные причины:")
            print("  - Ошибка в SubscriptionService.create_subscription")
            print("  - Ошибка при создании ключей на серверах")
            print("  - Исключение во время выполнения (проверьте логи)")
        
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python3 diagnose_free_subscription.py <user_id>")
        sys.exit(1)
    
    try:
        user_id = int(sys.argv[1])
        diagnose_free_subscription_issue(user_id)
    except ValueError:
        print(f"Ошибка: {sys.argv[1]} не является валидным user_id")
        sys.exit(1)
    except Exception as e:
        print(f"Ошибка при диагностике: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

