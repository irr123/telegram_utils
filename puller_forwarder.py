import asyncio
import datetime as dt
import json
import os

import google
from google.generativeai.types.generation_types import GenerateContentResponse
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from telethon import TelegramClient, events, tl
from telethon.sessions import StringSession


class Gemini:
    def __init__(self, api_key: str):
        assert api_key
        google.generativeai.configure(api_key=api_key)

    async def complete(
        self,
        user_prompt: str,
        sys_prompt: str | None = None,
        model: str = "gemini-2.5-flash-lite",
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
    SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
    S_ACCOUNT_FILE = "./credentials.json"

    def __init__(self, cal_id: str):
        assert cal_id
        self._cal_id = cal_id

        creds_builder = Credentials.from_service_account_file
        creds = creds_builder(self.S_ACCOUNT_FILE, scopes=self.SCOPES)
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
    session = StringSession(os.getenv("SESSION"))
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

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


PROMPT_TEMPLATE = """**Role:** AI assistant for extracting *public, participatory events* from text.

**Task:** Identify publicly accessible, participatory events mentioned in the text. These are activities where a person can physically or virtually attend. Extract or assign a date (YYYY-MM-DD) and create a brief summary for each event. Output ONLY a JSON list: `[{{"date": "YYYY-MM-DD", "summary": "Item Summary"}}, ...]` or an empty list `[]` if no events are found.

**Reference Dates:**
* Now Date (use for "now", "сейчас", "today"): {now_date_iso}
* Weekend Start Date (use for "weekend", "выходные"): {weekend_start_date_iso}
* Reference Date for Year Inference: {current_date_formatted}
* **Dates for This Week (use for "в понедельник", "on Tuesday", "в эту среду", etc.):**
  * This Monday: {monday_iso}
  * This Tuesday: {tuesday_iso}
  * This Wednesday: {wednesday_iso}
  * This Thursday: {thursday_iso}
  * This Friday: {friday_iso}
  * This Saturday: {saturday_iso}
  * This Sunday: {sunday_iso}

**Instructions:**

1.  **Criteria for a Participatory Event:** An item qualifies as a participatory event ONLY IF it meets these criteria:
    *   It is an activity open to the public (e.g., concert, exhibition, workshop, meetup, film screening, lecture, talk, performance, community action).
    *   It implies attendance. Look for indicators like a specific venue/location (e.g., "в Полете", "at the gallery"), registration details, ticket prices, or a clear call to join an activity with a host/performer at a certain place and time.

2.  **Identify Potential Items:** Find mentions of:
    *   Specific, scheduled **future events** with explicit dates that meet the criteria in step 1.
    *   Events mentioned with a day of the week (e.g., "this Wednesday").
    *   **Actionable** current activities or **participatory suggestions** (e.g., "go for a walk", "visit an exhibition") linked to "now", "сейчас", "today".
    *   **Actionable** activities or **participatory suggestions** linked to "weekend", "выходные".

3.  **Strictly Exclude:**
    *   **News & Announcements:** Exclude news reports and announcements about future political or economic actions (e.g., 'measures will be presented', 'an address will be made'). These are not participatory.
    *   **Informational Bulletins:** Exclude service disruptions or closures (e.g., 'a strike will begin on...', 'a road will be closed').
    *   **General Calls to Action:** Exclude non-event requests (donations, subscriptions).
    *   **Past Events:** Ignore events with dates clearly before {now_date_iso}.
    *   **Classifieds:** Exclude personal ads (e.g., 'cat looking for a home').
    *   **Summaries/Headlines:** Do not extract the summary of a news digest itself. Instead, look for qualifying events *within* the digest's list items.

4.  **For Each Found Item (that was NOT excluded):**
    *   **a. Determine Date Source & Assign Date (in order of priority):**
        *   1. **Explicit Day-Month:** If found, use that Day-Month.
        *   2. **Day of the Week:** If a day of the week is mentioned (e.g., "в среду", "on Friday"), **use the corresponding full date from the "Dates for This Week" reference list above.** For example, for "в эту среду", use the date provided for `{wednesday_iso}`.
        *   3. **"Now" Keywords:** If "now"/"сейчас"/"today" keywords are used, use the **Now Date** (`{now_date_iso}`).
        *   4. **"Weekend" Keywords:** If "weekend"/"выходные" keywords are used, use the **Weekend Start Date** (`{weekend_start_date_iso}`).
        *   *If none of the above time references are found, skip the item.*
    *   **b. Infer Year (only if using an explicit Day-Month from 4.a.1):**
        *   If an explicit year is mentioned for the item, use it.
        *   Otherwise, compare the item's Month-Day to **{current_month_day_formatted}**:
            *   If on or after **{current_month_day_formatted}**, use the current year: **{current_year}**.
            *   If before **{current_month_day_formatted}**, use the next year: **{next_year}**.
    *   **c. Format Final Date:** Combine the inferred parts into **YYYY-MM-DD** format. (Dates from steps 4.a.2, 4.a.3, and 4.a.4 are already fully formatted).
    *   **d. Create Summary:** Write a brief summary of the event (e.g., "«Серьёзный разговор» с Костей Широковым в Полете", "Film screening 'Les Enfants Terribles'").
    *   **e. Create JSON Object:** Structure as `{{"date": "YYYY-MM-DD", "summary": "Your summary"}}`.

5.  **Compile and Output:**
    *   Collect all valid JSON objects into a single list.
    *   **Output ONLY the JSON list or `[]`. Do not add any other text or explanations.**"""  # noqa: E501


def make_prompt(date: dt.datetime) -> str:
    base_dt = date.date()

    days_until_saturday = (5 - base_dt.weekday() + 7) % 7
    weekend_start = base_dt + dt.timedelta(
        days=days_until_saturday if days_until_saturday > 0 else 7
    )

    today_weekday = base_dt.weekday()  # Monday is 0, Sunday is 6
    week_dates = {}
    day_names = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    for i in range(7):
        days_to_add = (i - today_weekday + 7) % 7
        target_date = base_dt + dt.timedelta(days=days_to_add)
        week_dates[f"{day_names[i]}_iso"] = target_date.isoformat()

    return PROMPT_TEMPLATE.format(
        current_year=base_dt.year,
        next_year=base_dt.year + 1,
        current_date_formatted=base_dt.strftime("%B %d, %Y"),
        current_month_day_formatted=base_dt.strftime("%b %d"),
        now_date_iso=base_dt.isoformat(),
        weekend_start_date_iso=weekend_start.isoformat(),
        **week_dates,  # Unpack the dictionary with all the week dates
    )


async def setup(
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
            if not message or not message.message:
                return

            sender = await message.get_sender()
            sender_name = getattr(sender, "username", "Unknown")

            print(f"{sender_name} 1: {message.message}")
            prompt = make_prompt(message.date or dt.datetime.now())
            _, completion = await gemini.complete(message.message, prompt)
            completion = completion.removeprefix("```json").removesuffix("```")
            print(f"{sender_name} 2: {completion}")
            try:
                events = json.loads(completion)
            except Exception as e:
                print(f"{sender_name}: {e}")
                return

            if not events:
                return

            try:
                forwarded = await message.forward_to(dest_entity)
            except Exception as e:
                print(f"{sender_name}: {e}")
                return

            for event in events:
                try:
                    ev_date = dt.datetime.strptime(event["date"], "%Y-%m-%d")
                except Exception as e:
                    print(f"{sender_name}: {e}")
                    continue

                summary = event.get("summary", f"{sender_name}: {message.message[:32]}")
                link = f"https://t.me/{dest_entity.username}/{forwarded.id}"

                try:
                    ev = await calendar.publish(ev_date, summary, link)
                except Exception as e:
                    print(f"{sender_name}: {e}")
                    continue

                print(f"{sender_name} 3: {ev}")


if __name__ == "__main__":
    asyncio.run(main())
