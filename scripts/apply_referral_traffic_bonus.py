#!/usr/bin/env python3
"""
Применение 100 ГБ трафика к реферерам, у которых уже был начислен бонус.

Скрипт начисляет 100 ГБ (102400 МБ) трафика к текущему лимиту подписки рефереров,
у которых есть рефералы с bonus_issued = 1.
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

REFERRAL_TRAFFIC_BONUS_MB = 102400  # 100 ГБ


def format_bytes(mb: int) -> str:
    """Форматирование МБ в читаемый формат"""
    if mb == 0:
        return "0 МБ (безлимит)"
    if mb < 1024:
        return f"{mb} МБ"
    gb = mb / 1024
    return f"{gb:.2f} ГБ ({mb} МБ)"


def apply_traffic_bonus(dry_run: bool = False):
    """Применение бонуса трафика к реферерам"""
    db_path = settings.DATABASE_PATH
    subscription_repo = SubscriptionRepository(db_path)
    now = int(time.time())
    
    print("=" * 100)
    if dry_run:
        print("РЕЖИМ ПРОВЕРКИ (DRY RUN) - изменения не будут применены")
    else:
        print("ПРИМЕНЕНИЕ 100 ГБ ТРАФИКА К РЕФЕРЕРАМ")
    print("=" * 100)
    print()
    
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Получаем всех рефереров с их рефералами, у которых bonus_issued = 1
        cursor.execute("""
            SELECT 
                r.referrer_id,
                COUNT(*) as referrals_count
            FROM referrals r
            WHERE r.bonus_issued = 1
            GROUP BY r.referrer_id
            ORDER BY r.referrer_id
        """)
        
        referrers = cursor.fetchall()
        
        if not referrers:
            print("❌ Не найдено рефереров с начисленными бонусами")
            return
        
        print(f"Найдено рефереров: {len(referrers)}\n")
        
        applied_count = 0
        skipped_count = 0
        
        for referrer_id, referrals_count in referrers:
            # Получаем информацию о подписке реферера
            subscription = subscription_repo.get_active_subscription(referrer_id)
            
            if not subscription:
                print(f"⚠️  Реферер {referrer_id}: нет активной подписки - пропуск")
                skipped_count += 1
                continue
            
            subscription_id = subscription[0]
            expires_at = subscription[4]
            
            # Получаем информацию о трафике
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
            elif traffic_limit_mb > 0:
                # Если traffic_limit_mb > 0, добавляем 100 ГБ
                new_traffic_limit_mb = traffic_limit_mb + REFERRAL_TRAFFIC_BONUS_MB
            else:
                # Если traffic_limit_mb = 0 (безлимит), не добавляем трафик
                print(f"⚠️  Реферер {referrer_id} (подписка {subscription_id}): текущий лимит = 0 - пропуск")
                skipped_count += 1
                continue
            
            # Применяем изменения
            print(f"✅ Реферер {referrer_id} (подписка {subscription_id}):")
            print(f"   Текущий лимит: {format_bytes(traffic_limit_mb or 0)}")
            print(f"   Новый лимит: {format_bytes(new_traffic_limit_mb)}")
            print(f"   Будет добавлено: {format_bytes(REFERRAL_TRAFFIC_BONUS_MB)}")
            
            if not dry_run:
                subscription_repo.update_subscription_traffic_limit(subscription_id, new_traffic_limit_mb)
                print(f"   ✅ Изменения применены")
            else:
                print(f"   [DRY RUN] Изменения не применены")
            
            applied_count += 1
            print()
        
        print("=" * 100)
        print("ИТОГИ")
        print("=" * 100)
        print(f"Обработано рефереров: {len(referrers)}")
        print(f"  - Начислено 100 ГБ: {applied_count}")
        print(f"  - Пропущено: {skipped_count}")
        
        if dry_run:
            print("\n⚠️  Это был режим проверки. Для применения изменений запустите скрипт без --dry-run")
        else:
            print("\n✅ Изменения успешно применены!")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Применение 100 ГБ трафика к реферерам")
    parser.add_argument("--dry-run", action="store_true", help="Режим проверки без применения изменений")
    args = parser.parse_args()
    
    apply_traffic_bonus(dry_run=args.dry_run)

