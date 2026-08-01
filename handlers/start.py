from aiogram import Router
from aiogram.filters import CommandStart

router_start = Router()


@router_start.message(CommandStart())
async def command_start_handler(message):
    await message.answer(f"Привет, <b>{message.from_user.full_name}</b>! Как дела?")
