import asyncio
import datetime as dt
import json
import os

import httpx
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.custom import Message


class Ollama:
    def __init__(self, base_url):
        self.base_url = base_url

    async def complete(
        self,
        user_prompt: str,
        sys_prompt: str,
        model: str = "llama3.2:1b-instruct-q4_K_M",
    ) -> tuple[tuple[dict, dict], str]:
        payload = {
            "model": model,
            "system": sys_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
            },
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.base_url}/api/generate", json=payload)
            try:
                resp.raise_for_status()
            except Exception as e:
                raise Exception(resp.text) from e

            result = resp.json()

        text = result["response"].strip() or "[]"
        return (payload, result), text.removeprefix("```json").removesuffix("```")


class Calendar:
    SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
    S_ACCOUNT_FILE = "./credentials.json"

    def __init__(self, cal_id: str):
        self._cal_id = cal_id
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
        req = self._client.events().insert(calendarId=self._cal_id, body=ev)
        return await asyncio.to_thread(req.execute)


PROMPT_TEMPLATE = """Extract ONLY public participatory events from the text. Output as JSON list: [{{"date": "YYYY-MM-DD", "summary": "Brief title"}}]. Return [] if none.

CRITICAL RULES:
- MUST include: public access + clear attendance signal (e.g., "at [place]", "join", "starts at", "free entry", "visit").
- MUST exclude: services (coaching, advice), news, ads, donations, store closures, past events (< {now_date_iso}).
- Date resolution priority:
  1. Explicit date (e.g., "Dec 5")
  2. Relative day (e.g., "this Wednesday" → {wednesday_iso})
  3. "today" → {now_date_iso}
  4. "weekend" → {weekend_start_date_iso}
  5. No date? → SKIP
- Year inference: if month-day ≥ {current_month_day_formatted} → {current_year}, else {next_year}

RETURN ONLY VALID JSON. NO TEXT. NO EXPLANATIONS."""  # noqa: E501


def make_prompt(message_date: dt.datetime) -> str:
    base_dt = message_date.date()
    now_date_iso = base_dt.isoformat()
    current_month_day_formatted = base_dt.strftime("%b %d")
    current_year = base_dt.year
    next_year = base_dt.year + 1

    days_until_saturday = (5 - base_dt.weekday() + 7) % 7
    weekend_start = base_dt + dt.timedelta(days=days_until_saturday or 7)
    weekend_start_date_iso = weekend_start.isoformat()

    day_names = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    week_dates = {
        f"{day_names[i]}_iso": (
            base_dt + dt.timedelta(days=(i - base_dt.weekday() + 7) % 7)
        ).isoformat()
        for i in range(7)
    }

    return PROMPT_TEMPLATE.format(
        now_date_iso=now_date_iso,
        current_month_day_formatted=current_month_day_formatted,
        current_year=current_year,
        next_year=next_year,
        weekend_start_date_iso=weekend_start_date_iso,
        **week_dates,
    )


async def setup(
    tg: TelegramClient,
    llm: Ollama,
    calendar: Calendar,
    dst: str,
    src: tuple[str, ...],
):
    dest_entity = await tg.get_entity(dst)
    dest_username = getattr(dest_entity, "username", "")

    for source in src:
        source_entity = await tg.get_entity(source)

        @tg.on(events.NewMessage(chats=source_entity))
        async def handler(event: events.NewMessage.Event):
            message: Message = event.message
            if not message.message:
                return

            sender = await message.get_sender()
            sender_name = getattr(sender, "username", None)
            sender_name = sender_name or getattr(sender, "title", "Unknown")

            print(f"{sender_name} 1: {message.message}")
            prompt = make_prompt(message.date or dt.datetime.now())
            resp, completion = await llm.complete(message.message, prompt)
            print(f"{sender_name} 2: {resp}")

            try:
                events_list: list[dict[str, str]] = json.loads(completion)
            except Exception as e:
                print(f"{sender_name}: {e}")
                return

            if not events_list:
                return

            try:
                forwarded = await message.forward_to(dest_entity)
            except Exception as e:
                print(f"{sender_name}: {e}")
                return

            if isinstance(events_list, dict):
                events_list = [events_list]

            for ev in events_list:
                try:
                    ev_date = dt.datetime.strptime(ev["date"], "%Y-%m-%d")
                except Exception as e:
                    print(f"{sender_name}: {e}")
                    continue

                summary = ev.get("summary", f"{sender_name}: {message.message[:32]}")
                link = f"https://t.me/{dest_username}/{forwarded.id}"

                try:
                    ev_cal = await calendar.publish(ev_date, summary, link)
                except Exception as e:
                    print(f"{sender_name}: {e}")
                    continue

                print(f"{sender_name} 3: {ev_cal}")


async def main():
    cal_id = os.getenv("CALENDAR_ID")
    assert cal_id
    calendar = Calendar(cal_id)

    base_url = os.getenv("OLLAMA_HOST")
    assert base_url
    llm = Ollama(base_url)

    session_str = os.getenv("SESSION")
    assert session_str
    session = StringSession(session_str)

    api_id_str = os.getenv("TG_API_ID")
    assert api_id_str
    api_id = int(api_id_str)

    api_hash = os.getenv("TG_API_HASH")
    assert api_hash

    async with TelegramClient(session, api_id, api_hash) as tg:
        await setup(
            tg,
            llm,
            calendar,
            "https://t.me/belgrade_aggregated",
            (
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

        print("Started!")
        await tg.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
