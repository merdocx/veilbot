#!/usr/bin/env python3
"""Исправление подписок #316 и #322 - установка правильного лимита трафика из тарифа"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from app.settings import settings

db_path = settings.DATABASE_PATH

print("=" * 80)
print("ИСПРАВЛЕНИЕ ПОДПИСОК #316 И #322")
print("=" * 80)
print()

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Проверяем подписки
subscription_ids = [316, 322]

for subscription_id in subscription_ids:
    print(f"\n{'='*80}")
    print(f"ПОДПИСКА #{subscription_id}")
    print(f"{'='*80}")
    
    # Получаем информацию о подписке
    cursor.execute("""
        SELECT id, user_id, tariff_id, traffic_limit_mb
        FROM subscriptions
        WHERE id = ?
    """, (subscription_id,))
    
    sub = cursor.fetchone()
    if not sub:
        print(f"❌ Подписка #{subscription_id} не найдена!")
        continue
    
    sub_id, user_id, tariff_id, current_traffic_limit = sub
    
    print(f"Текущий лимит: {current_traffic_limit} MB")
    
    # Получаем лимит из тарифа
    if tariff_id:
        cursor.execute("""
            SELECT id, name, traffic_limit_mb
            FROM tariffs
            WHERE id = ?
        """, (tariff_id,))
        
        tariff = cursor.fetchone()
        if tariff:
            t_id, t_name, t_traffic_limit = tariff
            print(f"Тариф: {t_name}")
            print(f"Лимит тарифа: {t_traffic_limit} MB")
            
            if current_traffic_limit != t_traffic_limit:
                print(f"\n⚠️  Несоответствие: подписка имеет {current_traffic_limit} MB, тариф имеет {t_traffic_limit} MB")
                print(f"Исправляем: устанавливаем traffic_limit_mb = {t_traffic_limit} MB")
                
                # Обновляем лимит
                cursor.execute("""
                    UPDATE subscriptions
                    SET traffic_limit_mb = ?
                    WHERE id = ?
                """, (t_traffic_limit, subscription_id))
                
                conn.commit()
                print(f"✅ Подписка #{subscription_id} исправлена: traffic_limit_mb = {t_traffic_limit} MB")
            else:
                print(f"✅ Лимит трафика соответствует тарифу")
        else:
            print(f"⚠️  Тариф #{tariff_id} не найден!")
    else:
        print(f"⚠️  Tariff ID не установлен!")

conn.close()
print("\n" + "=" * 80)
print("ГОТОВО")
print("=" * 80)
