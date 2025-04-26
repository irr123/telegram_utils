import asyncio
import os

from telethon import TelegramClient


async def main():
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

    async with TelegramClient("my", api_id, api_hash) as tg:
        await tg.send_message("me", "Hello, myself!")


if __name__ == "__main__":
    asyncio.run(main())
