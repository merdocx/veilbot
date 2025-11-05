"""
Вспомогательные функции для операций с ключами (reissue, protocol change, country change)
"""
import asyncio
import time
import logging
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from config import PROTOCOLS, ADMIN_ID
from utils import get_db_cursor
from outline import create_key, delete_key
from vpn_protocols import ProtocolFactory
from bot.keyboards import get_main_menu, get_country_menu, get_countries_by_protocol
from bot.utils import format_key_message_unified

# Эти функции будут импортированы из bot.py и переданы через register_key_management_handlers
# Они слишком большие и тесно связаны с bot.py, поэтому пока оставляем их там

