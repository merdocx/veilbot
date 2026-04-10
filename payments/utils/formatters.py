from typing import Optional
from datetime import datetime, timezone
from ..models.payment import Payment, PaymentStatus


class PaymentFormatters:
    """Форматтеры для платежного модуля"""
    
    @staticmethod
    def format_payment_message(
        payment: Payment,
        tariff_name: str,
        protocol_name: str,
        payment_url: Optional[str] = None
    ) -> str:
        """
        Форматирование сообщения о платеже
        
        Args:
            payment: Объект платежа
            tariff_name: Название тарифа
            protocol_name: Название протокола
            payment_url: URL для оплаты
            
        Returns:
            Отформатированное сообщение
        """
        amount_rub = payment.amount / 100  # Конвертируем копейки в рубли
        
        message = f"💳 *Платеж {protocol_name.upper()}*\n\n"
        message += f"📦 Тариф: *{tariff_name}*\n"
        message += f"💰 Сумма: *{amount_rub:.2f}₽*\n"
        
        if payment.email:
            message += f"📧 Email: `{payment.email}`\n"
        
        if payment.country:
            message += f"🌍 Страна: *{payment.country}*\n"
        
        message += f"📊 Статус: *{PaymentFormatters.format_payment_status(payment.status)}*\n"
        message += f"🆔 ID: `{payment.payment_id}`\n"
        
        if payment.created_at:
            message += f"📅 Создан: *{payment.created_at.strftime('%d.%m.%Y %H:%M')}*\n"
        
        if payment_url:
            message += f"\n🔗 [Ссылка для оплаты]({payment_url})"
        
        return message
    
    @staticmethod
    def format_payment_status(status: PaymentStatus) -> str:
        """
        Форматирование статуса платежа
        
        Args:
            status: Статус платежа
            
        Returns:
            Отформатированный статус
        """
        status_map = {
            PaymentStatus.PENDING: "⏳ Ожидает оплаты",
            PaymentStatus.PAID: "✅ Оплачен",
            PaymentStatus.FAILED: "❌ Неудачен",
            PaymentStatus.CANCELLED: "🚫 Отменен",
            PaymentStatus.REFUNDED: "↩️ Возвращен",
            PaymentStatus.EXPIRED: "⏰ Истек"
        }
        
        return status_map.get(status, str(status.value))
    
    @staticmethod
    def format_amount(amount: int, currency: str = "RUB") -> str:
        """
        Форматирование суммы
        
        Args:
            amount: Сумма в копейках
            currency: Валюта
            
        Returns:
            Отформатированная сумма
        """
        if currency == "RUB":
            rubles = amount / 100
            return f"{rubles:.2f}₽"
        elif currency == "USD":
            dollars = amount / 100
            return f"${dollars:.2f}"
        elif currency == "EUR":
            euros = amount / 100
            return f"€{euros:.2f}"
        else:
            return f"{amount} {currency}"
    
    @staticmethod
    def format_payment_receipt(payment: Payment, tariff_name: str) -> str:
        """
        Форматирование чека платежа
        
        Args:
            payment: Объект платежа
            tariff_name: Название тарифа
            
        Returns:
            Отформатированный чек
        """
        payment.amount / 100
        
        receipt = f"🧾 *Чек платежа*\n\n"
        receipt += f"📦 Товар: {tariff_name}\n"
        receipt += f"💰 Сумма: {PaymentFormatters.format_amount(payment.amount)}\n"
        receipt += f"📅 Дата: {payment.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        receipt += f"🆔 Номер: {payment.payment_id}\n"
        
        if payment.email:
            receipt += f"📧 Email: {payment.email}\n"
        
        receipt += f"📊 Статус: {PaymentFormatters.format_payment_status(payment.status)}"
        
        return receipt
    
    @staticmethod
    def format_payment_history(payments: list, page: int = 1, total_pages: int = 1) -> str:
        """
        Форматирование истории платежей
        
        Args:
            payments: Список платежей
            page: Текущая страница
            total_pages: Общее количество страниц
            
        Returns:
            Отформатированная история
        """
        if not payments:
            return "📋 *История платежей*\n\nНет платежей"
        
        history = f"📋 *История платежей* (стр. {page}/{total_pages})\n\n"
        
        for i, payment in enumerate(payments, 1):
            amount_rub = payment.amount / 100
            status_emoji = "✅" if payment.is_paid() else "⏳" if payment.is_pending() else "❌"
            
            history += f"{i}. {status_emoji} {amount_rub:.2f}₽ - {PaymentFormatters.format_payment_status(payment.status)}\n"
            history += f"   📅 {payment.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            history += f"   🆔 `{payment.payment_id[:8]}...`\n\n"
        
        return history
    
    @staticmethod
    def format_payment_statistics(stats: dict) -> str:
        """
        Форматирование статистики платежей
        
        Args:
            stats: Словарь со статистикой
            
        Returns:
            Отформатированная статистика
        """
        total_amount = stats.get('total_amount', 0) / 100  # Конвертируем в рубли
        
        statistics = f"📊 *Статистика платежей* (за {stats.get('period_days', 30)} дней)\n\n"
        statistics += f"📈 Всего платежей: *{stats.get('total_payments', 0)}*\n"
        statistics += f"✅ Успешных: *{stats.get('paid_payments', 0)}*\n"
        statistics += f"⏳ Ожидающих: *{stats.get('pending_payments', 0)}*\n"
        statistics += f"❌ Неудачных: *{stats.get('failed_payments', 0)}*\n"
        statistics += f"💰 Общая сумма: *{total_amount:.2f}₽*\n"
        statistics += f"📊 Успешность: *{stats.get('success_rate', 0):.1f}%*"
        
        return statistics
    
    @staticmethod
    def format_error_message(error: str, payment_id: Optional[str] = None) -> str:
        """
        Форматирование сообщения об ошибке
        
        Args:
            error: Текст ошибки
            payment_id: ID платежа (если есть)
            
        Returns:
            Отформатированное сообщение об ошибке
        """
        message = "❌ *Ошибка платежа*\n\n"
        message += f"🔍 Описание: {error}\n"
        
        if payment_id:
            message += f"🆔 ID платежа: `{payment_id}`\n"
        
        message += "\n💬 Обратитесь в поддержку, если проблема повторяется."
        
        return message
    
    @staticmethod
    def format_success_message(payment: Payment, tariff_name: str) -> str:
        """
        Форматирование сообщения об успешном платеже
        
        Args:
            payment: Объект платежа
            tariff_name: Название тарифа
            
        Returns:
            Отформатированное сообщение об успехе
        """
        amount_rub = payment.amount / 100
        
        message = f"🎉 *Платеж успешно завершен!*\n\n"
        message += f"📦 Тариф: *{tariff_name}*\n"
        message += f"💰 Сумма: *{amount_rub:.2f}₽*\n"
        message += f"🆔 ID: `{payment.payment_id}`\n"
        message += f"📅 Дата: *{payment.paid_at.strftime('%d.%m.%Y %H:%M')}*\n\n"
        message += "🔑 Теперь вы можете получить VPN ключ!"
        
        return message
    
    @staticmethod
    def format_payment_method(method: str) -> str:
        """
        Форматирование метода оплаты
        
        Args:
            method: Метод оплаты
            
        Returns:
            Отформатированный метод
        """
        method_map = {
            "card": "💳 Банковская карта",
            "sbp": "📱 СБП",
            "wallet": "💰 Электронный кошелек",
            "bank_transfer": "🏦 Банковский перевод"
        }
        
        return method_map.get(method, method)
    
    @staticmethod
    def format_currency(currency: str) -> str:
        """
        Форматирование валюты
        
        Args:
            currency: Код валюты
            
        Returns:
            Отформатированная валюта
        """
        currency_map = {
            "RUB": "🇷🇺 Рубли (₽)",
            "USD": "🇺🇸 Доллары ($)",
            "EUR": "🇪🇺 Евро (€)"
        }
        
        return currency_map.get(currency, currency)
    
    @staticmethod
    def format_timestamp(timestamp: datetime) -> str:
        """
        Форматирование временной метки
        
        Args:
            timestamp: Временная метка
            
        Returns:
            Отформатированное время
        """
        now = datetime.now(timezone.utc)
        diff = now - timestamp
        
        if diff.days > 0:
            return f"{diff.days} дн. назад"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} ч. назад"
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f"{minutes} мин. назад"
        else:
            return "Только что"
