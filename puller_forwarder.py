import asyncio
import datetime as dt
import json
import logging
import os
import re
import sys
from typing import Any

import httplib2
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


PROMPT_TEMPLATE = """Extract public events from the message.

Ignore services, ads, generic announcements.
Include only real events people can attend with a specific date/time.

Return JSON only in exact format:
{{"events":[{{"date":"YYYY-MM-DD","summary":"short text"}}]}}

Rules:
- If there are no events, return {{"events":[]}}
- No markdown or extra text
- Summary must be short, clear, and in English
- Use "Event @ Place" when place is obvious
- Date must be YYYY-MM-DD
- Today = {now_date_iso}
- Tomorrow = the next day after {now_date_iso}
- If a date has no year, use {current_year} unless already past, then use {next_year}"""


class OpenAICompatibleLLM:
    BASE_URL = "https://opencode.ai/zen/v1"

    FALLBACK_MODELS = [
        "big-pickle",
        "gpt-5-nano",
        "minimax-m2.5-free",
        "mimo-v2-pro-free",
        "qwen3.6-plus-free",
        "nemotron-3-super-free",
    ]
    MODEL = FALLBACK_MODELS[0]

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._http = httplib2.Http()

    def _extract_json_from_text(self, text: str) -> dict[str, Any]:
        logger.debug(
            "Parsing model output",
            extra={"output": text[:200] + "..." if len(text) > 200 else text},
        )

        try:
            result = json.loads(text.strip())
            logger.debug("Direct JSON parse succeeded")
            return result
        except json.JSONDecodeError:
            logger.debug("Direct JSON parse failed")

        json_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        matches = re.findall(json_pattern, text, re.DOTALL | re.IGNORECASE)
        for match in matches:
            try:
                result = json.loads(match.strip())
                logger.debug("Markdown fence JSON parse succeeded")
                return result
            except json.JSONDecodeError:
                continue
        if matches:
            logger.debug("Markdown fence JSON parse failed")

        json_object_pattern = r'\{[^{}]*"events"[^{}]*\[[^\]]*\][^{}]*\}'
        matches = re.findall(json_object_pattern, text, re.DOTALL)
        for match in matches:
            try:
                result = json.loads(match.strip())
                logger.debug("Events object pattern parse succeeded")
                return result
            except json.JSONDecodeError:
                continue
        if matches:
            logger.debug("Events object pattern parse failed")

        array_pattern = r"\[(?:[^[\]]*\{[^{}]*\}[^[\]]*)+\]"
        matches = re.findall(array_pattern, text, re.DOTALL)
        for match in matches:
            try:
                events_array = json.loads(match.strip())
                logger.debug("JSON array parse succeeded")
                return {"events": events_array}
            except json.JSONDecodeError:
                continue
        if matches:
            logger.debug("JSON array parse failed")

        string_array_pattern = r'^\s*"?\[([^\[\]]+)\]"?\s*$'
        if re.match(string_array_pattern, text.strip()):
            logger.debug("Model returned string array instead of JSON")
            return {"events": []}

        logger.debug("All parsing strategies failed", extra={"text_length": len(text)})
        return {"events": []}

    async def _make_request(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Make HTTP request with fail-fast strategy - no retries."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": "curl/8.7.1",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        url = f"{self.BASE_URL}/chat/completions"
        body = json.dumps(payload).encode("utf-8")

        try:

            def make_request():
                return self._http.request(
                    uri=url, method="POST", body=body, headers=headers
                )

            response, content = await asyncio.to_thread(make_request)

            if response.status != 200:
                logger.debug(
                    f"HTTP {response.status} - switching model",
                    extra={"status": response.status},
                )
                return None

            try:
                data = json.loads(content.decode("utf-8"))
            except json.JSONDecodeError:
                logger.debug("Invalid JSON response - switching model")
                return None

            if data.get("error"):
                logger.debug(
                    "API error - switching model", extra={"error": data["error"]}
                )
                return None

            return data

        except Exception as e:
            logger.debug("Request failed - switching model", extra={"error": str(e)})
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
        logger.debug(
            "Trying model",
            extra={
                "model": model,
                "url": f"{self.BASE_URL}/chat/completions",
            },
        )
        try:
            data = await self._make_request(payload)
            if not data:
                return None
            try:
                output_text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                logger.debug(f"Invalid response structure from {model}")
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

            logger.debug(
                "Successfully extracted events",
                extra={
                    "model": model,
                    "event_count": len(normalized),
                },
            )
            return normalized

        except Exception as e:
            logger.debug(f"Model {model} failed", extra={"error": str(e)})
            return None

            output_text = None
            for item in data.get("output", []):
                if item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text")
                        if isinstance(text, str):
                            output_text = text
                        break
                if output_text:
                    break

            if not output_text:
                logger.error(
                    "Model returned no output text",
                    extra={"model": model, "response": data},
                )
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

    async def complete(
        self,
        user_prompt: str,
        sys_prompt: str | None = None,
    ) -> list[dict]:
        """Complete request using fail-fast fallback strategy."""

        for i, model in enumerate(self.FALLBACK_MODELS):
            logger.debug(
                f"Attempting model {i + 1}/{len(self.FALLBACK_MODELS)}: {model}"
            )
            result = await self._try_model(model, user_prompt, sys_prompt)
            if result is not None:
                logger.info(
                    f"Success with model: {model}",
                    extra={
                        "model": model,
                        "event_count": len(result),
                    },
                )
                return result

        logger.error("All fallback models failed")
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

        try:
            forwarded = await message.forward_to(dest_entity)
            if not forwarded:
                raise RuntimeError("Message was not forwarded")
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
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
