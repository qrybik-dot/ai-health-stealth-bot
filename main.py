import os
import sys
import json
import logging
import datetime as dt
import requests
from typing import Any, Dict, Optional

from dotenv import load_dotenv
import google.generativeai as genai
from garminconnect import Garmin

load_dotenv()

import datetime as dt
import uvicorn
from fastapi import FastAPI, Request, Response
from cache import (
    get_weekly_vote_stats,
    load_cache,
    load_weekly_state,
    save_daily_snapshot,
    save_weekly_state,
    upsert_color_vote,
)
from color_engine import (
    build_color_metaphor_line,
    build_color_story,
    generate_weekly_color,
    iso_week_id,
    generate_color_card_image,
    self_check_color_card,
    self_check_color_engine,
    weekly_color_from_dict,
)


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


def telegram_send_with_markup(token: str, chat_id: str, text: str, reply_markup: Dict[str, Any]) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")




def telegram_set_my_commands(token: str) -> None:
    url = f"https://api.telegram.org/bot{token}/setMyCommands"
    commands = [
        {"command": "today", "description": "Короткий инсайт дня"},
        {"command": "color", "description": "Цвет недели"},
        {"command": "week", "description": "Сводка недели"},
        {"command": "help", "description": "Список команд"},
    ]
    requests.post(url, json={"commands": commands}, timeout=15)
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
    """Builds the user prompt for a scheduled push, using the strict codex."""
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


def generate_message(
    gemini_key: str, model_name: str, cache: Dict[str, Any], push_kind: str
) -> str:
    """Generates a daily push message using the strict CODEX_RULES."""
    genai.configure(api_key=gemini_key)
    # For pushes, we use a dedicated model/config that understands the strict rules.
    # The system prompt is minimal as the rules are in the user prompt.
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction="You are a health assistant bot. Follow the user's instructions precisely.",
    )
    prompt = build_user_prompt(cache, push_kind)
    resp = model.generate_content(prompt)
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty text for push")
    return text


def run_sync() -> None:
    log.info("Sync started")
    try:
        data = fetch_garmin_minimal(env("GARMIN_EMAIL"), env("GARMIN_PASSWORD"))
        save_daily_snapshot(data)
        log.info("Sync ok, cache updated for today")
    except Exception as e:
        log.exception("Sync failed")
        error_data = {
            "source": "garmin",
            "error": "sync_failed",
            "errors": [{"metric": "sync", "error": str(e)}],
            "fetched_at_utc": utc_now_iso(),
        }
        save_daily_snapshot(error_data)
        raise


def run_push(push_kind: str) -> None:
    tg_token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    log.info("Push started: %s", push_kind)

    full_history = load_cache()

    if not full_history:
        msg = "⚠️ Кэш пока пуст (SYNC ещё не выполнялся). Я живой 🙂"
        telegram_send(tg_token, chat_id, msg)
        log.info("Push: sent heartbeat (no cache)")
        return

    # For daily pushes, we only need the most recent context.
    # Let's provide today's and yesterday's data to the model for comparison.
    today_str = dt.date.today().strftime("%Y-%m-%d")
    yesterday_str = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y-%m-%d")

    prompt_cache = {
        "today": full_history.get(today_str),
        "yesterday": full_history.get(yesterday_str),
    }

    # Do not send push if today's data is missing.
    if not prompt_cache.get("today"):
        log.warning("Push skipped: no data for today in cache.")
        # We don't send a message here, as this is an expected state between syncs.
        return

    try:
        msg = generate_message(
            env("GEMINI_API_KEY"), env("GEMINI_MODEL"), prompt_cache, push_kind
        )
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


def get_or_create_weekly_color_state() -> Dict[str, Any]:
    week_id = iso_week_id()
    color = generate_weekly_color(week_id).to_dict()
    try:
        state = load_weekly_state()
        if week_id in state and isinstance(state[week_id], dict):
            return state[week_id]
        save_weekly_state(week_id, color)
        return color
    except Exception:
        log.exception("Failed to load/save weekly color state, using deterministic fallback")
        return color


def telegram_send_photo_with_markup(
    token: str,
    chat_id: str,
    photo_path: str,
    caption: str,
    reply_markup: Dict[str, Any],
) -> Optional[int]:
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with open(photo_path, "rb") as photo_file:
        response = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "caption": caption,
                "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
            },
            files={"photo": photo_file},
            timeout=30,
        )
    if response.status_code != 200:
        raise RuntimeError(f"Telegram error {response.status_code}: {response.text}")
    payload = response.json()
    return payload.get("result", {}).get("message_id")


def telegram_answer_callback(token: str, callback_query_id: str, text: str = "") -> None:
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    requests.post(url, json=payload, timeout=10)


def telegram_edit_message_reply_markup(token: str, chat_id: str, message_id: int) -> None:
    url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
    response = requests.post(
        url,
        json={"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        timeout=15,
    )
    if response.status_code != 200:
        log.warning("Failed to clear inline keyboard: %s", response.text)


def map_rarity_ru(rarity_level: str) -> str:
    if rarity_level == "rare":
        return "редкий"
    if rarity_level == "exotic":
        return "экзотический"
    return "классический"


def build_color_caption(color: Dict[str, Any]) -> str:
    color_obj = weekly_color_from_dict(color)
    rarity_label = map_rarity_ru(color_obj.rarity_level)
    return (
        f"Цвет недели: {color_obj.name_ru} · {color_obj.hex}\n"
        f"Фокус: {build_color_metaphor_line(color_obj)}\n"
        f"Известность названия: {rarity_label}"
    )


def build_color_keyboard(week_id: str) -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "🎨 История цвета", "callback_data": f"color_story:{week_id}"}],
            [
                {"text": "✅ Попало", "callback_data": f"color_vote:{week_id}:yes"},
                {"text": "➖ Частично", "callback_data": f"color_vote:{week_id}:partial"},
                {"text": "❌ Мимо", "callback_data": f"color_vote:{week_id}:no"},
            ],
        ]
    }


def handle_color_command(tg_token: str, chat_id: str) -> None:
    color = get_or_create_weekly_color_state()
    image_path = generate_color_card_image(color["week_id"], color["hex"])
    telegram_send_photo_with_markup(
        tg_token,
        chat_id,
        image_path,
        build_color_caption(color),
        build_color_keyboard(color["week_id"]),
    )


def _color_state_for_week(week_id: str) -> Dict[str, Any]:
    color_state = get_or_create_weekly_color_state()
    if color_state.get("week_id") == week_id:
        return color_state
    return generate_weekly_color(week_id).to_dict()


def handle_color_story_callback(tg_token: str, chat_id: str, callback_query: Dict[str, Any]) -> None:
    callback_id = callback_query.get("id")
    callback_data = callback_query.get("data", "")
    week_id = callback_data.split(":", 1)[1] if ":" in callback_data else iso_week_id()

    color_state = _color_state_for_week(week_id)
    story = build_color_story(weekly_color_from_dict(color_state))
    telegram_send(tg_token, chat_id, story)

    message = callback_query.get("message", {})
    message_id = message.get("message_id")
    if message_id is not None:
        telegram_edit_message_reply_markup(tg_token, chat_id, int(message_id))

    if callback_id:
        telegram_answer_callback(tg_token, callback_id)


def handle_color_vote_callback(tg_token: str, chat_id: str, callback_query: Dict[str, Any]) -> None:
    callback_id = callback_query.get("id")
    callback_data = callback_query.get("data", "")
    parts = callback_data.split(":")
    week_id = parts[1] if len(parts) > 1 else iso_week_id()
    vote_value = parts[2] if len(parts) > 2 else "partial"
    today = dt.date.today().isoformat()
    upsert_color_vote(chat_id=chat_id, vote_date=today, vote_value=vote_value, week_id=week_id)
    stats = get_weekly_vote_stats(week_id)
    if callback_id:
        telegram_answer_callback(tg_token, callback_id, text="Голос учтён")
    log.info("Color vote stored: week=%s vote=%s stats=%s", week_id, vote_value, stats)


def build_help_message() -> str:
    return "Команды:\n/today\n/color\n/week\n/help"


def build_chat_prompt(cache: Dict[str, Any], query: str) -> str:
    """Builds the user prompt for conversational chat."""
    return (
        "Write in Russian.\n"
        f"User query: {query}\n"
        "Here is the data history context:\n"
        f"{json.dumps(cache, ensure_ascii=False, indent=2)}\n"
    )


def generate_chat_message(gemini_key: str, model_name: str, cache: Dict[str, Any], query: str) -> str:
    """Generates a conversational response based on history and a user query."""
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=SYSTEM_PROMPT,
    )
    prompt = build_chat_prompt(cache, query)
    resp = model.generate_content(prompt)
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty text for chat")
    return text


# --- FastAPI Server ---
app = FastAPI()


@app.get("/health")
def health_check():
    return "ok"


@app.post("/webhook")
async def webhook(request: Request):
    """Telegram webhook endpoint."""
    tg_token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    try:
        data = await request.json()
        log.info("Webhook received: %s", data)

        callback_query = data.get("callback_query")
        if callback_query:
            callback_data = callback_query.get("data", "")
            callback_chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", chat_id))
            if callback_data.startswith("color_story"):
                handle_color_story_callback(tg_token, callback_chat_id, callback_query)
            elif callback_data.startswith("color_vote"):
                handle_color_vote_callback(tg_token, callback_chat_id, callback_query)
            return Response(status_code=200)

        message = data.get("message", {})
        text = message.get("text", "").strip()
        message_chat_id = str(message.get("chat", {}).get("id", chat_id))

        if not text:
            return Response(status_code=200)

        # Acknowledge receipt to prevent Telegram retries
        # and do the heavy lifting after.
        # Here we just send a "typing..." indicator.
        url = f"https://api.telegram.org/bot{tg_token}/sendChatAction"
        requests.post(url, json={"chat_id": message_chat_id, "action": "typing"})

        if text.lower() == "/color":
            handle_color_command(tg_token, message_chat_id)
            return Response(status_code=200)
        if text.lower() == "/help":
            telegram_send(tg_token, message_chat_id, build_help_message())
            return Response(status_code=200)

        # Load the latest cache from Gist
        history_cache = load_cache()

        # Generate a conversational response
        response_msg = generate_chat_message(
            env("GEMINI_API_KEY"), env("GEMINI_MODEL"), history_cache, text
        )

        telegram_send(tg_token, message_chat_id, response_msg)

    except Exception:
        log.exception("Webhook processing failed")
        # Silently fail to prevent error loops with Telegram,
        # but log the error for debugging.

    return Response(status_code=200)


# --- CLI ---
def run_serve() -> None:
    """Starts the Uvicorn server."""
    log.info("Starting web server")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 main.py [sync|push|serve|color-self-check|color-card-self-check]")
        return

    mode = sys.argv[1].strip().lower()

    if mode == "sync":
        run_sync()
    elif mode == "push":
        push_kind = (sys.argv[2] if len(sys.argv) > 2 else "morning").strip().lower()
        if push_kind not in ["morning", "midday", "evening"]:
            print("Error: push mode requires a valid kind [morning|midday|evening]")
            return
        run_push(push_kind)
    elif mode == "serve":
        run_serve()
    elif mode == "color-self-check":
        problems = self_check_color_engine()
        if problems:
            print("color-self-check failed")
            for problem in problems:
                print(problem)
            sys.exit(1)
        print("color-self-check ok")
    elif mode == "color-card-self-check":
        problems = self_check_color_card()
        if problems:
            print("color-card-self-check failed")
            for problem in problems:
                print(problem)
            sys.exit(1)
        print("color-card-self-check ok")
    else:
        print(f"Error: Unknown mode '{mode}'. Use sync, push, serve, color-self-check, or color-card-self-check.")
        sys.exit(1)

if __name__ == "__main__":
    main()
    
    
