#!/usr/bin/env python3
"""
Анализ учета VIP статуса и реферального бонуса при покупке и продлении подписок.
"""
import re
from pathlib import Path

file_path = Path(__file__).parent.parent / "payments/services/subscription_purchase_service.py"

print("=" * 80)
print("АНАЛИЗ УЧЕТА VIP СТАТУСА И РЕФЕРАЛЬНОГО БОНУСА")
print("=" * 80)
print()

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Проверка VIP статуса
print("1. ПРОВЕРКА VIP СТАТУСА")
print("-" * 80)

vip_checks = []
methods_to_check = [
    ("process_subscription_purchase", "Основной метод обработки покупки"),
    ("_get_or_create_subscription", "Получение/создание подписки"),
    ("_create_subscription", "Создание подписки"),
    ("_create_subscription_as_renewal", "Создание подписки как продление"),
    ("_extend_subscription", "Продление подписки"),
    ("_calculate_subscription_expires_at", "Расчет expires_at"),
]

for method_name, description in methods_to_check:
    pattern = rf"(async def {method_name}|def {method_name})"
    match = re.search(pattern, content)
    if match:
        start = match.start()
        # Находим конец метода
        end_match = re.search(r"\n    async def |\n    def |\nclass ", content[start+100:])
        if end_match:
            method_content = content[start:start+100+end_match.start()]
        else:
            method_content = content[start:start+3000]
        
        has_vip_check = "is_vip" in method_content or "is_user_vip" in method_content
        has_vip_expires = "VIP_EXPIRES_AT" in method_content
        has_vip_traffic = "VIP_TRAFFIC_LIMIT_MB" in method_content
        
        vip_checks.append({
            'method': method_name,
            'description': description,
            'has_check': has_vip_check,
            'has_expires': has_vip_expires,
            'has_traffic': has_vip_traffic,
            'content': method_content
        })

for check in vip_checks:
    print(f"\n{check['method']} ({check['description']}):")
    print(f"  Проверка VIP статуса: {'✅' if check['has_check'] else '❌'}")
    print(f"  VIP_EXPIRES_AT: {'✅' if check['has_expires'] else '❌'}")
    print(f"  VIP_TRAFFIC_LIMIT_MB: {'✅' if check['has_traffic'] else '❌'}")
    
    if not check['has_check'] and check['method'] in ['_get_or_create_subscription', '_extend_subscription', 'process_subscription_purchase']:
        print(f"  ⚠️  ПРОБЛЕМА: VIP статус не проверяется в критическом методе!")

print()

# 2. Проверка реферального бонуса
print("2. ПРОВЕРКА РЕФЕРАЛЬНОГО БОНУСА")
print("-" * 80)

referral_checks = []
for check in vip_checks:
    has_referral = "_calculate_referral_bonuses" in check['content'] or "referral" in check['content'].lower()
    has_safe_update = "_update_subscription_traffic_limit_safe" in check['content']
    
    referral_checks.append({
        'method': check['method'],
        'has_referral': has_referral,
        'has_safe_update': has_safe_update
    })

for check in referral_checks:
    print(f"\n{check['method']}:")
    print(f"  Учет реферального бонуса: {'✅' if check['has_referral'] or check['has_safe_update'] else '❌'}")
    print(f"  Безопасное обновление лимита: {'✅' if check['has_safe_update'] else '❌'}")

print()

# 3. Проверка критических мест
print("3. КРИТИЧЕСКИЕ МЕСТА")
print("-" * 80)

# Проверяем _get_or_create_subscription - создание новой подписки
get_or_create_match = re.search(r"async def _get_or_create_subscription", content)
if get_or_create_match:
    start = get_or_create_match.start()
    end_match = re.search(r"\n    async def |\n    def |\nclass ", content[start+500:])
    if end_match:
        method_content = content[start:start+500+end_match.start()]
    else:
        method_content = content[start:start+2000]
    
    # Проверяем создание подписки
    insert_match = re.search(r"INSERT INTO subscriptions", method_content)
    if insert_match:
        insert_context = method_content[max(0, insert_match.start()-300):insert_match.end()+300]
        has_vip_in_create = "is_vip" in insert_context or "VIP_EXPIRES_AT" in insert_context
        print(f"\n_get_or_create_subscription - создание подписки:")
        print(f"  Проверка VIP при создании: {'✅' if has_vip_in_create else '❌'}")
        if not has_vip_in_create:
            print(f"  ⚠️  ПРОБЛЕМА: VIP статус не проверяется при создании подписки!")

# Проверяем _extend_subscription - продление подписки
extend_match = re.search(r"async def _extend_subscription", content)
if extend_match:
    start = extend_match.start()
    end_match = re.search(r"\n    async def |\n    def |\nclass ", content[start+1000:])
    if end_match:
        method_content = content[start:start+1000+end_match.start()]
    else:
        method_content = content[start:start+3000]
    
    has_vip_check = "is_vip" in method_content or "is_user_vip" in method_content
    has_manual_check = "MANUAL_EXPIRY_THRESHOLD" in method_content or "4102434000" in method_content
    
    print(f"\n_extend_subscription - продление подписки:")
    print(f"  Проверка VIP статуса: {'✅' if has_vip_check else '❌'}")
    print(f"  Проверка ручной установки (VIP): {'✅' if has_manual_check else '❌'}")
    if not has_vip_check:
        print(f"  ⚠️  ПРОБЛЕМА: VIP статус не проверяется при продлении!")
        print(f"     Это может привести к сбросу VIP expires_at при продлении!")

# Проверяем process_subscription_purchase - основной метод
process_match = re.search(r"async def process_subscription_purchase", content)
if process_match:
    start = process_match.start()
    end_match = re.search(r"\n    async def |\n    def |\nclass ", content[start+2000:])
    if end_match:
        method_content = content[start:start+2000+end_match.start()]
    else:
        method_content = content[start:start+5000]
    
    has_vip_check = "is_vip" in method_content or "is_user_vip" in method_content
    
    print(f"\nprocess_subscription_purchase - основной метод:")
    print(f"  Проверка VIP статуса: {'✅' if has_vip_check else '❌'}")
    if not has_vip_check:
        print(f"  ⚠️  ПРОБЛЕМА: VIP статус не проверяется в основном методе!")
        print(f"     Это может привести к сбросу VIP expires_at при обработке платежа!")

print()

# 4. Проверка сохранения реферального бонуса
print("4. СОХРАНЕНИЕ РЕФЕРАЛЬНОГО БОНУСА")
print("-" * 80)

safe_update_match = re.search(r"async def _update_subscription_traffic_limit_safe", content)
if safe_update_match:
    start = safe_update_match.start()
    end_match = re.search(r"\n    async def |\n    def |\nclass ", content[start+500:])
    if end_match:
        method_content = content[start:start+500+end_match.start()]
    else:
        method_content = content[start:start+2000]
    
    has_bonus_check = "реферальный бонус" in method_content.lower() or "referral" in method_content.lower() or "current_limit_mb > tariff_limit_mb" in method_content
    
    print(f"\n_update_subscription_traffic_limit_safe:")
    print(f"  Сохранение реферального бонуса: {'✅' if has_bonus_check else '❌'}")
    
    # Проверяем логику
    if "current_limit_mb > tariff_limit_mb" in method_content:
        print(f"  ✅ Логика сохранения бонуса присутствует")
    else:
        print(f"  ❌ Логика сохранения бонуса отсутствует")

print()
print("=" * 80)
print("✅ Анализ завершен")
