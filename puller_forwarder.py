import asyncio
import datetime as dt
import os

import google
from google.generativeai.types.generation_types import GenerateContentResponse
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from telethon import TelegramClient, events, tl


class Gemini:
    def __init__(self, api_key: str):
        assert api_key
        google.generativeai.configure(api_key=api_key)

    async def complete(
        self,
        user_prompt: str,
        sys_prompt: str | None = None,
        model: str = "gemini-2.0-flash",
    ) -> tuple[GenerateContentResponse, str]:
        m = google.generativeai.GenerativeModel(model, system_instruction=sys_prompt)

        try:
            resp = await m.generate_content_async(
                user_prompt,
                safety_settings="block_none",
                generation_config=google.generativeai.GenerationConfig(
                    candidate_count=1,
                    temperature=0.0,
                ),
            )
        except google.api_core.exceptions.NotFound as exc:
            try:
                models = google.generativeai.list_models()
            except Exception:
                pass
            else:
                print(f"Available list of models: {[m.name for m in models]}")
            raise exc

        return resp, resp.text.strip()


class Calendar:
    SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
    SERVICE_ACCOUNT_FILE = "./credentials.json"

    def __init__(self, cal_id: str):
        assert cal_id
        self._cal_id = cal_id

        creds = Credentials.from_service_account_file(
            self.SERVICE_ACCOUNT_FILE, scopes=self.SCOPES
        )
        self._client = build("calendar", "v3", credentials=creds)

    async def publish(self, ev_date: dt.datetime, summary: str, link: str):
        ev = {
            "summary": summary,
            "description": f"Source: {link}",
            "start": {
                "date": ev_date.date().isoformat(),
                "timeZone": "UTC",
            },
            "end": {
                "date": (ev_date.date() + dt.timedelta(days=1)).isoformat(),
                "timeZone": "UTC",
            },
        }

        req = self._client.events().insert(calendarId=self._cal_id, body=ev)
        return await asyncio.to_thread(req.execute)


async def main():
    calendar = Calendar(os.getenv("CALENDAR_ID"))
    gemini = Gemini(os.getenv("GEMINI_API_KEY"))
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

    async with TelegramClient("my", api_id, api_hash) as tg:
        await _main(
            tg,
            gemini,
            calendar,
            "https://t.me/belgrade_aggregated",
            (
                "https://t.me/m2Rb4gv9J8J5",
                "https://t.me/debaty_belgrad",
                "https://t.me/ikonamitakikonami",
                "https://t.me/serbia_nalogi",
                "https://t.me/cofeek_vezde",
                "https://t.me/Serbia",
                "https://t.me/zarko_tusic",
                "https://t.me/SerbiaInMyMind",
                "https://t.me/mapamagrus",
                "https://t.me/kikirikirs",
                "https://t.me/CaoBeograd",
                "https://t.me/vstrechi_v_belgrade",
                "https://t.me/balkanoutdoor",
            ),
        )

        print("Started!")

        await tg.run_until_disconnected()


PROMPT = """**Role:** You are an AI assistant specialized in extracting specific date information from text posts.

**Task:** Analyze the provided text post to determine if it announces a specific, scheduled **future event** (like a lecture, meeting, workshop, etc.). Your goal is to extract the date of this event and format it strictly as YYYY-MM-DD.

**Instructions:**

1.  Read the text carefully.
2.  Identify if the primary purpose of the text is to announce a **future scheduled event** that people can attend or participate in. Look for indicators like explicit future dates, times, locations, registration details, calls to attend, etc.
3.  **Crucially distinguish** this from:
    * News reports about **past events** (even if they have dates). The date mentioned in a news report about something that *already happened* should NOT be extracted.
    * Mentions of dates related to historical context within the text (e.g., "In 1983...").
    * General discussions without a specific scheduled meeting date.
4.  If a **future scheduled event** is identified:
    * Determine the full date (Day, Month, Year) of the event.
    * **Year Inference:** If the year is not explicitly mentioned, infer it based on the current date (**April 26, 2025**). Assume it's the current year (2025) if the date hasn't passed yet, or the next year (2026) if the date has already passed in 2025 relative to the current date. If the text provides an explicit year for the future event, use that year.
    * Convert the identified month name (e.g., "апреля", "April") to its corresponding two-digit number (e.g., 04).
    * Format the final date strictly as **YYYY-MM-DD**.
    * Output **only** this date string and nothing else.
5.  If the text **does not** announce a future scheduled event (it's a news report about a past event, historical info, general discussion, etc.):
    * Output the exact string: **N/A**

**Input Text Post:**"""  # noqa: E501


async def _main(
    tg: TelegramClient,
    gemini: Gemini,
    calendar: Calendar,
    dst: str,
    src: tuple[str],
):
    dest_entity = await tg.get_entity(dst)

    for source in src:
        source_entity = await tg.get_entity(source)

        @tg.on(events.NewMessage(chats=source_entity))
        async def handler(event: events.NewMessage.Event):
            message: tl.custom.message.Message = event.message
            sender = await message.get_sender()
            sender_name = getattr(sender, "username", "Unknown")

            print(f"{sender_name} 1: {message.message}")
            _, completion = await gemini.complete(message.message, PROMPT)
            print(f"{sender_name} 2: {completion}")
            try:
                ev_date = dt.datetime.strptime(completion, "%Y-%m-%d")
            except Exception as e:
                print(f"{sender_name}: {e}")
                return

            try:
                forwarded = await message.forward_to(dest_entity)
            except Exception as e:
                print(f"{sender_name}: {e}")
                return

            summary = f"{sender_name}: {message.message[:32]}"
            link = f"https://t.me/{dest_entity.username}/{forwarded.id}"

            try:
                ev = await calendar.publish(ev_date, summary, link)
            except Exception as e:
                print(f"{sender_name}: {e}")
                return

            print(f"{sender_name} 3: {ev}")


if __name__ == "__main__":
    asyncio.run(main())
