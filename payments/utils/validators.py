import re
import logging
from typing import Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class PaymentValidators:
    """Валидаторы для платежного модуля"""
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """
        Валидация email адреса
        
        Args:
            email: Email для проверки
            
        Returns:
            True если email валиден
        """
        if not email:
            return False
        
        # Простая проверка формата email
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
    
    @staticmethod
    def validate_amount(amount: int) -> Tuple[bool, str]:
        """
        Валидация суммы платежа
        
        Args:
            amount: Сумма в копейках
            
        Returns:
            Tuple[валидность, сообщение об ошибке]
        """
        if amount <= 0:
            return False, "Сумма должна быть больше нуля"
        
        if amount > 100000000:  # 1 миллион рублей
            return False, "Сумма слишком большая"
        
        return True, ""
    
    @staticmethod
    def validate_payment_id(payment_id: str) -> bool:
        """
        Валидация ID платежа
        
        Args:
            payment_id: ID платежа
            
        Returns:
            True если ID валиден
        """
        if not payment_id:
            return False
        
        # Проверяем формат YooKassa ID
        pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        return bool(re.match(pattern, payment_id))
    
    @staticmethod
    def validate_currency(currency: str) -> bool:
        """
        Валидация валюты
        
        Args:
            currency: Код валюты
            
        Returns:
            True если валюта поддерживается
        """
        supported_currencies = ['RUB', 'USD', 'EUR']
        return currency.upper() in supported_currencies
    
    @staticmethod
    def validate_country_code(country: str) -> bool:
        """
        Валидация кода страны
        
        Args:
            country: Код страны
            
        Returns:
            True если код страны валиден
        """
        if not country:
            return True  # Пустая страна допустима
        
        # Проверяем формат ISO 3166-1 alpha-2
        pattern = r'^[A-Z]{2}$'
        return bool(re.match(pattern, country.upper()))
    
    @staticmethod
    def validate_protocol(protocol: str) -> bool:
        """
        Валидация VPN протокола
        
        Args:
            protocol: Название протокола
            
        Returns:
            True если протокол поддерживается
        """
        supported_protocols = ['outline', 'v2ray']
        return protocol.lower() in supported_protocols
    
    @staticmethod
    def validate_description(description: str) -> Tuple[bool, str]:
        """
        Валидация описания платежа
        
        Args:
            description: Описание платежа
            
        Returns:
            Tuple[валидность, сообщение об ошибке]
        """
        if not description:
            return False, "Описание не может быть пустым"
        
        if len(description) > 128:
            return False, "Описание слишком длинное (максимум 128 символов)"
        
        return True, ""
    
    @staticmethod
    def validate_phone(phone: str) -> bool:
        """
        Валидация номера телефона
        
        Args:
            phone: Номер телефона
            
        Returns:
            True если номер валиден
        """
        if not phone:
            return False
        
        # Убираем все нецифровые символы
        digits_only = re.sub(r'\D', '', phone)
        
        # Проверяем длину (7-15 цифр)
        if len(digits_only) < 7 or len(digits_only) > 15:
            return False
        
        return True
    
    @staticmethod
    def validate_card_number(card_number: str) -> bool:
        """
        Валидация номера карты (алгоритм Луна)
        
        Args:
            card_number: Номер карты
            
        Returns:
            True если номер карты валиден
        """
        if not card_number:
            return False
        
        # Убираем пробелы и дефисы
        card_number = re.sub(r'\s+|-', '', card_number)
        
        # Проверяем, что все символы цифры
        if not card_number.isdigit():
            return False
        
        # Проверяем длину (13-19 цифр)
        if len(card_number) < 13 or len(card_number) > 19:
            return False
        
        # Алгоритм Луна
        digits = [int(d) for d in card_number]
        odd_digits = digits[-1::-2]
        even_digits = digits[-2::-2]
        
        checksum = sum(odd_digits)
        for d in even_digits:
            checksum += sum(divmod(d * 2, 10))
        
        return checksum % 10 == 0
    
    @staticmethod
    def validate_expiry_date(expiry: str) -> bool:
        """
        Валидация срока действия карты
        
        Args:
            expiry: Срок действия в формате MM/YY
            
        Returns:
            True если срок действия валиден
        """
        if not expiry:
            return False
        
        # Проверяем формат
        pattern = r'^(0[1-9]|1[0-2])/([0-9]{2})$'
        if not re.match(pattern, expiry):
            return False
        
        month, year = expiry.split('/')
        month = int(month)
        year = int(year)
        
        # Проверяем месяц
        if month < 1 or month > 12:
            return False
        
        # Проверяем, что карта не истекла
        current_year = datetime.now().year % 100
        current_month = datetime.now().month
        
        if year < current_year or (year == current_year and month < current_month):
            return False
        
        return True
    
    @staticmethod
    def validate_cvv(cvv: str) -> bool:
        """
        Валидация CVV кода
        
        Args:
            cvv: CVV код
            
        Returns:
            True если CVV валиден
        """
        if not cvv:
            return False
        
        # CVV должен быть 3-4 цифры
        pattern = r'^[0-9]{3,4}$'
        return bool(re.match(pattern, cvv))
    
    @staticmethod
    def sanitize_input(text: str, max_length: int = 100) -> str:
        """
        Санитизация пользовательского ввода
        
        Args:
            text: Текст для очистки
            max_length: Максимальная длина
            
        Returns:
            Очищенный текст
        """
        if not text:
            return ""
        
        # Убираем лишние пробелы
        text = re.sub(r'\s+', ' ', text.strip())
        
        # Ограничиваем длину
        if len(text) > max_length:
            text = text[:max_length]
        
        return text
    
    @staticmethod
    def validate_webhook_signature(body: bytes, signature: str, secret_key: str) -> bool:
        """
        Валидация подписи webhook'а
        
        Args:
            body: Тело запроса
            signature: Подпись из заголовка
            secret_key: Секретный ключ
            
        Returns:
            True если подпись верна
        """
        try:
            import hmac
            import hashlib
            
            # Создаем подпись
            expected_signature = hmac.new(
                secret_key.encode('utf-8'),
                body,
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(signature, expected_signature)
            
        except Exception as e:
            logger.error(f"Error validating webhook signature: {e}")
            return False
