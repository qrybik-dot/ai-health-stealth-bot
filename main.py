import os
import sys
import json
import datetime as dt
import requests
from typing import Any, Dict

import google.generativeai as genai
from garminconnect import Garmin

from cache import write_cache, read_cache
from prompts import SYSTEM_PROMPT

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
        "Use v2 visuals (emoji matrix), max 3 reasons, clear behavior frame, 1 line cost-of-ignoring, confidence marker, 1 human anchor.\n"
        "If cache has errors/missing data: lower confidence and mention uncertainty briefly.\n"
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
    data = fetch_garmin_minimal(env("GARMIN_EMAIL"), env("GARMIN_PASSWORD"))
    write_cache(data)

def run_push(push_kind: str) -> None:
    tg_token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")

    cache = read_cache()

    # If no cache exists yet, send heartbeat (your requirement: never silent)
    if cache.get("error") == "cache_missing":
        telegram_send(tg_token, chat_id, "⚠️ Кэш пока пуст (SYNC ещё не выполнялся). Я живой 🙂")
        return

    try:
        msg = generate_message(env("GEMINI_API_KEY"), env("GEMINI_MODEL"), cache, push_kind)
        telegram_send(tg_token, chat_id, msg)
    except Exception as e:
        telegram_send(
            tg_token,
            chat_id,
            f"⚠️ Я споткнулся при генерации сообщения ({push_kind}).\n{type(e).__name__}: {e}",
        )
        raise

def main():
    if len(sys.argv) < 2:
        raise RuntimeError("Usage: python main.py sync|push [morning|midday|evening]")

    mode = sys.argv[1].strip().lower()

    if mode == "sync":
        run_sync()
    elif mode == "push":
        push_kind = (sys.argv[2] if len(sys.argv) >= 3 else "morning").strip().lower()
        run_push(push_kind)
    else:
        raise RuntimeError("Unknown mode. Use sync or push.")

if __name__ == "__main__":
    main()
