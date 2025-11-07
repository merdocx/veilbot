"""
Модуль для расширенного логирования безопасности VeilBot
"""

import logging
import json
import hashlib
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
import os

DEFAULT_LOG_DIR = os.getenv("VEILBOT_LOG_DIR", "/var/log/veilbot")

@dataclass
class SecurityEvent:
    """Модель события безопасности"""
    timestamp: str
    event_type: str
    user_id: int
    action: str
    success: bool
    details: Dict[str, Any]
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    session_id: Optional[str] = None
    risk_score: int = 0

class SecurityLogger:
    """Класс для логирования событий безопасности"""
    
    def __init__(self, log_file: str = os.path.join(DEFAULT_LOG_DIR, "veilbot_security.log"), max_file_size: int = 10 * 1024 * 1024):
        self.log_file = log_file
        self.max_file_size = max_file_size
        
        # Настройка логгера безопасности
        self.logger = logging.getLogger('security')
        self.logger.setLevel(logging.INFO)
        
        # Создаем директорию для логов если её нет
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # Хендлер для файла
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        
        # Форматтер для структурированного логирования
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(formatter)
        
        # Очищаем существующие хендлеры и добавляем новый
        self.logger.handlers.clear()
        self.logger.addHandler(file_handler)
        
        # Кэш для отслеживания подозрительной активности
        self.suspicious_activity_cache = {}
        self.rate_limits = {
            'payment_attempts': {'limit': 5, 'window': 300},  # 5 попыток за 5 минут
            'failed_payments': {'limit': 3, 'window': 600},   # 3 неудачных за 10 минут
            'key_requests': {'limit': 10, 'window': 300},     # 10 запросов ключей за 5 минут
        }
    
    def _get_risk_score(self, event_type: str, user_id: int, action: str, details: Dict[str, Any]) -> int:
        """Вычисление оценки риска события"""
        risk_score = 0
        
        # Базовые риски по типу события
        risk_weights = {
            'payment_attempt': 10,
            'payment_failure': 20,
            'payment_success': 5,
            'key_creation': 15,
            'key_deletion': 10,
            'admin_action': 25,
            'suspicious_activity': 30,
            'rate_limit_exceeded': 40,
        }
        
        risk_score += risk_weights.get(event_type, 5)
        
        # Дополнительные факторы риска
        if details.get('amount', 0) > 10000:  # Сумма больше 1000 рублей
            risk_score += 10
        
        if details.get('protocol') == 'v2ray':  # V2Ray протокол
            risk_score += 5
        
        if details.get('country') not in ['RU', 'KZ', 'BY', 'UA']:  # Необычная страна
            risk_score += 15
        
        # Проверка истории пользователя
        user_history = self.suspicious_activity_cache.get(user_id, {})
        recent_failures = user_history.get('recent_failures', 0)
        if recent_failures > 2:
            risk_score += recent_failures * 10
        
        return min(risk_score, 100)  # Максимальный риск 100
    
    def _check_rate_limit(self, user_id: int, action_type: str) -> bool:
        """Проверка лимитов запросов"""
        now = datetime.now().timestamp()
        user_key = f"{user_id}_{action_type}"
        
        if user_key not in self.suspicious_activity_cache:
            self.suspicious_activity_cache[user_key] = {
                'timestamps': [],
                'count': 0
            }
        
        user_data = self.suspicious_activity_cache[user_key]
        limit_config = self.rate_limits.get(action_type, {'limit': 10, 'window': 300})
        
        # Удаляем старые записи
        user_data['timestamps'] = [
            ts for ts in user_data['timestamps'] 
            if now - ts < limit_config['window']
        ]
        
        # Проверяем лимит
        if len(user_data['timestamps']) >= limit_config['limit']:
            return False
        
        # Добавляем новую запись
        user_data['timestamps'].append(now)
        user_data['count'] = len(user_data['timestamps'])
        
        return True
    
    def log_payment_attempt(self, user_id: int, amount: int, protocol: str, 
                          country: Optional[str] = None, email: Optional[str] = None,
                          success: bool = True, error: Optional[str] = None,
                          ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> None:
        """Логирование попытки платежа"""
        
        # Проверяем лимиты
        rate_limit_ok = self._check_rate_limit(user_id, 'payment_attempts')
        if not rate_limit_ok:
            self.log_suspicious_activity(
                user_id, "rate_limit_exceeded", 
                f"Превышен лимит попыток платежей: {user_id}",
                ip_address, user_agent
            )
        
        # Создаем событие
        event = SecurityEvent(
            timestamp=datetime.now().isoformat(),
            event_type='payment_attempt',
            user_id=user_id,
            action='payment_creation',
            success=success,
            details={
                'amount': amount,
                'protocol': protocol,
                'country': country,
                'email': email,
                'error': error,
                'rate_limit_ok': rate_limit_ok
            },
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        # Вычисляем риск
        event.risk_score = self._get_risk_score(
            event.event_type, event.user_id, event.action, event.details
        )
        
        # Логируем событие
        self._log_event(event)
        
        # Если высокий риск, логируем как подозрительную активность
        if event.risk_score > 50:
            self.log_suspicious_activity(
                user_id, "high_risk_payment", 
                f"Высокий риск платежа: {event.risk_score}",
                ip_address, user_agent
            )
    
    def log_payment_success(self, user_id: int, payment_id: str, amount: int, 
                          protocol: str, country: Optional[str] = None,
                          ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> None:
        """Логирование успешного платежа"""
        
        event = SecurityEvent(
            timestamp=datetime.now().isoformat(),
            event_type='payment_success',
            user_id=user_id,
            action='payment_completion',
            success=True,
            details={
                'payment_id': payment_id,
                'amount': amount,
                'protocol': protocol,
                'country': country
            },
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        event.risk_score = self._get_risk_score(
            event.event_type, event.user_id, event.action, event.details
        )
        
        self._log_event(event)
    
    def log_payment_failure(self, user_id: int, amount: int, protocol: str, 
                          error: str, country: Optional[str] = None,
                          ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> None:
        """Логирование неудачного платежа"""
        
        # Проверяем лимиты неудачных попыток
        rate_limit_ok = self._check_rate_limit(user_id, 'failed_payments')
        if not rate_limit_ok:
            self.log_suspicious_activity(
                user_id, "rate_limit_exceeded", 
                f"Превышен лимит неудачных платежей: {user_id}",
                ip_address, user_agent
            )
        
        event = SecurityEvent(
            timestamp=datetime.now().isoformat(),
            event_type='payment_failure',
            user_id=user_id,
            action='payment_failure',
            success=False,
            details={
                'amount': amount,
                'protocol': protocol,
                'country': country,
                'error': error,
                'rate_limit_ok': rate_limit_ok
            },
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        event.risk_score = self._get_risk_score(
            event.event_type, event.user_id, event.action, event.details
        )
        
        self._log_event(event)
        
        # Обновляем счетчик неудач пользователя
        if user_id not in self.suspicious_activity_cache:
            self.suspicious_activity_cache[user_id] = {}
        
        self.suspicious_activity_cache[user_id]['recent_failures'] = \
            self.suspicious_activity_cache[user_id].get('recent_failures', 0) + 1
    
    def log_key_creation(self, user_id: int, key_id: str, protocol: str, 
                        server_id: int, tariff_id: int,
                        ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> None:
        """Логирование создания VPN ключа"""
        
        # Проверяем лимиты запросов ключей
        rate_limit_ok = self._check_rate_limit(user_id, 'key_requests')
        if not rate_limit_ok:
            self.log_suspicious_activity(
                user_id, "rate_limit_exceeded", 
                f"Превышен лимит запросов ключей: {user_id}",
                ip_address, user_agent
            )
        
        event = SecurityEvent(
            timestamp=datetime.now().isoformat(),
            event_type='key_creation',
            user_id=user_id,
            action='vpn_key_creation',
            success=True,
            details={
                'key_id': key_id,
                'protocol': protocol,
                'server_id': server_id,
                'tariff_id': tariff_id,
                'rate_limit_ok': rate_limit_ok
            },
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        event.risk_score = self._get_risk_score(
            event.event_type, event.user_id, event.action, event.details
        )
        
        self._log_event(event)
    
    def log_suspicious_activity(self, user_id: int, activity_type: str, details: str,
                              ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> None:
        """Логирование подозрительной активности"""
        
        event = SecurityEvent(
            timestamp=datetime.now().isoformat(),
            event_type='suspicious_activity',
            user_id=user_id,
            action=activity_type,
            success=False,
            details={
                'description': details,
                'activity_type': activity_type
            },
            ip_address=ip_address,
            user_agent=user_agent,
            risk_score=80  # Высокий риск для подозрительной активности
        )
        
        self._log_event(event)
        
        # Отправляем алерт администратору
        self._send_security_alert(event)
    
    def log_admin_action(self, admin_id: int, action: str, details: Dict[str, Any],
                        ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> None:
        """Логирование действий администратора"""
        
        event = SecurityEvent(
            timestamp=datetime.now().isoformat(),
            event_type='admin_action',
            user_id=admin_id,
            action=action,
            success=True,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
            risk_score=25  # Средний риск для админских действий
        )
        
        self._log_event(event)
    
    def _log_event(self, event: SecurityEvent) -> None:
        """Внутренний метод логирования события"""
        try:
            # Форматируем сообщение для логгера
            message = f"SECURITY_EVENT: {json.dumps(asdict(event), ensure_ascii=False)}"
            
            # Выбираем уровень логирования на основе риска
            if event.risk_score >= 80:
                self.logger.critical(message)
            elif event.risk_score >= 50:
                self.logger.warning(message)
            else:
                self.logger.info(message)
                
        except Exception as e:
            # Fallback логирование
            self.logger.error(f"Error logging security event: {e}")
    
    def _send_security_alert(self, event: SecurityEvent) -> None:
        """Отправка алерта о безопасности"""
        try:
            # Здесь можно добавить отправку уведомлений
            # Например, через Telegram бота администратору
            alert_message = (
                f"🚨 СИГНАЛИЗАЦИЯ БЕЗОПАСНОСТИ\n"
                f"Пользователь: {event.user_id}\n"
                f"Действие: {event.action}\n"
                f"Риск: {event.risk_score}/100\n"
                f"Детали: {event.details}\n"
                f"IP: {event.ip_address}\n"
                f"Время: {event.timestamp}"
            )
            
            # Пока просто логируем алерт
            self.logger.critical(f"SECURITY_ALERT: {alert_message}")
            
        except Exception as e:
            self.logger.error(f"Error sending security alert: {e}")
    
    def get_user_risk_profile(self, user_id: int) -> Dict[str, Any]:
        """Получение профиля риска пользователя"""
        user_data = self.suspicious_activity_cache.get(user_id, {})
        
        return {
            'user_id': user_id,
            'recent_failures': user_data.get('recent_failures', 0),
            'rate_limits': {
                action: {
                    'count': self.suspicious_activity_cache.get(f"{user_id}_{action}", {}).get('count', 0),
                    'limit': config['limit']
                }
                for action, config in self.rate_limits.items()
            },
            'risk_level': 'high' if user_data.get('recent_failures', 0) > 2 else 'normal'
        }
    
    def cleanup_old_data(self) -> None:
        """Очистка старых данных из кэша"""
        now = datetime.now().timestamp()
        
        # Очищаем старые записи из кэша
        keys_to_remove = []
        for key, data in self.suspicious_activity_cache.items():
            if 'timestamps' in data:
                # Удаляем записи старше 1 часа
                data['timestamps'] = [
                    ts for ts in data['timestamps'] 
                    if now - ts < 3600
                ]
                if not data['timestamps']:
                    keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self.suspicious_activity_cache[key]

# Глобальный экземпляр логгера безопасности
security_logger = SecurityLogger()

# Функции для удобного использования
def log_payment_attempt(user_id: int, amount: int, protocol: str, **kwargs):
    """Логирование попытки платежа"""
    security_logger.log_payment_attempt(user_id, amount, protocol, **kwargs)

def log_payment_success(user_id: int, payment_id: str, amount: int, protocol: str, **kwargs):
    """Логирование успешного платежа"""
    security_logger.log_payment_success(user_id, payment_id, amount, protocol, **kwargs)

def log_payment_failure(user_id: int, amount: int, protocol: str, error: str, **kwargs):
    """Логирование неудачного платежа"""
    security_logger.log_payment_failure(user_id, amount, protocol, error, **kwargs)

def log_key_creation(user_id: int, key_id: str, protocol: str, server_id: int, tariff_id: int, **kwargs):
    """Логирование создания VPN ключа"""
    security_logger.log_key_creation(user_id, key_id, protocol, server_id, tariff_id, **kwargs)

def log_suspicious_activity(user_id: int, activity_type: str, details: str, **kwargs):
    """Логирование подозрительной активности"""
    security_logger.log_suspicious_activity(user_id, activity_type, details, **kwargs)
