#!/usr/bin/env python

import asyncio
import json
import logging
import os, sys

from telegram import Bot

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

MY_USER_ID = 1
MY_TOKEN = "2"
MY_STICKER_SET = "3"


async def run(bot):
    for infile in sys.argv[1:]:
        if 'Color' not in infile:
            continue
        if 'Dark' in infile:
            continue
        if 'Light' in infile:
            continue
        if 'Medium' in infile:
            continue

        splitted = os.path.split(infile)

        emoji = ""
        try:
            with open(splitted[0]+"/../metadata.json", "rb") as meta:
                emoji = json.load(meta)["glyph"]
        except Exception:
            with open(splitted[0]+"/../../metadata.json", "rb") as meta:
                emoji = json.load(meta)["glyph"]

        with open(infile, "rb") as sticker:
            print("->", infile, emoji)
            res = await bot.add_sticker_to_set(
            #res = await bot.create_new_sticker_set(
                MY_USER_ID,
                MY_STICKER_SET+"_by_just_another_bot",
                #MY_STICKER_SET,
                emoji,
                png_sticker=sticker
            )
            print(res)

async def check(bot):
    res = await bot.get_sticker_set(MY_STICKER_SET)
    print(res)


if __name__ == '__main__':
    async def main():
        async with Bot(MY_TOKEN) as bot:
            #await check(bot)
            await run(bot)

    asyncio.run(main())
