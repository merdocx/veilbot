#!/usr/bin/env python3
"""
Скрипт для полного удаления всех данных пользователя:
- Подписки
- Ключи (Outline и V2Ray)
- Платежи
- Выдачи бесплатных ключей
- Реферальные связи
- Информация о нажатиях start (запись в users)
"""
import argparse
import sqlite3
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH
from app.infra.foreign_keys import safe_foreign_keys_off


def delete_user_completely(user_id: int):
    """Полностью удаляет все данные пользователя"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        print(f"🗑️  Полное удаление данных пользователя {user_id}...")
        print(f"   База данных: {DATABASE_PATH}")
        print()
        
        # Используем контекстный менеджер для безопасного отключения foreign keys
        with safe_foreign_keys_off(cursor):
            # 1. Удаляем подписки
            cursor.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
            subscriptions_deleted = cursor.rowcount
            print(f"   ✓ Удалено подписок: {subscriptions_deleted}")
            
            # 2. Удаляем ключи Outline
            cursor.execute("DELETE FROM keys WHERE user_id = ?", (user_id,))
            keys_deleted = cursor.rowcount
            print(f"   ✓ Удалено ключей Outline: {keys_deleted}")
            
            # 3. Удаляем ключи V2Ray
            cursor.execute("DELETE FROM v2ray_keys WHERE user_id = ?", (user_id,))
            v2ray_keys_deleted = cursor.rowcount
            print(f"   ✓ Удалено ключей V2Ray: {v2ray_keys_deleted}")
            
            # 4. Удаляем платежи
            cursor.execute("DELETE FROM payments WHERE user_id = ?", (user_id,))
            payments_deleted = cursor.rowcount
            print(f"   ✓ Удалено платежей: {payments_deleted}")
            
            # 5. Удаляем выдачи бесплатных ключей
            cursor.execute("DELETE FROM free_key_usage WHERE user_id = ?", (user_id,))
            free_usage_deleted = cursor.rowcount
            print(f"   ✓ Удалено записей о бесплатных ключах: {free_usage_deleted}")
            
            # 6. Удаляем реферальные связи (где пользователь был реферером или рефералом)
            cursor.execute("DELETE FROM referrals WHERE referrer_id = ? OR referred_id = ?", (user_id, user_id))
            referrals_deleted = cursor.rowcount
            print(f"   ✓ Удалено реферальных связей: {referrals_deleted}")
            
            # 7. Удаляем информацию о нажатиях start (запись в users)
            cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            users_deleted = cursor.rowcount
            print(f"   ✓ Удалено записей из users (нажатия start): {users_deleted}")
        
        conn.commit()
        print()
        print(f"✅ Все данные пользователя {user_id} успешно удалены")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка при удалении данных пользователя: {e}")
        raise
    finally:
        conn.close()


def main():
    """Основная функция"""
    parser = argparse.ArgumentParser(
        description="Полностью удалить все данные пользователя по его ID"
    )
    parser.add_argument(
        "user_id",
        type=int,
        help="Идентификатор пользователя, данные которого нужно удалить",
    )
    args = parser.parse_args()
    user_id = args.user_id

    print("=" * 60)
    print("🧹 Полное удаление всех данных пользователя")
    print("=" * 60)
    print()
    print(f"⚠️  Будет выполнено для пользователя {user_id}:")
    print(f"   - Удаление всех подписок")
    print(f"   - Удаление всех ключей (Outline и V2Ray)")
    print(f"   - Удаление всех платежей")
    print(f"   - Удаление всех записей о бесплатных ключах")
    print(f"   - Удаление всех реферальных связей")
    print(f"   - Удаление информации о нажатиях start (users)")
    print()
    
    try:
        delete_user_completely(user_id)
        print()
        print("=" * 60)
        print("✅ Операция завершена успешно")
        print("=" * 60)
        
    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ Ошибка: {e}")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()



