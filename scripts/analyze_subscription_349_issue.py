#!/usr/bin/env python3
"""Анализ проблемы с подпиской #349 - двойное добавление длительности"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

print("=" * 80)
print("АНАЛИЗ ПРОБЛЕМЫ ПОДПИСКИ #349")
print("=" * 80)
print()

# Данные подписки #349
subscription_created_at = 1769181295  # 2026-01-23 18:14:55
subscription_expires_at = 1774365295  # 2026-03-24 18:14:55
tariff_duration = 2592000  # 30 дней

print("ДАННЫЕ ПОДПИСКИ #349:")
print(f"  Created at: {subscription_created_at} ({datetime.fromtimestamp(subscription_created_at).strftime('%Y-%m-%d %H:%M:%S')})")
print(f"  Expires at: {subscription_expires_at} ({datetime.fromtimestamp(subscription_expires_at).strftime('%Y-%m-%d %H:%M:%S')})")
print(f"  Tariff duration: {tariff_duration} сек (30 дней)")
print()

# Анализ
actual_duration = subscription_expires_at - subscription_created_at
expected_duration = tariff_duration

print("АНАЛИЗ:")
print(f"  Фактическая длительность: {actual_duration} сек ({actual_duration / 86400:.2f} дней)")
print(f"  Ожидаемая длительность: {expected_duration} сек ({expected_duration / 86400:.2f} дней)")
print(f"  Разница: {actual_duration - expected_duration} сек ({(actual_duration - expected_duration) / 86400:.2f} дней)")
print()

print("ВОЗМОЖНЫЕ ПРИЧИНЫ:")
print()
print("1. ДВОЙНОЕ ДОБАВЛЕНИЕ ДЛИТЕЛЬНОСТИ:")
print("   - При создании подписки в _get_or_create_subscription() устанавливается:")
print("     expires_at = now + tariff['duration_sec']")
print("   - Затем в process_subscription_purchase() вызывается _calculate_subscription_expires_at(),")
print("     который суммирует длительности ВСЕХ платежей, включая текущий")
print("   - Результат: длительность добавляется дважды")
print()
print("2. ПРОБЛЕМА В ЛОГИКЕ was_created:")
print("   - Если was_created = True, используется _calculate_subscription_expires_at()")
print("   - Но подписка уже создана с expires_at = created_at + duration_sec")
print("   - Затем _calculate_subscription_expires_at() добавляет длительность еще раз")
print()
print("3. ПРОБЛЕМА В _calculate_subscription_expires_at():")
print("   - Метод суммирует длительности всех completed платежей")
print("   - Если текущий платеж уже добавлен в all_payments, длительность добавляется")
print("   - Но подписка уже была создана с этой длительностью")
print()

print("РЕКОМЕНДАЦИЯ:")
print("  Проверить логику в _get_or_create_subscription():")
print("  - При создании новой подписки не устанавливать expires_at = now + duration_sec")
print("  - Вместо этого устанавливать expires_at = now (или created_at)")
print("  - Затем в process_subscription_purchase() правильно пересчитывать через _calculate_subscription_expires_at()")
print()
print("  ИЛИ:")
print("  - В _calculate_subscription_expires_at() не учитывать текущий платеж, если подписка только что создана")
print("  - Или проверять, был ли expires_at уже установлен при создании подписки")
print()

print("=" * 80)
