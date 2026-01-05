#!/usr/bin/env python3
"""
Анализ всех рефереров для начисления 100 ГБ трафика за рефералов.

Скрипт анализирует:
1. Всех пользователей, которые пригласили рефералов
2. Рефералов, за которых уже был начислен бонус (bonus_issued = 1)
3. Текущий статус подписки реферера
4. Текущий лимит трафика
5. Формирует список для начисления 100 ГБ трафика
"""
import sys
import os
import time
from datetime import datetime

_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root_dir)

from app.infra.sqlite_utils import open_connection
from app.settings import settings
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository

REFERRAL_TRAFFIC_BONUS_MB = 102400  # 100 ГБ


def format_timestamp(ts: int) -> str:
    """Форматирование timestamp в читаемый формат"""
    if not ts:
        return "N/A"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def format_bytes(mb: int) -> str:
    """Форматирование МБ в читаемый формат"""
    if mb == 0:
        return "0 МБ (безлимит)"
    if mb < 1024:
        return f"{mb} МБ"
    gb = mb / 1024
    return f"{gb:.2f} ГБ ({mb} МБ)"


def analyze_referrers():
    """Анализ всех рефереров"""
    db_path = settings.DATABASE_PATH
    subscription_repo = SubscriptionRepository(db_path)
    user_repo = UserRepository(db_path)
    now = int(time.time())
    
    print("=" * 100)
    print("АНАЛИЗ РЕФЕРАЛОВ ДЛЯ НАЧИСЛЕНИЯ 100 ГБ ТРАФИКА")
    print("=" * 100)
    print()
    
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Получаем всех рефереров с их рефералами, у которых bonus_issued = 1
        cursor.execute("""
            SELECT 
                r.referrer_id,
                COUNT(*) as referrals_count,
                MIN(r.created_at) as first_referral_at,
                MAX(r.created_at) as last_referral_at
            FROM referrals r
            WHERE r.bonus_issued = 1
            GROUP BY r.referrer_id
            ORDER BY r.referrer_id
        """)
        
        referrers = cursor.fetchall()
        
        if not referrers:
            print("❌ Не найдено рефереров с начисленными бонусами")
            return
        
        print(f"Найдено рефереров с начисленными бонусами: {len(referrers)}\n")
        
        results = []
        
        for referrer_id, referrals_count, first_referral_at, last_referral_at in referrers:
            # Получаем информацию о подписке реферера
            subscription = subscription_repo.get_active_subscription(referrer_id)
            
            # Получаем информацию о трафике
            if subscription:
                subscription_id = subscription[0]
                expires_at = subscription[4]
                
                cursor.execute("""
                    SELECT traffic_limit_mb, tariff_id
                    FROM subscriptions
                    WHERE id = ?
                """, (subscription_id,))
                
                sub_row = cursor.fetchone()
                traffic_limit_mb = sub_row[0] if sub_row else None
                tariff_id = sub_row[1] if sub_row else None
                
                # Вычисляем новый лимит трафика
                new_traffic_limit_mb = None
                can_add_traffic = False
                
                if traffic_limit_mb is None:
                    # Если traffic_limit_mb = NULL, получаем из тарифа и добавляем 100 ГБ
                    if tariff_id:
                        cursor.execute(
                            "SELECT traffic_limit_mb FROM tariffs WHERE id = ?",
                            (tariff_id,)
                        )
                        tariff_row = cursor.fetchone()
                        if tariff_row and tariff_row[0] is not None:
                            base_limit = tariff_row[0] or 0
                            new_traffic_limit_mb = base_limit + REFERRAL_TRAFFIC_BONUS_MB
                        else:
                            new_traffic_limit_mb = REFERRAL_TRAFFIC_BONUS_MB
                    else:
                        new_traffic_limit_mb = REFERRAL_TRAFFIC_BONUS_MB
                    can_add_traffic = True
                elif traffic_limit_mb > 0:
                    # Если traffic_limit_mb > 0, добавляем 100 ГБ
                    new_traffic_limit_mb = traffic_limit_mb + REFERRAL_TRAFFIC_BONUS_MB
                    can_add_traffic = True
                # Если traffic_limit_mb = 0 (безлимит), не добавляем трафик
                
                # Получаем email пользователя
                email = user_repo._resolve_user_email(cursor, referrer_id) or None
                
                # Получаем список рефералов
                cursor.execute("""
                    SELECT referred_id, created_at
                    FROM referrals
                    WHERE referrer_id = ? AND bonus_issued = 1
                    ORDER BY created_at
                """, (referrer_id,))
                referred_list = cursor.fetchall()
                
                results.append({
                    "referrer_id": referrer_id,
                    "email": email,
                    "subscription_id": subscription_id,
                    "expires_at": expires_at,
                    "current_traffic_limit_mb": traffic_limit_mb,
                    "new_traffic_limit_mb": new_traffic_limit_mb,
                    "can_add_traffic": can_add_traffic,
                    "referrals_count": referrals_count,
                    "first_referral_at": first_referral_at,
                    "last_referral_at": last_referral_at,
                    "referred_list": referred_list,
                })
            else:
                # Нет активной подписки
                cursor.execute("SELECT email FROM users WHERE user_id = ?", (referrer_id,))
                user_row = cursor.fetchone()
                email = user_row[0] if user_row else None
                
                cursor.execute("""
                    SELECT referred_id, created_at
                    FROM referrals
                    WHERE referrer_id = ? AND bonus_issued = 1
                    ORDER BY created_at
                """, (referrer_id,))
                referred_list = cursor.fetchall()
                
                results.append({
                    "referrer_id": referrer_id,
                    "email": email,
                    "subscription_id": None,
                    "expires_at": None,
                    "current_traffic_limit_mb": None,
                    "new_traffic_limit_mb": None,
                    "can_add_traffic": False,
                    "referrals_count": referrals_count,
                    "first_referral_at": first_referral_at,
                    "last_referral_at": last_referral_at,
                    "referred_list": referred_list,
                    "note": "Нет активной подписки - трафик не начисляется"
                })
        
        # Выводим результаты
        print("РЕЗУЛЬТАТЫ АНАЛИЗА:\n")
        print("-" * 100)
        
        can_add_count = sum(1 for r in results if r.get("can_add_traffic", False))
        no_subscription_count = sum(1 for r in results if r.get("subscription_id") is None)
        
        print(f"Всего рефереров: {len(results)}")
        print(f"  - Могут получить 100 ГБ трафика: {can_add_count}")
        print(f"  - Без активной подписки: {no_subscription_count}")
        print()
        
        # Детальная информация
        for idx, result in enumerate(results, 1):
            print(f"\n{idx}. Реферер ID: {result['referrer_id']}")
            if result['email']:
                print(f"   Email: {result['email']}")
            print(f"   Рефералов с начисленным бонусом: {result['referrals_count']}")
            print(f"   Первый реферал: {format_timestamp(result['first_referral_at'])}")
            print(f"   Последний реферал: {format_timestamp(result['last_referral_at'])}")
            
                if result['subscription_id']:
                print(f"   Подписка ID: {result['subscription_id']}")
                print(f"   Истекает: {format_timestamp(result['expires_at'])}")
                
                if result['current_traffic_limit_mb'] is not None:
                    print(f"   Текущий лимит: {format_bytes(result['current_traffic_limit_mb'])}")
                else:
                    print(f"   Текущий лимит: NULL (из тарифа)")
                
                if result['can_add_traffic']:
                    print(f"   ✅ Новый лимит: {format_bytes(result['new_traffic_limit_mb'])}")
                    print(f"   ✅ Будет добавлено: {format_bytes(REFERRAL_TRAFFIC_BONUS_MB)}")
                else:
                    print(f"   ⚠️  Текущий лимит = 0 (безлимит) - трафик не добавляется")
            else:
                print(f"   ❌ Нет активной подписки")
                print(f"   ⚠️  Трафик не начисляется (нет подписки)")
            
            # Список рефералов
            if result['referred_list']:
                print(f"   Рефералы:")
                for referred_id, created_at in result['referred_list']:
                    print(f"     - ID {referred_id} (приглашен {format_timestamp(created_at)})")
        
        print("\n" + "=" * 100)
        print("ИТОГОВАЯ СТАТИСТИКА")
        print("=" * 100)
        print(f"Всего рефереров: {len(results)}")
        print(f"Могут получить 100 ГБ: {can_add_count}")
        print(f"Без подписки: {no_subscription_count}")
        print()
        
        # Формируем список для начисления
        eligible_referrers = [r for r in results if r.get("can_add_traffic", False)]
        
        if eligible_referrers:
            print("СПИСОК ДЛЯ НАЧИСЛЕНИЯ 100 ГБ ТРАФИКА:")
            print("-" * 100)
            for result in eligible_referrers:
                print(f"  - Реферер {result['referrer_id']} (подписка {result['subscription_id']}): "
                      f"{format_bytes(result['current_traffic_limit_mb'] or 0)} → "
                      f"{format_bytes(result['new_traffic_limit_mb'])}")
        else:
            print("Нет рефереров, которым можно начислить трафик")
        
        print("\n" + "=" * 100)


if __name__ == "__main__":
    analyze_referrers()

