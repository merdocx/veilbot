#!/usr/bin/env python3
"""
Скрипт для получения полной информации о пользователе из базы данных
"""
import sys
import os
from datetime import datetime
from typing import Optional

# Добавляем корневую директорию в путь
_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root_dir)

from app.repositories.user_repository import UserRepository
from app.repositories.key_repository import KeyRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.infra.sqlite_utils import open_connection
from app.settings import settings
from payments.repositories.payment_repository import PaymentRepository
from payments.models.payment import PaymentFilter


def format_timestamp(ts: Optional[int]) -> str:
    """Форматирование timestamp в читаемый вид"""
    if not ts or ts == 0:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return str(ts)


def format_duration(seconds: int) -> str:
    """Форматирование длительности"""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days}д {hours}ч"
    elif hours > 0:
        return f"{hours}ч {minutes}м"
    else:
        return f"{minutes}м"


def get_user_info(user_id: int):
    """Получить полную информацию о пользователе"""
    db_path = settings.DATABASE_PATH
    repo = UserRepository(db_path)
    key_repo = KeyRepository(db_path)
    sub_repo = SubscriptionRepository(db_path)
    pay_repo = PaymentRepository(db_path)
    
    print("=" * 80)
    print(f"ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ {user_id}")
    print("=" * 80)
    print()
    
    # 1. Основная информация из таблицы users
    print("📋 ОСНОВНАЯ ИНФОРМАЦИЯ")
    print("-" * 80)
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username, first_name, last_name, created_at, last_active_at, blocked
            FROM users
            WHERE user_id = ?
        """, (user_id,))
        user_row = cursor.fetchone()
        
        if user_row:
            uid, username, first_name, last_name, created_at, last_active_at, blocked = user_row
            print(f"User ID:       {uid}")
            print(f"Username:      @{username if username else 'N/A'}")
            print(f"Имя:           {first_name or 'N/A'}")
            print(f"Фамилия:       {last_name or 'N/A'}")
            print(f"Создан:        {format_timestamp(created_at)}")
            print(f"Последняя активность: {format_timestamp(last_active_at)}")
            print(f"Заблокирован:  {'Да' if blocked else 'Нет'}")
        else:
            print(f"⚠️  Пользователь {user_id} не найден в таблице users")
    print()
    
    # 2. Обзор пользователя
    print("📊 ОБЗОР")
    print("-" * 80)
    overview = repo.get_user_overview(user_id)
    print(f"V2Ray ключей:    {overview['v2ray_count']}")
    print(f"Рефералов:       {overview['referrals']}")
    print(f"Email:           {overview['email'] or 'N/A'}")
    print(f"Последняя активность: {format_timestamp(overview.get('last_activity'))}")
    print()
    
    # 3. Подписки
    print("🔑 ПОДПИСКИ")
    print("-" * 80)
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.id, s.subscription_token, s.created_at, s.expires_at, 
                   s.tariff_id, s.is_active, s.last_updated_at, s.notified,
                   t.name as tariff_name
            FROM subscriptions s
            LEFT JOIN tariffs t ON s.tariff_id = t.id
            WHERE s.user_id = ?
            ORDER BY s.created_at DESC
        """, (user_id,))
        subscriptions = cursor.fetchall()
        
        if subscriptions:
            for sub in subscriptions:
                sub_id, token, created_at, expires_at, tariff_id, is_active, last_updated, notified, tariff_name = sub
                now = int(datetime.now().timestamp())
                is_expired = expires_at < now if expires_at else False
                remaining = expires_at - now if expires_at and not is_expired else 0
                
                print(f"  Подписка #{sub_id}")
                print(f"    Токен:         {token[:50]}...")
                print(f"    Тариф:         {tariff_name or f'ID {tariff_id}'}")
                print(f"    Создана:       {format_timestamp(created_at)}")
                print(f"    Истекает:      {format_timestamp(expires_at)}")
                if not is_expired and remaining > 0:
                    print(f"    Осталось:      {format_duration(remaining)}")
                print(f"    Активна:       {'Да' if is_active else 'Нет'}")
                print(f"    Статус:        {'Истекла' if is_expired else 'Действует'}")
                print(f"    Уведомления:   {'Отправлены' if notified else 'Не отправлены'}")
                print(f"    Обновлена:     {format_timestamp(last_updated)}")
                print()
        else:
            print("  Нет подписок")
    print()
    
    # 4. Ключи (V2Ray)
    print("🛡️  V2RAY КЛЮЧИ")
    print("-" * 80)
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT k.id, k.v2ray_uuid, k.email, k.created_at, COALESCE(sub.expires_at, 0) as expiry_at,
                   k.server_id, k.tariff_id, k.subscription_id, k.traffic_limit_mb,
                   k.traffic_usage_bytes, k.notified,
                   s.name as server_name, t.name as tariff_name,
                   sub.expires_at as subscription_expires
            FROM v2ray_keys k
            LEFT JOIN servers s ON k.server_id = s.id
            LEFT JOIN tariffs t ON k.tariff_id = t.id
            LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
            WHERE k.user_id = ?
            ORDER BY k.created_at DESC
        """, (user_id,))
        v2ray_keys = cursor.fetchall()
        
        if v2ray_keys:
            for key in v2ray_keys:
                key_id, uuid, email, created_at, expiry_at, server_id, tariff_id, sub_id, traffic_limit, traffic_usage, notified, server_name, tariff_name, sub_expires = key
                expiry = sub_expires or expiry_at
                now = int(datetime.now().timestamp())
                is_expired = expiry < now if expiry else False
                remaining = expiry - now if expiry and not is_expired else 0
                
                print(f"  Ключ #{key_id}")
                print(f"    UUID:          {uuid}")
                print(f"    Email:         {email or 'N/A'}")
                print(f"    Сервер:        {server_name or f'ID {server_id}'}")
                print(f"    Тариф:         {tariff_name or f'ID {tariff_id}'}")
                print(f"    Подписка:      {sub_id or 'Нет'}")
                if expiry:
                    print(f"    Истекает:      {format_timestamp(expiry)}")
                    if not is_expired:
                        print(f"    Осталось:      {format_duration(remaining)}")
                    else:
                        print(f"    Статус:        Истек")
                if traffic_limit and traffic_limit > 0:
                    usage_mb = (traffic_usage or 0) / (1024 * 1024)
                    limit_mb = traffic_limit
                    usage_percent = (usage_mb / limit_mb * 100) if limit_mb > 0 else 0
                    print(f"    Трафик:        {usage_mb:.2f} MB / {limit_mb} MB ({usage_percent:.1f}%)")
                print(f"    Создан:        {format_timestamp(created_at)}")
                print(f"    Уведомления:   {'Отправлены' if notified else 'Не отправлены'}")
                print()
        else:
            print("  Нет V2Ray ключей")
    print()
    
    # 5. Платежи
    print("💳 ПЛАТЕЖИ")
    print("-" * 80)
    import asyncio
    async def get_payments():
        payments = await pay_repo.filter(
            PaymentFilter(user_id=user_id, limit=100, offset=0),
            sort_by="created_at",
            sort_order="DESC"
        )
        return payments
    
    payments = asyncio.run(get_payments())
    
    if payments:
        total_amount = 0
        paid_count = 0
        for payment in payments:
            # Конвертируем копейки в рубли
            amount_rub = (payment.amount or 0) / 100.0
            
            if payment.status.value == 'paid':
                total_amount += amount_rub
                paid_count += 1
            
            print(f"  Платеж {payment.payment_id}")
            print(f"    Статус:        {payment.status.value}")
            print(f"    Сумма:         {amount_rub:.2f} {payment.currency or 'RUB'}")
            print(f"    Провайдер:     {payment.provider or 'N/A'}")
            print(f"    Тариф:         {payment.tariff_id or 'N/A'}")
            print(f"    Страна:        {payment.country or 'N/A'}")
            print(f"    Протокол:      {payment.protocol or 'N/A'}")
            print(f"    Email:         {payment.email or 'N/A'}")
            print(f"    Создан:        {format_timestamp(payment.created_at)}")
            if payment.paid_at:
                print(f"    Оплачен:       {format_timestamp(payment.paid_at)}")
            print()
        
        print(f"  Итого оплачено: {paid_count} платежей на сумму {total_amount:.2f} RUB")
    else:
        print("  Нет платежей")
    print()
    
    # 6. Рефералы
    print("👥 РЕФЕРАЛЫ")
    print("-" * 80)
    referrals = repo.list_referrals(user_id)
    
    if referrals:
        for ref in referrals:
            print(f"  Реферал {ref['user_id']}")
            print(f"    Email:         {ref['email'] or 'N/A'}")
            print(f"    Приглашен:     {format_timestamp(ref['created_at'])}")
            print(f"    Бонус выдан:   {'Да' if ref['bonus_issued'] else 'Нет'}")
            print()
    else:
        print("  Нет рефералов")
    print()
    
    # 7. Реферальная информация (как реферер)
    print("🎯 РЕФЕРАЛЬНАЯ СЕТЬ")
    print("-" * 80)
    with open_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT referrer_id, created_at, bonus_issued
            FROM referrals
            WHERE referred_id = ?
        """, (user_id,))
        referrer_info = cursor.fetchone()
        
        if referrer_info:
            referrer_id, created_at, bonus_issued = referrer_info
            print(f"  Приглашен пользователем: {referrer_id}")
            print(f"  Дата приглашения: {format_timestamp(created_at)}")
            print(f"  Бонус выдан рефереру: {'Да' if bonus_issued else 'Нет'}")
        else:
            print("  Пользователь не был приглашен по реферальной программе")
    print()
    
    print("=" * 80)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python get_user_info.py <user_id>")
        sys.exit(1)
    
    try:
        user_id = int(sys.argv[1])
        get_user_info(user_id)
    except ValueError:
        print(f"Ошибка: {sys.argv[1]} не является валидным user_id")
        sys.exit(1)
    except Exception as e:
        print(f"Ошибка при получении информации: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

