"""
Модуль для валидации входных данных
"""
import re
import sqlite3
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta

class ValidationError(Exception):
    """Исключение для ошибок валидации"""
    pass

class InputValidator:
    """Класс для валидации входных данных"""
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Валидация email адреса"""
        if not email:
            return False
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
    
    @staticmethod
    def validate_telegram_id(user_id: int) -> bool:
        """Валидация Telegram User ID"""
        return isinstance(user_id, int) and user_id > 0
    
    @staticmethod
    def validate_country(country: str) -> bool:
        """Валидация названия страны"""
        if not country or len(country.strip()) < 2:
            return False
        # Проверяем, что содержит только буквы, цифры, пробелы и специальные символы
        pattern = r'^[a-zA-Zа-яА-Я0-9\s\-\(\)\.]+$'
        return bool(re.match(pattern, country))
    
    @staticmethod
    def validate_protocol(protocol: str) -> bool:
        """Валидация VPN протокола"""
        valid_protocols = ['outline', 'v2ray']
        return protocol.lower() in valid_protocols
    
    @staticmethod
    def validate_tariff_name(name: str) -> bool:
        """Валидация названия тарифа"""
        if not name or len(name.strip()) < 2:
            return False
        # Проверяем, что содержит только буквы, цифры, пробелы и специальные символы
        pattern = r'^[a-zA-Zа-яА-Я0-9\s\-\(\)\.]+$'
        return bool(re.match(pattern, name))
    
    @staticmethod
    def validate_duration(duration_sec: int) -> bool:
        """Валидация длительности в секундах"""
        return isinstance(duration_sec, int) and duration_sec > 0
    
    @staticmethod
    def validate_price(price_rub: int) -> bool:
        """Валидация цены в рублях"""
        return isinstance(price_rub, int) and price_rub >= 0
    
    @staticmethod
    def validate_server_url(url: str) -> bool:
        """Валидация URL сервера"""
        if not url:
            return False
        pattern = r'^https?://[^\s/$.?#].[^\s]*$'
        return bool(re.match(pattern, url))
    
    @staticmethod
    def validate_cert_sha256(cert_hash: str) -> bool:
        """Валидация SHA256 хеша сертификата"""
        if not cert_hash:
            return False
        pattern = r'^[A-Fa-f0-9]{64}$'
        return bool(re.match(pattern, cert_hash))
    
    @staticmethod
    def validate_api_key(api_key: str) -> bool:
        """Валидация API ключа"""
        if not api_key:
            return False
        # Проверяем base64 формат
        pattern = r'^[A-Za-z0-9+/=]+$'
        return bool(re.match(pattern, api_key))
    
    @staticmethod
    def validate_uuid(uuid_str: str) -> bool:
        """Валидация UUID"""
        if not uuid_str:
            return False
        pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        return bool(re.match(pattern, uuid_str.lower()))
    
    @staticmethod
    def validate_key_id(key_id: str) -> bool:
        """Валидация ID ключа"""
        if not key_id:
            return False
        # Проверяем, что содержит только буквы, цифры и дефисы
        pattern = r'^[a-zA-Z0-9\-]+$'
        return bool(re.match(pattern, key_id))
    
    @staticmethod
    def validate_payment_id(payment_id: str) -> bool:
        """Валидация ID платежа"""
        if not payment_id:
            return False
        # Проверяем, что содержит только буквы, цифры и дефисы
        pattern = r'^[a-zA-Z0-9\-_]+$'
        return bool(re.match(pattern, payment_id))
    
    @staticmethod
    def sanitize_string(text: str, max_length: int = 255) -> str:
        """Очистка строки от потенциально опасных символов"""
        if not text:
            return ""
        # Удаляем HTML теги
        text = re.sub(r'<[^>]+>', '', text)
        # Удаляем управляющие символы
        text = re.sub(r'[\x00-\x1f\x7f]', '', text)
        # Обрезаем до максимальной длины
        return text.strip()[:max_length]
    
    @staticmethod
    def validate_sql_injection(text: str) -> bool:
        """Проверка на SQL инъекции"""
        if not text:
            return True
        # Список потенциально опасных SQL команд
        dangerous_patterns = [
            r'\b(union|select|insert|update|delete|drop|create|alter|exec|execute)\b',
            r'[\'";]',
            r'--',
            r'/\*.*\*/',
            r'xp_',
            r'sp_'
        ]
        
        text_lower = text.lower()
        for pattern in dangerous_patterns:
            if re.search(pattern, text_lower):
                return False
        return True

class DatabaseValidator:
    """Класс для валидации данных базы данных"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    def validate_user_exists(self, user_id: int) -> bool:
        """Проверка существования пользователя"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # Проверяем наличие ключей у пользователя
                cursor.execute("""
                    SELECT COUNT(*) FROM keys WHERE user_id = ?
                    UNION ALL
                    SELECT COUNT(*) FROM v2ray_keys WHERE user_id = ?
                """, (user_id, user_id))
                results = cursor.fetchall()
                return any(count > 0 for count in results)
        except Exception:
            return False
    
    def validate_server_exists(self, server_id: int) -> bool:
        """Проверка существования сервера"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM servers WHERE id = ? AND active = 1", (server_id,))
                return cursor.fetchone()[0] > 0
        except Exception:
            return False
    
    def validate_tariff_exists(self, tariff_id: int) -> bool:
        """Проверка существования тарифа"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM tariffs WHERE id = ?", (tariff_id,))
                return cursor.fetchone()[0] > 0
        except Exception:
            return False
    
    def validate_key_exists(self, key_id: str, protocol: str = 'outline') -> bool:
        """Проверка существования ключа"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if protocol == 'outline':
                    cursor.execute("SELECT COUNT(*) FROM keys WHERE key_id = ?", (key_id,))
                else:
                    cursor.execute("SELECT COUNT(*) FROM v2ray_keys WHERE v2ray_uuid = ?", (key_id,))
                return cursor.fetchone()[0] > 0
        except Exception:
            return False
    
    def validate_payment_exists(self, payment_id: str) -> bool:
        """Проверка существования платежа"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM payments WHERE payment_id = ?", (payment_id,))
                return cursor.fetchone()[0] > 0
        except Exception:
            return False

class BusinessLogicValidator:
    """Класс для валидации бизнес-логики"""
    
    @staticmethod
    def validate_free_tariff_limit(user_id: int, db_path: str) -> Tuple[bool, str]:
        """Проверка лимита бесплатных тарифов"""
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                # Проверяем количество бесплатных ключей за последние 30 дней
                thirty_days_ago = int((datetime.now() - timedelta(days=30)).timestamp())
                
                cursor.execute("""
                    SELECT COUNT(*) FROM keys 
                    WHERE user_id = ? AND created_at > ? AND tariff_id = 1
                    UNION ALL
                    SELECT COUNT(*) FROM v2ray_keys 
                    WHERE user_id = ? AND created_at > ? AND tariff_id = 1
                """, (user_id, thirty_days_ago, user_id, thirty_days_ago))
                
                results = cursor.fetchall()
                total_free_keys = sum(count for count in results)
                
                if total_free_keys >= 1:
                    return False, "Превышен лимит бесплатных тарифов (1 в месяц)"
                return True, ""
        except Exception as e:
            return False, f"Ошибка проверки лимита: {str(e)}"
    
    @staticmethod
    def validate_key_expiry(key_id: str, protocol: str, db_path: str) -> Tuple[bool, str]:
        """Проверка срока действия ключа"""
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                now = int(datetime.now().timestamp())
                
                if protocol == 'outline':
                    cursor.execute("""
                        SELECT COALESCE(sub.expires_at, 0) as expiry_at FROM keys k
                        LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                        WHERE k.key_id = ?
                    """, (key_id,))
                else:
                    cursor.execute("""
                        SELECT COALESCE(sub.expires_at, 0) as expiry_at FROM v2ray_keys k
                        LEFT JOIN subscriptions sub ON k.subscription_id = sub.id
                        WHERE k.v2ray_uuid = ?
                    """, (key_id,))
                
                result = cursor.fetchone()
                if not result:
                    return False, "Ключ не найден"
                
                expiry_at = result[0]
                if expiry_at <= now:
                    return False, "Срок действия ключа истек"
                
                return True, ""
        except Exception as e:
            return False, f"Ошибка проверки срока действия: {str(e)}"
    
    @staticmethod
    def validate_server_capacity(server_id: int, db_path: str) -> Tuple[bool, str]:
        """Проверка загрузки сервера"""
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                
                # Получаем максимальное количество ключей для сервера
                cursor.execute("SELECT max_keys FROM servers WHERE id = ?", (server_id,))
                max_keys_result = cursor.fetchone()
                if not max_keys_result:
                    return False, "Сервер не найден"
                
                max_keys = max_keys_result[0] or 100
                
                # Подсчитываем активные ключи
                now = int(datetime.now().timestamp())
                cursor.execute("""
                    SELECT COUNT(*) FROM keys k
                    JOIN subscriptions sub ON k.subscription_id = sub.id
                    WHERE k.server_id = ? AND sub.expires_at > ?
                    UNION ALL
                    SELECT COUNT(*) FROM v2ray_keys k
                    JOIN subscriptions sub ON k.subscription_id = sub.id
                    WHERE k.server_id = ? AND sub.expires_at > ?
                """, (server_id, now, server_id, now))
                
                results = cursor.fetchall()
                total_active_keys = sum(count for count in results)
                
                if total_active_keys >= max_keys:
                    return False, f"Сервер переполнен (максимум {max_keys} ключей)"
                
                return True, ""
        except Exception as e:
            return False, f"Ошибка проверки загрузки сервера: {str(e)}"

# Создаем глобальные экземпляры валидаторов
input_validator = InputValidator()
try:
    from app.settings import settings as _settings
    _db_path = _settings.DATABASE_PATH
except Exception:
    # Fallback на дефолт, если настройки недоступны при импортном времени
    _db_path = "vpn.db"

db_validator = DatabaseValidator(_db_path)
business_validator = BusinessLogicValidator()

def validate_user_input(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Валидация пользовательского ввода"""
    errors = []
    
    # Валидация email
    if 'email' in data:
        email = data['email']
        if email and not input_validator.validate_email(email):
            errors.append("Неверный формат email адреса")
        if email and not input_validator.validate_sql_injection(email):
            errors.append("Email содержит недопустимые символы")
    
    # Валидация user_id
    if 'user_id' in data:
        user_id = data['user_id']
        if not input_validator.validate_telegram_id(user_id):
            errors.append("Неверный Telegram User ID")
    
    # Валидация страны
    if 'country' in data:
        country = data['country']
        if country and not input_validator.validate_country(country):
            errors.append("Неверное название страны")
    
    # Валидация протокола
    if 'protocol' in data:
        protocol = data['protocol']
        if protocol and not input_validator.validate_protocol(protocol):
            errors.append("Неверный VPN протокол")
    
    # Валидация названия тарифа
    if 'tariff_name' in data:
        tariff_name = data['tariff_name']
        if tariff_name and not input_validator.validate_tariff_name(tariff_name):
            errors.append("Неверное название тарифа")
    
    # Валидация длительности
    if 'duration_sec' in data:
        duration_sec = data['duration_sec']
        if not input_validator.validate_duration(duration_sec):
            errors.append("Неверная длительность тарифа")
    
    # Валидация цены
    if 'price_rub' in data:
        price_rub = data['price_rub']
        if not input_validator.validate_price(price_rub):
            errors.append("Неверная цена")
    
    return len(errors) == 0, errors

def sanitize_user_input(data: Dict[str, Any]) -> Dict[str, Any]:
    """Очистка пользовательского ввода"""
    sanitized = {}
    
    for key, value in data.items():
        if isinstance(value, str):
            sanitized[key] = input_validator.sanitize_string(value)
        else:
            sanitized[key] = value
    
    return sanitized


def is_valid_email(email: str) -> bool:
    """
    Валидация email адреса
    
    Args:
        email: Email адрес для проверки
    
    Returns:
        True если email валиден, False в противном случае
    """
    return input_validator.validate_email(email)