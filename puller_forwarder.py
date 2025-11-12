import asyncio
import datetime as dt
import json
import os

import google.api_core.exceptions
import google.generativeai as genai
from google.generativeai.types import generation_types
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.custom import Message


class Gemini:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)

    async def complete(
        self,
        user_prompt: str,
        sys_prompt: str | None = None,
        model: str = "gemini-2.5-flash-lite",
    ) -> tuple[generation_types.GenerateContentResponse, str]:
        m = genai.GenerativeModel(model_name=model, system_instruction=sys_prompt)

        try:
            resp = await m.generate_content_async(
                user_prompt,
                safety_settings="block_none",
                generation_config=genai.GenerationConfig(
                    candidate_count=1,
                    temperature=0.0,
                ),
            )
        except google.api_core.exceptions.NotFound as exc:
            try:
                models = list(genai.list_models())
            except Exception:
                pass
            else:
                print(f"Available models: {[m.name for m in models]}")
            raise exc

        return resp, resp.text.strip()  # type: ignore


class Calendar:
    SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
    S_ACCOUNT_FILE = "./credentials.json"

    def __init__(self, cal_id: str):
        creds = Credentials.from_service_account_file(
            self.S_ACCOUNT_FILE,
            scopes=self.SCOPES,
        )
        self._client = build("calendar", "v3", credentials=creds)
        self._cal_id = cal_id

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


PROMPT_TEMPLATE = """**Role:** AI assistant for extracting *public, participatory events* from text.

**Task:** Extract ONLY events where a person can physically or virtually attend. Output JSON list `[{{\"date\": \"YYYY-MM-DD\", \"summary\": \"Brief event description\"}}]`. Empty list `[]` if none.

**CRITICAL FILTERS — APPLY STRICTLY:**

1. **MUST HAVE:**
   - Open to public (concert, meetup, workshop, screening, lecture, game, walk, market, tour, performance).
   - **Clear attendance signal:** venue, time, registration, free/paid entry, "come", "join", "visit", "at [place]", "starts at [time]".

2. **MUST EXCLUDE:**
   - **Services/consultations** (career advice, resume help, coaching, even if recurring).
   - **General announcements** (flights start, building demolished, strike begins, museum free days *without specific event*).
   - **Calls to action without event** (donate, subscribe, support project).
   - **News summaries/headlines** — extract only *embedded events*.
   - **Past events** (before {now_date_iso}).
   - **Non-participatory** (store closing, landlord dispute, even with "visit today" — unless it's a public farewell event).

**DATE RESOLUTION (priority order):**

1. **Explicit full date** → use it.
2. **Day of week** ("this Wednesday", "в субботу") → use `{wednesday_iso}`, `{saturday_iso}`, etc. from list below.
3. **"today"/"now"/"сейчас"** → `{now_date_iso}`
4. **"weekend"/"выходные"** → `{weekend_start_date_iso}`
5. **No time reference** → **SKIP**

**YEAR INFERENCE (only for Day-Month):**
- Use explicit year if given.
- Else: if Month-Day ≥ `{current_month_day_formatted}` → `{current_year}`
- Else → `{next_year}`

**REFERENCE DATES:**
* Now: {now_date_iso}
* Weekend start: {weekend_start_date_iso}
* This week:
  - Mon: {monday_iso}
  - Tue: {tuesday_iso}
  - Wed: {wednesday_iso}
  - Thu: {thursday_iso}
  - Fri: {friday_iso}
  - Sat: {saturday_iso}
  - Sun: {sunday_iso}

**OUTPUT ONLY VALID JSON LIST. NO TEXT.**"""  # noqa: E501


def make_prompt(date: dt.datetime) -> str:
    base_dt = date.date()
    days_until_saturday = (5 - base_dt.weekday() + 7) % 7
    weekend_start = base_dt + dt.timedelta(days=days_until_saturday or 7)

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
        current_month_day_formatted=base_dt.strftime("%b %d"),
        now_date_iso=base_dt.isoformat(),
        weekend_start_date_iso=weekend_start.isoformat(),
        **week_dates,
    )


async def setup(
    tg: TelegramClient,
    gemini: Gemini,
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
            _, completion = await gemini.complete(message.message, prompt)
            completion = completion.removeprefix("```json").removesuffix("```").strip()
            print(f"{sender_name} 2: {completion}")

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

            for event_dict in events_list:
                try:
                    ev_date = dt.datetime.strptime(event_dict["date"], "%Y-%m-%d")
                except Exception as e:
                    print(f"{sender_name}: {e}")
                    continue

                summary = event_dict.get(
                    "summary", f"{sender_name}: {message.message[:32]}"
                )
                link = f"https://t.me/{dest_username}/{forwarded.id}"

                try:
                    ev = await calendar.publish(ev_date, summary, link)
                except Exception as e:
                    print(f"{sender_name}: {e}")
                    continue

                print(f"{sender_name} 3: {ev}")


async def main():
    cal_id = os.getenv("CALENDAR_ID")
    assert cal_id
    calendar = Calendar(cal_id)

    api_key = os.getenv("GEMINI_API_KEY")
    assert api_key
    gemini = Gemini(api_key)

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
            gemini,
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
