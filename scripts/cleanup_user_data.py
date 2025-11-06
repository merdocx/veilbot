#!/usr/bin/env python3
"""
Скрипт для очистки данных пользователя и рефералов
"""
import sqlite3
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH
from app.infra.foreign_keys import safe_foreign_keys_off

def cleanup_user_data(user_id: int):
    """Удаляет все данные пользователя из базы"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        print(f"🗑️  Удаление данных пользователя {user_id}...")
        
        # Используем контекстный менеджер для безопасного отключения foreign keys
        with safe_foreign_keys_off(cursor):
            # Удаляем ключи
            cursor.execute("DELETE FROM keys WHERE user_id = ?", (user_id,))
            keys_deleted = cursor.rowcount
            print(f"   ✓ Удалено ключей Outline: {keys_deleted}")
            
            cursor.execute("DELETE FROM v2ray_keys WHERE user_id = ?", (user_id,))
            v2ray_keys_deleted = cursor.rowcount
            print(f"   ✓ Удалено ключей V2Ray: {v2ray_keys_deleted}")
            
            # Удаляем платежи
            cursor.execute("DELETE FROM payments WHERE user_id = ?", (user_id,))
            payments_deleted = cursor.rowcount
            print(f"   ✓ Удалено платежей: {payments_deleted}")
            
            # Удаляем использование бесплатных тарифов
            cursor.execute("DELETE FROM free_key_usage WHERE user_id = ?", (user_id,))
            free_usage_deleted = cursor.rowcount
            print(f"   ✓ Удалено записей free_key_usage: {free_usage_deleted}")
            
            # Удаляем из таблицы users (если существует)
            try:
                cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
                users_deleted = cursor.rowcount
                if users_deleted > 0:
                    print(f"   ✓ Удалено из users: {users_deleted}")
            except sqlite3.OperationalError:
                print("   ⚠️  Таблица users не найдена, пропускаем")
            
            # Удаляем реферальные связи (где пользователь был реферером или рефералом)
            cursor.execute("DELETE FROM referrals WHERE referrer_id = ? OR referred_id = ?", (user_id, user_id))
            referrals_deleted = cursor.rowcount
            if referrals_deleted > 0:
                print(f"   ✓ Удалено реферальных связей: {referrals_deleted}")
        
        conn.commit()
        print(f"✅ Данные пользователя {user_id} успешно удалены")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка при удалении данных пользователя: {e}")
        raise
    finally:
        conn.close()


def cleanup_all_referrals():
    """Очищает все данные о рефералах"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        print("🗑️  Очистка всех данных о рефералах...")
        
        # Получаем количество записей перед удалением
        cursor.execute("SELECT COUNT(*) FROM referrals")
        count_before = cursor.fetchone()[0]
        
        # Удаляем все записи из таблицы referrals
        cursor.execute("DELETE FROM referrals")
        deleted_count = cursor.rowcount
        
        conn.commit()
        print(f"✅ Удалено реферальных связей: {deleted_count} (было: {count_before})")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка при очистке рефералов: {e}")
        raise
    finally:
        conn.close()


def main():
    """Основная функция"""
    user_id = 6358556135
    
    print("=" * 60)
    print("🧹 Очистка данных пользователя и рефералов")
    print("=" * 60)
    print()
    
    # Подтверждение
    print(f"⚠️  ВНИМАНИЕ: Будут удалены:")
    print(f"   - Все данные пользователя {user_id}")
    print(f"   - Все данные о рефералах")
    print()
    
    response = input("Продолжить? (yes/no): ").strip().lower()
    if response != 'yes':
        print("❌ Операция отменена")
        return
    
    print()
    
    try:
        # Удаляем данные пользователя
        cleanup_user_data(user_id)
        print()
        
        # Очищаем рефералы
        cleanup_all_referrals()
        print()
        
        print("=" * 60)
        print("✅ Очистка завершена успешно")
        print("=" * 60)
        
    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ Ошибка: {e}")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()

