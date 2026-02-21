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

# Webhook server
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn

load_dotenv()

from cache import write_cache, write_minimal_error_cache, read_cache
from prompts import SYSTEM_PROMPT, CODEX_RULES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI()


def env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def opt(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def telegram_send(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def telegram_set_webhook(token: str, url: str, secret_token: Optional[str] = None) -> Dict[str, Any]:
    api = f"https://api.telegram.org/bot{token}/setWebhook"
    payload: Dict[str, Any] = {"url": url}
    # Telegram supports a secret token header (recommended)
    if secret_token:
        payload["secret_token"] = secret_token
    r = requests.post(api, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def telegram_get_webhook_info(token: str) -> Dict[str, Any]:
    api = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    r = requests.get(api, timeout=30)
    r.raise_for_status()
    return r.json()


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


def build_user_prompt(cache: Dict[str, Any], push_kind: str, user_text: Optional[str] = None) -> str:
    extra = ""
    if user_text:
        extra = (
            "\nUser message (answer it directly, using the same tone and constraints, "
            "and keep it concise):\n"
            f"{user_text}\n"
        )

    return (
        "Write in Russian.\n"
        f"Push type: {push_kind}\n"
        "Generate ONE message. If user message exists, answer it. Otherwise send Daily-Insight.\n"
        "If sending Daily-Insight, follow REQUIRED format.\n"
        "Codex (strict):\n"
        + (CODEX_RULES or "")
        + "\nIf cache has errors or missing data: lower confidence and mention uncertainty briefly.\n"
        "User is recovering after clavicle fracture: DO NOT push sport/training.\n"
        + extra +
        "Input JSON:\n"
        f"{json.dumps(cache, ensure_ascii=False)}\n"
    )


def generate_message(gemini_key: str, model_name: str, cache: Dict[str, Any], push_kind: str, user_text: Optional[str] = None) -> str:
    if not SYSTEM_PROMPT or not SYSTEM_PROMPT.strip():
        raise RuntimeError("Missing GEMINI_SYSTEM_PROMPT (set it in env)")

    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=SYSTEM_PROMPT,
    )
    resp = model.generate_content(build_user_prompt(cache, push_kind, user_text=user_text))
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


def _is_allowed_chat(message: Dict[str, Any]) -> bool:
    allowed = opt("TELEGRAM_CHAT_ID", "")
    if not allowed:
        return True
    try:
        chat_id = str(message.get("chat", {}).get("id", ""))
        return chat_id == str(allowed)
    except Exception:
        return False


def _extract_text(update: Dict[str, Any]) -> Optional[str]:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None
    t = (msg.get("text") or "").strip()
    return t or None


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"


@app.post("/webhook", response_class=PlainTextResponse)
async def webhook(req: Request):
    # Optional security: verify Telegram secret header if set
    expected_secret = opt("TELEGRAM_WEBHOOK_SECRET", "")
    if expected_secret:
        got = req.headers.get("x-telegram-bot-api-secret-token", "")
        if got != expected_secret:
            log.warning("Webhook secret mismatch")
            return "forbidden"

    update = await req.json()

    # Only react to text messages
    msg = update.get("message") or update.get("edited_message")
    if not msg or not _is_allowed_chat(msg):
        return "ok"

    user_text = _extract_text(update)
    if not user_text:
        return "ok"

    tg_token = env("TELEGRAM_BOT_TOKEN")
    chat_id = str(msg.get("chat", {}).get("id"))

    # Choose push_kind based on local time (very rough; ok for v1)
    hour = dt.datetime.now().hour
    if hour < 12:
        push_kind = "morning"
    elif hour < 18:
        push_kind = "midday"
    else:
        push_kind = "evening"

    # Read cache; if missing, still answer but disclose uncertainty
    cache = read_cache()
    if cache.get("error") == "cache_missing":
        cache = {"error": "cache_missing", "errors": ["no_cache_yet"], "fetched_at_utc": utc_now_iso()}

    try:
        answer = generate_message(env("GEMINI_API_KEY"), env("GEMINI_MODEL"), cache, push_kind, user_text=user_text)
        telegram_send(tg_token, chat_id, answer)
    except Exception as e:
        log.exception("Webhook reply failed")
        err_msg = f"⚠️ Я споткнулся при ответе.\n{type(e).__name__}: {e}"
        telegram_send(tg_token, chat_id, err_msg)

    return "ok"


@app.on_event("startup")
def on_startup():
    """
    Optional auto-webhook setup.
    If PUBLIC_BASE_URL is set, we register webhook to: {PUBLIC_BASE_URL}/webhook
    """
    base = opt("PUBLIC_BASE_URL", "")
    if not base:
        log.info("PUBLIC_BASE_URL not set: skipping auto setWebhook")
        return

    token = env("TELEGRAM_BOT_TOKEN")
    hook_url = base.rstrip("/") + "/webhook"
    secret = opt("TELEGRAM_WEBHOOK_SECRET", "")

    try:
        res = telegram_set_webhook(token, hook_url, secret_token=secret if secret else None)
        info = telegram_get_webhook_info(token)
        log.info("Webhook set: %s | setWebhook=%s | getWebhookInfo=%s", hook_url, res, info)
    except Exception:
        log.exception("Failed to set webhook on startup")


def main() -> None:
    if len(sys.argv) >= 2:
        mode = sys.argv[1].strip().lower()
    else:
        mode = "serve"

    if mode == "sync":
        run_sync()
    elif mode == "push":
        push_kind = (sys.argv[2] if len(sys.argv) >= 3 else "morning").strip().lower()
        if push_kind not in ("morning", "midday", "evening"):
            raise RuntimeError("push must be morning|midday|evening")
        run_push(push_kind)
    elif mode == "serve":
        port = int(os.getenv("PORT", "8080"))
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    else:
        raise RuntimeError("Unknown mode. Use sync | push | serve.")


if __name__ == "__main__":
    main()
    