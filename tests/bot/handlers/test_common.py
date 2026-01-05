"""
Интеграционные тесты для bot/handlers/common.py
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from aiogram import types
from bot.handlers.common import (
    handle_invite_friend,
    handle_help,
    handle_support,
    handle_help_back,
    handle_apple_tv_instruction
)


class TestCommonHandlers:
    """Тесты для общих обработчиков"""
    
    @pytest.mark.asyncio
    async def test_handle_invite_friend_success(self, mock_message, mock_bot):
        """Тест обработки приглашения друга - успех"""
        with patch('bot.handlers.common.get_bot_instance', return_value=mock_bot):
            await handle_invite_friend(mock_message)
            
            # Проверяем, что было отправлено сообщение
            mock_message.answer.assert_called_once()
            call_args = mock_message.answer.call_args
            assert "Пригласите друга" in call_args[0][0]
            assert "https://t.me/test_bot?start=12345" in call_args[0][0]
    
    @pytest.mark.asyncio
    async def test_handle_invite_friend_bot_not_initialized(self, mock_message):
        """Тест обработки приглашения друга - бот не инициализирован"""
        with patch('bot.handlers.common.get_bot_instance', return_value=None):
            await handle_invite_friend(mock_message)
            
            mock_message.answer.assert_called_once()
            assert "Ошибка: бот не инициализирован" in mock_message.answer.call_args[0][0]
    
    @pytest.mark.asyncio
    async def test_handle_help(self, mock_message):
        """Тест обработки команды помощи"""
        await handle_help(mock_message)
        
        mock_message.answer.assert_called_once()
        help_text = mock_message.answer.call_args[0][0]
        assert "В данном меню вы можете" in help_text
        assert "инструкцию по подключению" in help_text.lower()
        assert "Связаться с поддержкой" in help_text
    
    @pytest.mark.asyncio
    async def test_handle_support_with_username(self, mock_message):
        """Тест обработки связи с поддержкой - используется @vee_support"""
        await handle_support(mock_message)
        
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        assert "@vee_support" in call_args[0][0]
        assert call_args[1]['reply_markup'] is not None
    
    @pytest.mark.asyncio
    async def test_handle_support_without_username(self, mock_message):
        """Тест обработки связи с поддержкой - всегда используется @vee_support"""
        await handle_support(mock_message)
        
        mock_message.answer.assert_called_once()
        assert "@vee_support" in mock_message.answer.call_args[0][0]
    
    @pytest.mark.asyncio
    async def test_handle_help_back(self, mock_message):
        """Тест возврата из помощи в главное меню"""
        await handle_help_back(mock_message)
        
        mock_message.answer.assert_called_once()
        assert "Главное меню" in mock_message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_apple_tv_instruction_without_image(self, mock_message, monkeypatch):
        """Фолбэк без изображения"""
        # Убеждаемся, что answer_photo является AsyncMock (важно установить ДО вызова функции)
        mock_message.answer_photo = AsyncMock(return_value=None)
        
        import bot.handlers.common as common_module
        # Мокируем метод exists() чтобы он всегда возвращал False
        # Это гарантирует, что код пойдет по ветке без изображения
        original_path = common_module.APPLE_TV_GUIDE_IMAGE_PATH
        mock_path = MagicMock()
        mock_path.exists = MagicMock(return_value=False)
        monkeypatch.setattr(common_module, "APPLE_TV_GUIDE_IMAGE_PATH", mock_path)
        
        await handle_apple_tv_instruction(mock_message)
        
        mock_message.answer.assert_called_once()
        assert "Изображение временно недоступно" in mock_message.answer.call_args[0][0]
        # answer_photo не должен быть вызван, так как файл не существует
        mock_message.answer_photo.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_apple_tv_instruction_with_image(self, mock_message, tmp_path, monkeypatch):
        """Отправка инструкции с изображением"""
        guide_path = tmp_path / "guide.png"
        # Пишем минимальный PNG-хедер, чтобы InputFile мог открыть файл
        guide_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        
        mock_message.answer_photo = AsyncMock(return_value=None)
        import bot.handlers.common as common_module
        monkeypatch.setattr(common_module, "APPLE_TV_GUIDE_IMAGE_PATH", guide_path)
        
        await handle_apple_tv_instruction(mock_message)
        
        mock_message.answer_photo.assert_called_once()
        mock_message.answer.assert_not_called()

