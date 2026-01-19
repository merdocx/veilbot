#!/usr/bin/env python3
"""
Скрипт для удаления подписок, платежей и ключей пользователя
с сохранением флага о том, что ему ранее был выдан бесплатный ключ
"""
import argparse
import sqlite3
import sys
import os
import time

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH
from app.infra.foreign_keys import safe_foreign_keys_off
from app.utils.user_deletion_guard import check_user_can_be_deleted


def cleanup_user_with_free_key_flag(user_id: int):
    """Удаляет подписки, платежи и ключи пользователя, оставляя флаг о бесплатном ключе"""
    # Проверяем, можно ли удалить пользователя
    can_delete, reasons = check_user_can_be_deleted(user_id, DATABASE_PATH)
    if not can_delete:
        print(f"❌ Невозможно удалить данные пользователя {user_id}:")
        for reason in reasons:
            print(f"   - {reason}")
        raise ValueError(f"Нельзя удалить пользователя {user_id}: {'; '.join(reasons)}")
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        print(f"🗑️  Удаление данных пользователя {user_id}...")
        print(f"   База данных: {DATABASE_PATH}")
        print()
        
        # Используем контекстный менеджер для безопасного отключения foreign keys
        with safe_foreign_keys_off(cursor):
            # 1. Удаляем подписки
            cursor.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
            subscriptions_deleted = cursor.rowcount
            print(f"   ✓ Удалено подписок: {subscriptions_deleted}")
            
            # 2. Удаляем платежи (только те, которые не в статусе paid/completed)
            # КРИТИЧНО: Платежи со статусом 'paid' или 'completed' не могут быть удалены
            cursor.execute("""
                SELECT COUNT(*) FROM payments 
                WHERE user_id = ? AND status IN ('paid', 'completed')
            """, (user_id,))
            protected_payments = cursor.fetchone()[0]
            
            if protected_payments > 0:
                print(f"   ⚠️  Пропущено защищенных платежей (paid/completed): {protected_payments}")
            
            cursor.execute("""
                DELETE FROM payments 
                WHERE user_id = ? AND status NOT IN ('paid', 'completed')
            """, (user_id,))
            payments_deleted = cursor.rowcount
            print(f"   ✓ Удалено платежей: {payments_deleted}")
            
            # 3. Удаляем ключи Outline
            cursor.execute("DELETE FROM keys WHERE user_id = ?", (user_id,))
            keys_deleted = cursor.rowcount
            print(f"   ✓ Удалено ключей Outline: {keys_deleted}")
            
            # 4. Удаляем ключи V2Ray
            cursor.execute("DELETE FROM v2ray_keys WHERE user_id = ?", (user_id,))
            v2ray_keys_deleted = cursor.rowcount
            print(f"   ✓ Удалено ключей V2Ray: {v2ray_keys_deleted}")
            
            # 5. Добавляем флаг о том, что пользователю ранее был выдан бесплатный ключ
            # Проверяем, есть ли уже запись
            cursor.execute("""
                SELECT id FROM free_key_usage 
                WHERE user_id = ? AND protocol = ? AND country IS NULL
            """, (user_id, 'outline'))
            
            if cursor.fetchone():
                print(f"   ℹ️  Флаг о бесплатном ключе уже существует для протокола 'outline'")
            else:
                now = int(time.time())
                try:
                    cursor.execute("""
                        INSERT INTO free_key_usage (user_id, protocol, country, created_at)
                        VALUES (?, ?, ?, ?)
                    """, (user_id, 'outline', None, now))
                    print(f"   ✓ Добавлен флаг о бесплатном ключе для протокола 'outline'")
                except sqlite3.IntegrityError:
                    print(f"   ⚠️  Не удалось добавить флаг (возможно, уже существует)")
            
            # Также добавляем для v2ray, если нужно
            cursor.execute("""
                SELECT id FROM free_key_usage 
                WHERE user_id = ? AND protocol = ? AND country IS NULL
            """, (user_id, 'v2ray'))
            
            if cursor.fetchone():
                print(f"   ℹ️  Флаг о бесплатном ключе уже существует для протокола 'v2ray'")
            else:
                now = int(time.time())
                try:
                    cursor.execute("""
                        INSERT INTO free_key_usage (user_id, protocol, country, created_at)
                        VALUES (?, ?, ?, ?)
                    """, (user_id, 'v2ray', None, now))
                    print(f"   ✓ Добавлен флаг о бесплатном ключе для протокола 'v2ray'")
                except sqlite3.IntegrityError:
                    print(f"   ⚠️  Не удалось добавить флаг (возможно, уже существует)")
        
        conn.commit()
        print()
        print(f"✅ Данные пользователя {user_id} успешно удалены")
        print(f"   Флаг о бесплатном ключе сохранен")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка при удалении данных пользователя: {e}")
        raise
    finally:
        conn.close()


def main():
    """Основная функция"""
    parser = argparse.ArgumentParser(
        description="Очистить данные пользователя и сохранить флаг бесплатного ключа"
    )
    parser.add_argument(
        "user_id",
        type=int,
        help="Идентификатор пользователя, которого нужно очистить",
    )
    args = parser.parse_args()
    user_id = args.user_id

    print("=" * 60)
    print("🧹 Удаление подписок, платежей и ключей пользователя")
    print("=" * 60)
    print()
    print(f"⚠️  Будет выполнено для пользователя {user_id}:")
    print(f"   - Удаление всех подписок")
    print(f"   - Удаление всех платежей")
    print(f"   - Удаление всех ключей (Outline и V2Ray)")
    print(f"   - Сохранение флага о том, что пользователю ранее был выдан бесплатный ключ")
    print()
    
    try:
        cleanup_user_with_free_key_flag(user_id)
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












