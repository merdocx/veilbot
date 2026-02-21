#!/usr/bin/env python3
"""
Анализ процесса покупки и продления подписок.

Проверяет:
1. Всегда ли устанавливается tariff_id при создании/продлении
2. Всегда ли устанавливается expires_at при создании/продлении
3. Всегда ли устанавливается traffic_limit_mb при создании/продлении
4. Всегда ли обновляется subscription_id в платеже
"""
import re
from pathlib import Path

# Путь к файлу
file_path = Path(__file__).parent.parent / "payments/services/subscription_purchase_service.py"

print("=" * 80)
print("АНАЛИЗ ПРОЦЕССА ПОКУПКИ И ПРОДЛЕНИЯ ПОДПИСОК")
print("=" * 80)
print()

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Проверка создания подписки
print("1. СОЗДАНИЕ ПОДПИСКИ")
print("-" * 80)

# Ищем все места создания подписки
create_patterns = [
    (r"INSERT INTO subscriptions.*?VALUES", "INSERT INTO subscriptions"),
    (r"create_subscription_async", "create_subscription_async"),
]

create_found = []
for pattern, desc in create_patterns:
    matches = list(re.finditer(pattern, content, re.MULTILINE | re.DOTALL))
    for match in matches:
        line_num = content[:match.start()].count('\n') + 1
        context_start = max(0, match.start() - 200)
        context_end = min(len(content), match.end() + 200)
        context = content[context_start:context_end]
        create_found.append((line_num, desc, context))

if create_found:
    print(f"Найдено мест создания подписки: {len(create_found)}")
    for line_num, desc, context in create_found:
        print(f"\n  Строка {line_num}: {desc}")
        # Проверяем наличие tariff_id, expires_at, traffic_limit_mb
        has_tariff = 'tariff_id' in context or "tariff['id']" in context
        has_expires = 'expires_at' in context
        has_traffic = 'traffic_limit_mb' in context
        
        print(f"    tariff_id: {'✅' if has_tariff else '❌'}")
        print(f"    expires_at: {'✅' if has_expires else '❌'}")
        print(f"    traffic_limit_mb: {'✅' if has_traffic else '❌'}")
else:
    print("❌ Места создания подписки не найдены")

print()

# 2. Проверка обновления подписки
print("2. ОБНОВЛЕНИЕ ПОДПИСКИ (ПРОДЛЕНИЕ)")
print("-" * 80)

update_patterns = [
    (r"UPDATE subscriptions.*SET.*expires_at", "UPDATE subscriptions (expires_at)"),
    (r"extend_subscription.*async", "extend_subscription_async"),
    (r"extend_subscription_by_duration", "extend_subscription_by_duration_async"),
    (r"_update_subscription_expires_at", "_update_subscription_expires_at"),
]

update_found = []
for pattern, desc in update_patterns:
    matches = list(re.finditer(pattern, content, re.MULTILINE | re.DOTALL))
    for match in matches:
        line_num = content[:match.start()].count('\n') + 1
        context_start = max(0, match.start() - 200)
        context_end = min(len(content), match.end() + 200)
        context = content[context_start:context_end]
        update_found.append((line_num, desc, context))

if update_found:
    print(f"Найдено мест обновления подписки: {len(update_found)}")
    for line_num, desc, context in update_found:
        print(f"\n  Строка {line_num}: {desc}")
        # Проверяем наличие tariff_id, expires_at, traffic_limit_mb
        has_tariff = 'tariff_id' in context or "tariff['id']" in context
        has_expires = 'expires_at' in context
        has_traffic = 'traffic_limit_mb' in context or '_update_subscription_traffic_limit_safe' in context
        
        print(f"    tariff_id: {'✅' if has_tariff else '❌'}")
        print(f"    expires_at: {'✅' if has_expires else '❌'}")
        print(f"    traffic_limit_mb: {'✅' if has_traffic else '❌'}")
else:
    print("❌ Места обновления подписки не найдены")

print()

# 3. Проверка обновления subscription_id в платеже
print("3. ОБНОВЛЕНИЕ subscription_id В ПЛАТЕЖЕ")
print("-" * 80)

payment_update_patterns = [
    (r"update_subscription_id", "update_subscription_id"),
]

payment_updates = []
for pattern, desc in payment_update_patterns:
    matches = list(re.finditer(pattern, content, re.MULTILINE))
    for match in matches:
        line_num = content[:match.start()].count('\n') + 1
        context_start = max(0, match.start() - 150)
        context_end = min(len(content), match.end() + 150)
        context = content[context_start:context_end]
        # Извлекаем строку с вызовом
        lines = context.split('\n')
        call_line = None
        for i, line in enumerate(lines):
            if 'update_subscription_id' in line:
                call_line = line.strip()
                break
        payment_updates.append((line_num, call_line))

if payment_updates:
    print(f"Найдено вызовов update_subscription_id: {len(payment_updates)}")
    for line_num, call_line in payment_updates[:10]:  # Показываем первые 10
        print(f"  Строка {line_num}: {call_line}")
    if len(payment_updates) > 10:
        print(f"  ... и еще {len(payment_updates) - 10} вызовов")
else:
    print("❌ Вызовы update_subscription_id не найдены")

print()

# 4. Проверка основных методов
print("4. ОСНОВНЫЕ МЕТОДЫ")
print("-" * 80)

methods = [
    ("process_subscription_purchase", "Основной метод обработки покупки"),
    ("_get_or_create_subscription", "Получение/создание подписки"),
    ("_create_subscription", "Создание подписки"),
    ("_create_subscription_as_renewal", "Создание подписки как продление"),
    ("_extend_subscription", "Продление подписки"),
    ("_update_subscription_expires_at", "Обновление expires_at"),
    ("_update_subscription_traffic_limit_safe", "Безопасное обновление лимита"),
]

for method_name, description in methods:
    pattern = rf"async def {method_name}|def {method_name}"
    match = re.search(pattern, content)
    if match:
        line_num = content[:match.start()].count('\n') + 1
        print(f"  ✅ {method_name} (строка {line_num}): {description}")
    else:
        print(f"  ❌ {method_name}: не найден")

print()

# 5. Проверка критических путей
print("5. КРИТИЧЕСКИЕ ПУТИ ВЫПОЛНЕНИЯ")
print("-" * 80)

# Проверяем process_subscription_purchase
process_match = re.search(r"async def process_subscription_purchase", content)
if process_match:
    start = process_match.start()
    # Находим конец метода (следующий async def или def на том же уровне)
    end_match = re.search(r"\n    async def |\n    def |\nclass ", content[start+100:])
    if end_match:
        method_content = content[start:start+100+end_match.start()]
    else:
        method_content = content[start:start+5000]
    
    # Проверяем критические операции
    checks = {
        "update_subscription_id вызывается": "update_subscription_id" in method_content,
        "tariff_id обновляется": "tariff_id" in method_content and ("UPDATE" in method_content or "SET" in method_content),
        "expires_at обновляется": "expires_at" in method_content and ("UPDATE" in method_content or "extend" in method_content),
        "traffic_limit_mb обновляется": "traffic_limit_mb" in method_content or "_update_subscription_traffic_limit_safe" in method_content,
    }
    
    for check, result in checks.items():
        print(f"  {check}: {'✅' if result else '❌'}")

print()
print("=" * 80)
print("✅ Анализ завершен")
