#!/usr/bin/env python3
"""Показать таблицу для согласования связки подписок с платежами"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.link_subscriptions_to_payments import analyze_subscription_payments, format_timestamp
from app.settings import settings

def main():
    db_path = settings.DATABASE_PATH
    results = analyze_subscription_payments(db_path)
    
    # Фильтруем только те, что требуют обновления
    needs_update = [r for r in results if r['needs_update']]
    
    print('=' * 150)
    print('ТАБЛИЦА ДЛЯ СОГЛАСОВАНИЯ: СВЯЗКА ПОДПИСОК С ПЛАТЕЖАМИ')
    print('=' * 150)
    print()
    
    header = f"{'Sub ID':<8} {'User ID':<12} {'Тариф':<25} {'Payment ID':<40} {'Создан':<20} {'Сумма':<10} {'Tariff ID':<10}"
    print(header)
    print('-' * 150)
    
    total_payments = 0
    for r in needs_update:
        sub_id = r['subscription_id']
        user_id = r['user_id']
        tariff_name = r['tariff_name'][:25] if r['tariff_name'] else 'N/A'
        
        for p in r['payments_without_sub_id']:
            payment_id, p_id, created_at, status, amount, sub_id_old, p_tariff_id = p
            amount_rub = f'{amount/100:.2f} руб' if amount else 'N/A'
            created_str = format_timestamp(created_at)
            tariff_id_str = str(p_tariff_id) if p_tariff_id else 'N/A'
            
            row = f"{sub_id:<8} {user_id:<12} {tariff_name:<25} {p_id:<40} {created_str:<20} {amount_rub:<10} {tariff_id_str:<10}"
            print(row)
            total_payments += 1
    
    print('-' * 150)
    print()
    print(f'Всего подписок для обновления: {len(needs_update)}')
    print(f'Всего платежей для обновления: {total_payments}')
    print()
    print('=' * 150)

if __name__ == "__main__":
    main()
