import asyncio
import datetime as dt
import json
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
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")

    async with TelegramClient("my", api_id, api_hash) as tg:
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
                "https://t.me/balkanoutdoor",
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
                "https://t.me/pokerbelgradaa",
                "https://t.me/sta_imas_beograd",
                "https://t.me/standup_beo",
                "https://t.me/tech_illumination",
                "https://t.me/volna_srbjia",
                "https://t.me/vstrechi_v_belgrade",
                "https://t.me/yc_connect",
                "https://t.me/zarko_tusic",
            ),
        )

        print("Started!")

        await tg.run_until_disconnected()


PROMPT_TEMPLATE = """**Role:** AI assistant for extracting scheduled events, current activities, and suggestions.

**Task:** Identify future scheduled events, current activities, or suggestions mentioned in the text. Extract or assign a date (YYYY-MM-DD) and create a brief summary for each item. Output ONLY a JSON list: `[{{"date": "YYYY-MM-DD", "summary": "Item Summary"}}, ...]` or an empty list `[]` if no relevant items are found.

**Reference Dates:**
* Now Date (use for "now", "сейчас", "today"): {now_date_iso}
* Weekend Start Date (use for "weekend", "выходные"): {weekend_start_date_iso}
* Reference Date for Year Inference: {current_date_formatted}

**Instructions:**

1.  **Identify Items:** Find mentions of:
    * Specific, scheduled **future events** with explicit dates (Day, Month).
    * **Current activities or suggestions** linked to terms like "now", "сейчас", "today".
    * **Activities or suggestions** linked to "weekend", "выходные".
2.  **Exclude:** Ignore *only* past events (with dates clearly before {now_date_iso}) and purely historical date references. *Do not* exclude items just because they use "now" or "weekend".
3.  **For Each Found Item:**
    * **a. Determine Date Source:** Check if the text provides an explicit Day-Month, uses keywords for "now" (like "now", "сейчас", "today"), or keywords for "weekend" (like "weekend", "выходные"). Prioritize explicit dates if available for a specific phrase.
    * **b. Assign Base Date:**
        * If an explicit Day-Month is found for the item: Use that specific Day-Month.
        * If "now"/"сейчас"/"today" keywords are associated with the item: Use the **Now Date** (`{now_date_iso}`). Determine the Day-Month from this date.
        * If "weekend"/"выходные" keywords are associated with the item: Use the **Weekend Start Date** (`{weekend_start_date_iso}`). Determine the Day-Month from this date.
        * If none of these apply (e.g., just a general statement with no time reference), skip the item.
    * **c. Infer Year (using Day-Month from step 3b):**
        * If an explicit year is mentioned in the text *for that specific item*, use it.
        * Otherwise, compare the item's Month-Day (from 3b) to **{current_month_day_formatted}**:
            * If the Month-Day is on or after **{current_month_day_formatted}**, use the current year: **{current_year}**.
            * If the Month-Day is before **{current_month_day_formatted}**, use the next year: **{next_year}**.
    * **d. Format Final Date:** Combine the Day-Month from step 3b and the Year from step 3c into **YYYY-MM-DD** format. Use the specific assigned dates (`{now_date_iso}`, `{weekend_start_date_iso}`) directly when applicable as the final date.
    * **e. Create Summary:** Write a brief, concise summary of the event, activity, or suggestion (e.g., "Cleanup action 'Zasuči rukave'", "Observe cauliflory", "Walk in Botanical Garden").
    * **f. Create JSON Object:** Structure as `{{"date": "YYYY-MM-DD", "summary": "Your summary"}}`.
4.  **Compile List:** Collect all JSON objects from step 3f into a single JSON list.
5.  **Output:**
    * If items were found, output the JSON list. Example: `[ {{"date": "{now_date_iso}", "summary": "Observe cauliflory"}}, {{"date": "{weekend_start_date_iso}", "summary": "Walk in Botanical Garden"}}, {{"date": "{current_year}-07-07", "summary": "Belgrade-Subotica railway opening"}} ]`
    * If no items were found, output an empty JSON list: `[]`.
    * **Output ONLY the JSON list or `[]`, nothing else.**"""  # noqa: E501


def make_prompt(date: dt.datetime) -> str:
    base_dt = date.date()

    days_until_saturday = (5 - base_dt.weekday() + 7) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    weekend_start = base_dt + dt.timedelta(days=days_until_saturday)

    return PROMPT_TEMPLATE.format(
        current_year=base_dt.year,
        next_year=base_dt.year + 1,
        current_date_formatted=base_dt.strftime("%B %d, %Y"),
        current_month_day_formatted=base_dt.strftime("%b %d"),
        now_date_iso=base_dt.isoformat(),
        weekend_start_date_iso=weekend_start.isoformat(),
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
