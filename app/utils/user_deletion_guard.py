"""
Утилита для защиты от удаления пользователей с активными подписками и платежами
"""
import sqlite3
import time
from typing import Tuple, List, Optional


def check_user_can_be_deleted(user_id: int, db_path: str) -> Tuple[bool, List[str]]:
    """
    Проверяет, можно ли безопасно удалить пользователя.
    
    Args:
        user_id: ID пользователя
        db_path: Путь к базе данных
        
    Returns:
        Tuple[can_delete, reasons]
        can_delete: True если можно удалить, False если нельзя
        reasons: Список причин, почему нельзя удалить
    """
    reasons = []
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        now = int(time.time())
        
        # 1. Проверяем активные подписки
        cursor.execute("""
            SELECT id, expires_at, is_active
            FROM subscriptions
            WHERE user_id = ? AND expires_at > ?
        """, (user_id, now))
        active_subscriptions = cursor.fetchall()
        
        if active_subscriptions:
            active_count = sum(1 for sub in active_subscriptions if sub[2] == 1)
            if active_count > 0:
                reasons.append(
                    f"У пользователя есть {active_count} активная(ых) подписка(ок) "
                    f"(ID: {', '.join(str(sub[0]) for sub in active_subscriptions if sub[2] == 1)})"
                )
        
        # 2. Проверяем успешные платежи (completed или paid)
        cursor.execute("""
            SELECT id, payment_id, status, amount
            FROM payments
            WHERE user_id = ? AND status IN ('completed', 'paid')
        """, (user_id,))
        successful_payments = cursor.fetchall()
        
        if successful_payments:
            total_amount = sum(p[3] for p in successful_payments) / 100  # Конвертируем копейки в рубли
            payment_ids = [p[1] for p in successful_payments]
            reasons.append(
                f"У пользователя есть {len(successful_payments)} успешный(ых) платеж(ей) "
                f"на сумму {total_amount:.2f} RUB (Payment IDs: {', '.join(payment_ids[:5])}"
                f"{'...' if len(payment_ids) > 5 else ''})"
            )
        
        # 3. Проверяем активные ключи (связанные с активными подписками)
        cursor.execute("""
            SELECT COUNT(*)
            FROM keys k
            JOIN subscriptions s ON k.subscription_id = s.id
            WHERE k.user_id = ? AND s.expires_at > ? AND s.is_active = 1
        """, (user_id, now))
        active_outline_keys = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*)
            FROM v2ray_keys k
            JOIN subscriptions s ON k.subscription_id = s.id
            WHERE k.user_id = ? AND s.expires_at > ? AND s.is_active = 1
        """, (user_id, now))
        active_v2ray_keys = cursor.fetchone()[0]
        
        if active_outline_keys > 0 or active_v2ray_keys > 0:
            reasons.append(
                f"У пользователя есть активные ключи: {active_outline_keys} Outline, "
                f"{active_v2ray_keys} V2Ray"
            )
        
        can_delete = len(reasons) == 0
        return can_delete, reasons
        
    finally:
        conn.close()


def check_payment_can_be_deleted(payment_id: str, db_path: str) -> Tuple[bool, Optional[str]]:
    """
    Проверяет, можно ли безопасно удалить платеж.
    
    КРИТИЧНО: Платежи со статусом 'paid' или 'completed' НИКОГДА не могут быть удалены.
    Это защищает финансовую целостность данных.
    
    Args:
        payment_id: ID платежа (payment_id из таблицы payments)
        db_path: Путь к базе данных
        
    Returns:
        Tuple[can_delete, reason]
        can_delete: True если можно удалить, False если нельзя
        reason: Причина, почему нельзя удалить (если есть)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Получаем платеж
        cursor.execute("""
            SELECT id, user_id, status, amount
            FROM payments
            WHERE payment_id = ?
        """, (payment_id,))
        payment = cursor.fetchone()
        
        if not payment:
            return True, None  # Платеж не найден, можно "удалить" (ничего не произойдет)
        
        payment_db_id, user_id, status, amount = payment
        
        # КРИТИЧНО: Платежи со статусом 'paid' или 'completed' НИКОГДА не могут быть удалены
        if status in ('completed', 'paid'):
            amount_rub = amount / 100 if amount else 0
            return False, (
                f"Нельзя удалить платеж со статусом '{status}' (сумма: {amount_rub:.2f} RUB). "
                f"Успешные платежи не могут быть удалены для сохранения финансовой целостности данных."
            )
        
        return True, None
        
    finally:
        conn.close()

