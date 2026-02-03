import os
import json
import asyncio
from app.utils.config import settings
from telegram import Bot

TOKEN = settings.TELEGRAM_BOT_TOKEN
CHAT_ID = settings.TELEGRAM_OJ_CHATID

async def get_chat_id():
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    offset = 0
    while True:
        updates = await bot.get_updates(offset=offset)
        for update in updates:
            chat_id = update.message.chat.id
            print(f"Chat ID: {chat_id} (Username: {update.message.chat.username})")
            offset = update.update_id + 1
        await asyncio.sleep(1)  # Poll every second

async def send_message(text):
    bot = Bot(token=TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text=text)
