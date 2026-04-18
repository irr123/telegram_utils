import asyncio
import datetime as dt
import difflib
import json
import logging
import os
import sys
import typing as t
from collections import defaultdict

import google_auth_httplib2
import httplib2
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import HttpRequest
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.custom import Message


class StructuredFormatter(logging.Formatter):
    STANDARD_ATTRIBS = {
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

    def format(self, record):
        s = super().format(record)
        extra_data = {
            k: v for k, v in record.__dict__.items() if k not in self.STANDARD_ATTRIBS
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


PROMPT_TEMPLATE = """Extract public events from the message.
Include only real events people can attend with a specific date/time.

Return JSON only in exact format:
{{"events":[{{"date":"YYYY-MM-DD","summary":"short text"}}]}}

Rules:
- If there are no events, return {{"events":[]}}
- No markdown or extra text
- Summary must be short, clear, and English
- Use "Event @ Place" when place is obvious
- Date must be YYYY-MM-DD
- Today = {now_date_iso}
- Tomorrow = the next day after {now_date_iso}
- If a date has no year, use {current_year} unless already past, then use {next_year}"""


class AsyncRateLimiter:
    def __init__(self, min_interval: float = 1.0):
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._interval = min_interval

    async def acquire(self):
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._interval - (loop.time() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = loop.time()


class OpenAICompatibleLLM:
    BASE_URL = "https://opencode.ai/zen/v1"
    FALLBACK_MODELS = (
        "big-pickle",
        "gpt-5-nano",
        "minimax-m2.5-free",
        "mimo-v2-pro-free",
        "qwen3.6-plus-free",
        "nemotron-3-super-free",
    )
    MODEL = FALLBACK_MODELS[0]

    def __init__(self, api_key: str | None):
        self._api_key = api_key
        self._rate_limiter = AsyncRateLimiter()

    def _extract_json_from_text(self, text: str) -> dict[str, t.Any]:
        tmp_txt = text[:200] + "..." if len(text) > 200 else text
        logger.debug("Parsing model output", extra={"output": tmp_txt})
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            logger.warning("Direct JSON parse failed", extra={"output": tmp_txt})
            return {"events": []}

    async def _make_request(self, payload: dict[str, t.Any]) -> dict[str, t.Any] | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": "curl/8.7.1",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self.BASE_URL}/chat/completions"
        body = json.dumps(payload).encode("utf-8")

        def make_request():
            http = httplib2.Http()
            return http.request(url, method="POST", body=body, headers=headers)

        await self._rate_limiter.acquire()

        try:
            response, content = await asyncio.to_thread(make_request)
            if response.status != 200:
                extra = {
                    "status": response.status,
                    "resp_body": content.decode("utf-8", errors="replace")[:999],
                }
                logger.warning(f"HTTP {response.status} - switching model", extra=extra)
                return None

            try:
                data = json.loads(content.decode("utf-8"))
            except json.JSONDecodeError:
                extra = {"resp_body": content.decode("utf-8", errors="replace")[:999]}
                logger.warning("Invalid JSON response - switching model", extra=extra)
                return None

            if data.get("error"):
                extra = {"error": data["error"]}
                logger.warning("API error - switching model", extra=extra)
                return None

            return data

        except Exception as e:
            extra = {"error": str(e), "error_type": type(e).__name__}
            logger.warning("Request failed - switching model", extra=extra)
            return None

    async def _try_model(
        self,
        model: str,
        user_prompt: str,
        sys_prompt: str | None = None,
    ) -> list[dict] | None:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_prompt or ""},
                {"role": "user", "content": user_prompt},
            ],
        }
        extra = {"model": model, "url": f"{self.BASE_URL}/chat/completions"}
        logger.debug("Trying model", extra=extra)
        try:
            data = await self._make_request(payload)
            if not data:
                return None

            try:
                output_text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                logger.warning(f"Invalid response structure from {model}")
                return None

            parsed = self._extract_json_from_text(output_text)
            events = parsed.get("events", [])
            normalized = []
            for event in events:
                if not isinstance(event, dict):
                    continue
                date = event.get("date", "")
                if "T" in date:
                    date = date.split("T", 1)[0]
                summary = event.get("summary", "")
                if date and summary:
                    normalized.append({"date": date, "summary": summary})

            extra = {"model": model, "event_count": len(normalized)}
            logger.debug("Successfully extracted events", extra=extra)
            return normalized

        except Exception as e:
            logger.warning(f"Model {model} failed", extra={"error": str(e)})
            return None

    async def complete(
        self,
        user_prompt: str,
        sys_prompt: str | None = None,
    ) -> list[dict]:
        for i, model in enumerate(self.FALLBACK_MODELS):
            log_msg = f"Attempting model {i + 1}/{len(self.FALLBACK_MODELS)}: {model}"
            logger.debug(log_msg)
            result = await self._try_model(model, user_prompt, sys_prompt)
            if result is not None:
                extra = {"model": model, "event_count": len(result)}
                logger.info(f"Success with model: {model}", extra=extra)
                return result

        logger.error("All fallback models failed")
        return []


class Calendar:
    SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
    S_ACCOUNT_FILE = "./credentials.json"

    def __init__(self, cal_id: str | None):
        assert cal_id
        self._cal_id = cal_id
        if not os.path.exists(self.S_ACCOUNT_FILE):
            raise FileNotFoundError(f"Missing credentials file: {self.S_ACCOUNT_FILE}")

        self._creds = Credentials.from_service_account_file(
            self.S_ACCOUNT_FILE,
            scopes=self.SCOPES,
        )
        self._client = self._build_service()
        self._rate_limiter = AsyncRateLimiter()

    @staticmethod
    def normalize_summary(summary: str) -> str:
        return " ".join(summary.lower().strip().split())

    @staticmethod
    def is_similar(a: str, b: str, threshold: float = 0.65) -> bool:
        return difflib.SequenceMatcher(None, a, b).ratio() >= threshold

    def _build_service(self):
        return build("calendar", "v3", credentials=self._creds)

    async def _execute(self, req: HttpRequest) -> dict:
        await self._rate_limiter.acquire()

        def run():
            http = google_auth_httplib2.AuthorizedHttp(
                self._creds, http=httplib2.Http()
            )
            return req.execute(http=http)

        return await asyncio.to_thread(run)

    async def get_existing_events(self, target_date: dt.datetime) -> set[str]:
        try:
            date_str = target_date.date().isoformat()
            next_date_str = (target_date.date() + dt.timedelta(days=1)).isoformat()

            req = self._client.events().list(
                calendarId=self._cal_id,
                timeMin=f"{date_str}T00:00:00Z",
                timeMax=f"{next_date_str}T00:00:00Z",
                singleEvents=True,
                orderBy="startTime",
            )
            result = await self._execute(req)

            existing_summaries = set()
            for event in result.get("items", []):
                if "summary" in event:
                    normalized = self.normalize_summary(event["summary"])
                    existing_summaries.add(normalized)

            extra = {
                "date": date_str,
                "count": len(existing_summaries),
                "summaries": list(existing_summaries),
            }
            logger.debug("Found existing events for date", extra=extra)
            return existing_summaries

        except Exception as e:
            extra = {"date": target_date.date().isoformat()}
            logger.warning(
                "Failed to fetch existing events, proceeding without deduplication",
                exc_info=e,
                extra=extra,
            )
            return set()

    async def _insert_event(self, ev: dict) -> str | None:
        req = self._client.events().insert(calendarId=self._cal_id, body=ev)
        result = await self._execute(req)
        return result.get("htmlLink")

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
            return await self._insert_event(ev)
        except Exception as e:
            logger.warning(
                "Calendar publish failed, rebuilding service and retrying",
                exc_info=e,
            )

            try:
                self._client = self._build_service()
                return await self._insert_event(ev)
            except Exception as retry_e:
                logger.error("Calendar publish error after retry", exc_info=retry_e)
                raise


def make_prompt(date: dt.datetime) -> str:
    base_dt = date.date()

    return PROMPT_TEMPLATE.format(
        current_year=base_dt.year,
        next_year=base_dt.year + 1,
        now_date_iso=base_dt.isoformat(),
    )


async def setup_bot(
    tg: TelegramClient,
    llm: OpenAICompatibleLLM,
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

        events_by_date = defaultdict(list)
        for ev in events_list:
            try:
                ev_date = dt.datetime.strptime(ev["date"], "%Y-%m-%d")
                events_by_date[ev_date].append(ev)
            except Exception as e:
                extra = {"event": ev}
                logger.error("Invalid event date format", exc_info=e, extra=extra)
                continue

        dates = list(events_by_date.keys())
        summaries_per_date = await asyncio.gather(
            *(calendar.get_existing_events(d) for d in dates)
        )

        all_unique_events = []
        for ev_date, existing_summaries in zip(dates, summaries_per_date, strict=True):
            for ev in events_by_date[ev_date]:
                normalized_summary = calendar.normalize_summary(ev["summary"])
                is_dup = any(
                    calendar.is_similar(normalized_summary, s)
                    for s in existing_summaries
                )
                if not is_dup:
                    all_unique_events.append((ev_date, ev))
                    existing_summaries.add(normalized_summary)

        if not all_unique_events:
            logger.info("No new events after dedup", extra={"sender": sender_name})
            return

        try:
            forwarded = await message.forward_to(dest_entity)
            if not forwarded:
                raise RuntimeError("Message was not forwarded")
            link = f"https://t.me/{dest_username}/{forwarded.id}"
        except Exception as e:
            logger.error("Forward message error", exc_info=e)
            return

        async def publish_one(ev_date, ev):
            try:
                summary = ev["summary"]
                cal_link = await calendar.publish(ev_date, summary, link)
                extra = {"summary": summary, "date": ev["date"], "link": cal_link}
                logger.info("Calendar publish success", extra=extra)
            except Exception as e:
                logger.error("Calendar publish error", exc_info=e, extra={"event": ev})

        await asyncio.gather(*(publish_one(d, ev) for d, ev in all_unique_events))

    logger.info("Bot started and listening...")
    await t.cast(t.Any, tg.run_until_disconnected())


async def main():
    cal_id = os.getenv("CALENDAR_ID")
    api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY")
    session_str = os.getenv("SESSION")
    assert session_str
    api_id = os.getenv("TG_API_ID")
    assert api_id
    api_hash = os.getenv("TG_API_HASH")
    assert api_hash

    calendar = Calendar(cal_id)
    llm = OpenAICompatibleLLM(api_key)
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
                "https://t.me/noda_space",
                "https://t.me/xecut_bg",
                "https://t.me/neka_beograd",
                "https://t.me/technoblok77",
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
