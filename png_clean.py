#!/usr/bin/env python

import asyncio
import logging

from telegram import Bot

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

MY_USER_ID = 1
MY_TOKEN = "2"
MY_STICKER_SET = "3"


async def run(bot):
    res = await bot.get_sticker_set(MY_STICKER_SET + "_by_just_another_bot")
    for s in res.stickers[1:]:
        ok = await bot.delete_sticker_from_set(s.file_id)
        print(s.emoji, ok)


if __name__ == "__main__":

    async def main():
        async with Bot(MY_TOKEN) as bot:
            await run(bot)

    asyncio.run(main())
