import asyncio
import datetime as dt
import json
import logging
import os
import sys

import google.generativeai as genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.custom import Message


class StructuredFormatter(logging.Formatter):
    def format(self, record):
        s = super().format(record)
        standard_attribs = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
        }
        extra_data = {
            k: v for k, v in record.__dict__.items() if k not in standard_attribs
        }

        if extra_data:
            try:
                json_extras = json.dumps(extra_data, default=repr, ensure_ascii=False)
                s += f" | {json_extras}"
            except Exception:
                s += f" | {extra_data}"

        return s


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(
    StructuredFormatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)

logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("app")


PROMPT_TEMPLATE = """### CONTEXT (Current Date & Reference)
- **Current Date:** {now_date_iso}
- **Week Context:**
  - Monday: {monday_iso}
  - Tuesday: {tuesday_iso}
  - Wednesday: {wednesday_iso}
  - Thursday: {thursday_iso}
  - Friday: {friday_iso}
  - Saturday: {saturday_iso} (Weekend Start)
  - Sunday: {sunday_iso}

### GOAL
Extract **public, participatory events** from the provided text. Return a list of events. If no events are found, return an empty list.

### CRITICAL FILTERS (Strictly Apply)
1. **INCLUDE ONLY:**
   - Events with a specific start time/date where a human can physically go or join online.
   - Examples: Concerts, meetups, workshops, screenings, stand-up comedy, exhibitions, lectures, guided tours.
   - **Key Signal:** Look for venues ("at Dorcol Platz"), times ("starts at 19:00"), or entry details ("tickets", "free entry").

2. **EXCLUDE (False Positives):**
   - **Services:** Resume reviews, coaching sessions, beauty salons, recurring yoga classes *without* a specific "special event" label.
   - **Announcements:** "New cafe opened", "Flight launched", "Strike details", "Museum is free on Sundays" (unless a specific Sunday is mentioned).
   - **Calls to Action:** "Donate now", "Subscribe to channel".
   - **Past Events:** Any date prior to {now_date_iso}.

### SUMMARY STYLE GUIDE
- **Language:** Keep original language (Russian/Serbian).
- **Format:** "Event Name @ Venue" or "Event Type: Description".
- **Length:** Max 7 words. No emojis.
- **Bad:** "We invite you to come join us for a wonderful evening of jazz at the club..."
- **Good:** "Jazz Night @ Blue Note Club"

### DATE LOGIC
- If "Today" -> {now_date_iso}
- If "Tomorrow" -> Calculate based on {now_date_iso}
- If "This Weekend" -> Use {saturday_iso}
- If specific date (e.g., "25.05") -> Use current year ({current_year}), unless date < {current_month_day_formatted}, then use {next_year}."""  # noqa: E501


class Gemini:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)

    async def complete(
        self,
        user_prompt: str,
        sys_prompt: str | None = None,
        model: str = "gemini-flash-lite-latest",
    ) -> list[dict]:
        response_schema = {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "date": {
                        "type": "STRING",
                        "description": "ISO 8601 date YYYY-MM-DD",
                    },
                    "summary": {
                        "type": "STRING",
                        "description": "Short title of the event",
                    },
                },
                "required": ["date", "summary"],
            },
        }

        m = genai.GenerativeModel(model_name=model, system_instruction=sys_prompt)
        try:
            resp = await m.generate_content_async(
                user_prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    candidate_count=1,
                    temperature=0.0,
                ),
            )
            return json.loads(resp.text)
        except Exception as e:
            logger.warning("Gemini API error", exc_info=e)
            return []


class Calendar:
    SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
    S_ACCOUNT_FILE = "./credentials.json"

    def __init__(self, cal_id: str):
        self._cal_id = cal_id
        if not os.path.exists(self.S_ACCOUNT_FILE):
            raise FileNotFoundError(f"Missing credentials file: {self.S_ACCOUNT_FILE}")

        creds = Credentials.from_service_account_file(
            self.S_ACCOUNT_FILE,
            scopes=self.SCOPES,
        )
        self._client = build("calendar", "v3", credentials=creds)

    async def publish(self, ev_date: dt.datetime, summary: str, link: str):
        ev = {
            "summary": summary,
            "description": f"Source: {link}",
            "start": {"date": ev_date.date().isoformat(), "timeZone": "UTC"},
            "end": {
                "date": (ev_date.date() + dt.timedelta(days=1)).isoformat(),
                "timeZone": "UTC",
            },
        }
        try:
            req = self._client.events().insert(calendarId=self._cal_id, body=ev)
            result = await asyncio.to_thread(req.execute)
            return result.get("htmlLink")
        except Exception as e:
            logger.error("Calendar publish error", exc_info=e)
            raise


def make_prompt(date: dt.datetime) -> str:
    base_dt = date.date()
    today_weekday = base_dt.weekday()

    day_names = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    week_dates = {
        f"{day_names[i]}_iso": (
            base_dt + dt.timedelta(days=(i - today_weekday + 7) % 7)
        ).isoformat()
        for i in range(7)
    }

    return PROMPT_TEMPLATE.format(
        current_year=base_dt.year,
        next_year=base_dt.year + 1,
        current_month_day_formatted=base_dt.strftime("%m-%d"),
        now_date_iso=base_dt.isoformat(),
        **week_dates,
    )


async def setup_bot(
    tg: TelegramClient,
    llm: Gemini,
    calendar: Calendar,
    dst: str,
    src: tuple[str, ...],
):
    dest_entity = await tg.get_entity(dst)
    dest_username = getattr(dest_entity, "username", "")
    source_entities = []
    for source in src:
        s_ent = await tg.get_entity(source)
        source_entities.append(s_ent)
        logger.info(f"Listening to: {getattr(s_ent, 'title', source)}")

    @tg.on(events.NewMessage(chats=source_entities))
    async def handler(event: events.NewMessage.Event):
        message: Message = event.message
        text = message.message
        if not text or not text.strip():
            return

        sender = await message.get_sender()
        sender_name = getattr(sender, "username", None)
        sender_name = sender_name or getattr(sender, "title", "Unknown")

        logger.info("Processing", extra={"sender": sender_name, "text": text})

        prompt = make_prompt(message.date or dt.datetime.now())
        events_list = await llm.complete(text, prompt)

        if not events_list:
            logger.info("No events found", extra={"sender": sender_name})
            return

        logger.info("Extracted", extra={"count": len(events_list), "data": events_list})

        try:
            forwarded = await message.forward_to(dest_entity)
            link = f"https://t.me/{dest_username}/{forwarded.id}"
        except Exception as e:
            logger.error("Forward message error", exc_info=e)
            return

        for ev in events_list:
            try:
                ev_date = dt.datetime.strptime(ev["date"], "%Y-%m-%d")
                summary = ev["summary"]

                cal_link = await calendar.publish(ev_date, summary, link)
                logger.info(
                    "Calendar publish success",
                    extra={"summary": summary, "date": ev["date"], "link": cal_link},
                )
            except Exception as e:
                logger.error("Calendar publish error", exc_info=e, extra={"event": ev})

    logger.info("Bot started and listening...")
    await tg.run_until_disconnected()


async def main():
    cal_id = os.getenv("CALENDAR_ID")
    assert cal_id
    api_key = os.getenv("GEMINI_API_KEY")
    assert api_key
    session_str = os.getenv("SESSION")
    assert session_str
    api_id = os.getenv("TG_API_ID")
    assert api_id
    api_hash = os.getenv("TG_API_HASH")
    assert api_hash

    calendar = Calendar(cal_id)
    llm = Gemini(api_key)
    session = StringSession(session_str)

    async with TelegramClient(session, int(api_id), api_hash) as tg:
        await setup_bot(
            tg,
            llm,
            calendar,
            dst="https://t.me/belgrade_aggregated",
            src=(
                "https://t.me/m2Rb4gv9J8J5",
                "https://t.me/CaoBeograd",
                "https://t.me/Serbia",
                "https://t.me/SerbiaInMyMind",
                "https://t.me/adaptacija",
                "https://t.me/afisha_rs",
                "https://t.me/airsoft_serbia",
                "https://t.me/balkanoutdoor",
                "https://t.me/beogradske_vesti",
                "https://t.me/cofeek_vezde",
                "https://t.me/debaty_belgrad",
                "https://t.me/dobardabar_books",
                "https://t.me/go_tara",
                "https://t.me/ikonamitakikonami",
                "https://t.me/kikirikirs",
                "https://t.me/legiongamesrs",
                "https://t.me/lepopishem",
                "https://t.me/mapamagrus",
                "https://t.me/obitaniya_sreda",
                "https://t.me/poker_belgrade",
                "https://t.me/sta_imas_beograd",
                "https://t.me/standup_beo",
                "https://t.me/tech_illumination",
                "https://t.me/volna_srbjia",
                "https://t.me/vstrechi_v_belgrade",
                "https://t.me/zarko_tusic",
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
