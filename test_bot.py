from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

bot = Bot(token="7474256709:AAHi05xtaeVQkIteRoc00xMGmcEK6LUtnT4")
dp = Dispatcher(bot)

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    print(f"Получена команда /start от {message.from_user.id}")
    await message.answer("✅ Бот работает. Команда /start получена.")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
