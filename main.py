import os
import sys
import json
import logging
import datetime as dt
import requests
from typing import Any, Dict

from dotenv import load_dotenv
import google.generativeai as genai
from garminconnect import Garmin

load_dotenv()

from cache import write_cache, write_minimal_error_cache, read_cache
from prompts import SYSTEM_PROMPT, CODEX_RULES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def telegram_send(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text})
    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def utc_now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def fetch_garmin_minimal(email: str, password: str) -> Dict[str, Any]:
    api = Garmin(email, password)
    api.login()

    today = dt.date.today().strftime("%Y-%m-%d")
    out: Dict[str, Any] = {
        "source": "garmin",
        "date": today,
        "fetched_at_utc": utc_now_iso(),
        "errors": [],
    }

    # Minimal calls; each is optional and can fail independently.
    try:
        out["body_battery"] = api.get_body_battery(today)
    except Exception as e:
        out["errors"].append({"metric": "body_battery", "error": str(e)})

    try:
        out["stress"] = api.get_stress_data(today)
    except Exception as e:
        out["errors"].append({"metric": "stress", "error": str(e)})

    try:
        out["sleep"] = api.get_sleep_data(today)
    except Exception as e:
        out["errors"].append({"metric": "sleep", "error": str(e)})

    try:
        out["rhr"] = api.get_rhr_day(today)
    except Exception as e:
        out["errors"].append({"metric": "rhr", "error": str(e)})

    return out


def build_user_prompt(cache: Dict[str, Any], push_kind: str) -> str:
    return (
        "Write in Russian.\n"
        f"Push type: {push_kind}\n"
        "Generate ONE Daily-Insight message in the REQUIRED format.\n"
        "Codex (strict):\n"
        + CODEX_RULES
        + "\nIf cache has errors or missing data: lower confidence and mention uncertainty briefly.\n"
        "User is recovering after clavicle fracture: DO NOT push sport/training.\n"
        "Input JSON:\n"
        f"{json.dumps(cache, ensure_ascii=False)}\n"
    )


def generate_message(gemini_key: str, model_name: str, cache: Dict[str, Any], push_kind: str) -> str:
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=SYSTEM_PROMPT,
    )
    resp = model.generate_content(build_user_prompt(cache, push_kind))
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty text")
    return text


def run_sync() -> None:
    log.info("Sync started")
    try:
        data = fetch_garmin_minimal(env("GARMIN_EMAIL"), env("GARMIN_PASSWORD"))
        write_cache(data)
        log.info("Sync ok, cache written")
    except Exception as e:
        log.exception("Sync failed")
        write_minimal_error_cache("sync_failed", str(e))
        raise


def run_push(push_kind: str) -> None:
    tg_token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    log.info("Push started: %s", push_kind)

    cache = read_cache()

    if cache.get("error") == "cache_missing":
        msg = "⚠️ Кэш пока пуст (SYNC ещё не выполнялся). Я живой 🙂"
        telegram_send(tg_token, chat_id, msg)
        log.info("Push: sent heartbeat (no cache)")
        return

    try:
        msg = generate_message(env("GEMINI_API_KEY"), env("GEMINI_MODEL"), cache, push_kind)
        telegram_send(tg_token, chat_id, msg)
        log.info("Push ok: insight sent")
    except Exception as e:
        log.exception("Push: insight generation failed")
        err_msg = (
            f"⚠️ Я споткнулся при генерации сообщения ({push_kind}).\n"
            f"{type(e).__name__}: {e}"
        )
        telegram_send(tg_token, chat_id, err_msg)
        # Do not re-raise: we sent a message (heartbeat/error report). Job succeeds.


def main() -> None:
    if len(sys.argv) < 2:
        raise RuntimeError("Usage: python main.py sync|push [morning|midday|evening]")

    mode = sys.argv[1].strip().lower()

    if mode == "sync":
        run_sync()
    elif mode == "push":
        push_kind = (sys.argv[2] if len(sys.argv) >= 3 else "morning").strip().lower()
        if push_kind not in ("morning", "midday", "evening"):
            raise RuntimeError("push must be morning|midday|evening")
        run_push(push_kind)
    else:
        raise RuntimeError("Unknown mode. Use sync or push.")


if __name__ == "__main__":
    main()
    
