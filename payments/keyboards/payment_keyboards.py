from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


class PaymentKeyboards:
    """Клавиатуры для платежного модуля"""
    
    @staticmethod
    def get_payment_keyboard(payment_url: str, payment_id: str) -> InlineKeyboardMarkup:
        """
        Клавиатура для оплаты
        
        Args:
            payment_url: URL для оплаты
            payment_id: ID платежа
            
        Returns:
            InlineKeyboardMarkup
        """
        keyboard = InlineKeyboardMarkup(row_width=1)
        
        # Кнопка оплаты
        keyboard.add(
            InlineKeyboardButton("💳 Оплатить", url=payment_url)
        )
        
        # Кнопка проверки статуса
        keyboard.add(
            InlineKeyboardButton("🔄 Проверить", callback_data=f"check_payment:{payment_id}")
        )
        
        return keyboard
    
    @staticmethod
    def get_payment_cancel_keyboard(payment_id: str) -> InlineKeyboardMarkup:
        """
        Клавиатура отмены платежа
        
        Args:
            payment_id: ID платежа
            
        Returns:
            InlineKeyboardMarkup
        """
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        keyboard.row(
            InlineKeyboardButton("✅ Подтвердить отмену", callback_data=f"confirm_cancel:{payment_id}"),
            InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_payment:{payment_id}")
        )
        
        return keyboard
    
    @staticmethod
    def get_payment_success_keyboard() -> InlineKeyboardMarkup:
        """
        Клавиатура успешного платежа
        
        Returns:
            InlineKeyboardMarkup
        """
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        keyboard.row(
            InlineKeyboardButton("🔑 Получить ключ", callback_data="get_key"),
            InlineKeyboardButton("📋 Мои ключи", callback_data="my_keys")
        )
        
        keyboard.add(
            InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")
        )
        
        return keyboard
    
    @staticmethod
    def get_payment_failed_keyboard(payment_id: str) -> InlineKeyboardMarkup:
        """
        Клавиатура неудачного платежа
        
        Args:
            payment_id: ID платежа
            
        Returns:
            InlineKeyboardMarkup
        """
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        keyboard.row(
            InlineKeyboardButton("🔄 Попробовать снова", callback_data=f"retry_payment:{payment_id}"),
            InlineKeyboardButton("💬 Поддержка", callback_data="support")
        )
        
        keyboard.add(
            InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")
        )
        
        return keyboard
    
    @staticmethod
    def get_payment_methods_keyboard() -> InlineKeyboardMarkup:
        """
        Клавиатура выбора метода оплаты
        
        Returns:
            InlineKeyboardMarkup
        """
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        keyboard.row(
            InlineKeyboardButton("💳 Банковская карта", callback_data="payment_method:card"),
            InlineKeyboardButton("📱 СБП", callback_data="payment_method:sbp")
        )
        
        keyboard.row(
            InlineKeyboardButton("💰 Электронный кошелек", callback_data="payment_method:wallet"),
            InlineKeyboardButton("🏦 Банковский перевод", callback_data="payment_method:bank_transfer")
        )
        
        keyboard.add(
            InlineKeyboardButton("🔙 Назад", callback_data="back_to_tariff")
        )
        
        return keyboard
    
    @staticmethod
    def get_payment_info_keyboard(payment_id: str) -> InlineKeyboardMarkup:
        """
        Клавиатура информации о платеже
        
        Args:
            payment_id: ID платежа
            
        Returns:
            InlineKeyboardMarkup
        """
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        keyboard.row(
            InlineKeyboardButton("📊 Статус", callback_data=f"payment_status:{payment_id}"),
            InlineKeyboardButton("📄 Чек", callback_data=f"payment_receipt:{payment_id}")
        )
        
        keyboard.row(
            InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_payment:{payment_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_payment:{payment_id}")
        )
        
        return keyboard
    
    @staticmethod
    def get_admin_payment_keyboard(payment_id: str) -> InlineKeyboardMarkup:
        """
        Админская клавиатура для управления платежом
        
        Args:
            payment_id: ID платежа
            
        Returns:
            InlineKeyboardMarkup
        """
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        keyboard.row(
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"admin_confirm_payment:{payment_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"admin_reject_payment:{payment_id}")
        )
        
        keyboard.row(
            InlineKeyboardButton("💰 Возврат", callback_data=f"admin_refund_payment:{payment_id}"),
            InlineKeyboardButton("📊 Детали", callback_data=f"admin_payment_details:{payment_id}")
        )
        
        return keyboard
    
    @staticmethod
    def get_payment_history_keyboard(user_id: int, page: int = 0) -> InlineKeyboardMarkup:
        """
        Клавиатура истории платежей
        
        Args:
            user_id: ID пользователя
            page: Номер страницы
            
        Returns:
            InlineKeyboardMarkup
        """
        keyboard = InlineKeyboardMarkup(row_width=3)
        
        # Навигация по страницам
        if page > 0:
            keyboard.add(
                InlineKeyboardButton("⬅️ Назад", callback_data=f"payment_history:{user_id}:{page-1}")
            )
        
        keyboard.add(
            InlineKeyboardButton("🔄 Обновить", callback_data=f"payment_history:{user_id}:{page}")
        )
        
        keyboard.add(
            InlineKeyboardButton("➡️ Далее", callback_data=f"payment_history:{user_id}:{page+1}")
        )
        
        keyboard.add(
            InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")
        )
        
        return keyboard
