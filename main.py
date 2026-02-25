import os
import sys
import json
import logging
import datetime as dt
import requests
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
import google.generativeai as genai
from garminconnect import Garmin

load_dotenv()

from zoneinfo import ZoneInfo
import uvicorn
from fastapi import FastAPI, Request, Response
from cache import (
    get_color_vote,
    get_today_state,
    get_today_vote,
    get_today_vote_accuracy,
    get_week_vote_accuracy,
    get_weekly_vote_stats,
    load_cache,
    load_cache_with_meta,
    load_weekly_state,
    mark_slot_sent,
    mark_weekly_report_sent,
    save_daily_snapshot,
    save_weekly_state,
    upsert_today_state,
    upsert_today_vote,
    upsert_color_vote,
    was_slot_sent,
    was_weekly_report_sent,
)
from color_engine import (
    build_color_metaphor_line,
    build_color_story,
    generate_daily_accent_hex,
    generate_today_card_image,
    generate_weekly_color,
    iso_week_id,
    generate_color_card_image,
    self_check_color_card,
    self_check_color_engine,
    self_check_today_card,
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




def ensure_bot_commands(token: str) -> None:
    url = f"https://api.telegram.org/bot{token}/setMyCommands"
    commands = [
        {"command": "today", "description": "карточка дня"},
        {"command": "color", "description": "цвет недели"},
        {"command": "week", "description": "отчёт недели"},
        {"command": "stats", "description": "статистика недели"},
        {"command": "help", "description": "подсказка"},
    ]
    response = requests.post(url, json={"commands": commands}, timeout=15)
    if response.status_code != 200:
        raise RuntimeError(f"Telegram setMyCommands error {response.status_code}: {response.text}")
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram setMyCommands rejected: {payload}")


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


def _now_msk() -> dt.datetime:
    return dt.datetime.now(ZoneInfo("Europe/Moscow"))


SLOT_WINDOWS: Dict[str, Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]] = {
    "morning": ((8, 50), (9, 0), (10, 10)),
    "midday": ((12, 50), (13, 0), (14, 10)),
    "evening": ((18, 50), (19, 0), (20, 10)),
}


def _minutes(hh: int, mm: int) -> int:
    return hh * 60 + mm


def _resolve_push_slot(now_msk: dt.datetime) -> Optional[str]:
    now_min = _minutes(now_msk.hour, now_msk.minute)
    for slot, (start, _target, end) in SLOT_WINDOWS.items():
        if _minutes(*start) <= now_min <= _minutes(*end):
            return slot
    return None


def _nearest_slot(now_msk: dt.datetime) -> str:
    now_min = _minutes(now_msk.hour, now_msk.minute)
    best_slot = "morning"
    best_delta = 10**9
    for slot, (_start, target, _end) in SLOT_WINDOWS.items():
        delta = abs(now_min - _minutes(*target))
        if delta < best_delta:
            best_delta = delta
            best_slot = slot
    return best_slot


def _resolve_scheduled_push_kind(now_msk: dt.datetime, override: Optional[str] = None) -> str:
    if override in {"morning", "midday", "evening"}:
        return override
    in_window = _resolve_push_slot(now_msk)
    if in_window:
        return in_window
    return _nearest_slot(now_msk)


def _send_push_fallback(tg_token: str, chat_id: str, text: str) -> None:
    try:
        telegram_send(tg_token, chat_id, text)
        log.info("telegram send ok fallback=true")
    except Exception:
        log.exception("telegram send error fallback=true")


def _build_schedule_decision(now_msk: dt.datetime, chat_id: str, override: Optional[str] = None) -> Dict[str, Any]:
    window_slot = _resolve_push_slot(now_msk)
    slot = _resolve_scheduled_push_kind(now_msk, override=override)
    today_str = now_msk.date().isoformat()
    already_sent = was_slot_sent(chat_id=chat_id, send_date=today_str, slot=slot)
    return {
        "now_msk": now_msk.isoformat(),
        "window_matched": window_slot if window_slot is not None else "none",
        "slot_id": slot,
        "already_sent": already_sent,
        "target_chat_id": chat_id,
        "date": today_str,
    }


def _log_schedule_decision(decision: Dict[str, Any]) -> None:
    log.info(
        "schedule_decision now_msk=%s window=%s slot_id=%s already_sent=%s target_chat_id=%s",
        decision["now_msk"],
        decision["window_matched"],
        decision["slot_id"],
        decision["already_sent"],
        decision["target_chat_id"],
    )


def _build_fallback_message(slot: str) -> str:
    return (
        f"Слот {slot}: данных Garmin за сегодня пока нет. "
        "Держим ровный режим, сверимся позже."
    )


def _cache_reason_code(cache_meta: Dict[str, Any]) -> str:
    error_code = str(cache_meta.get("error", "")).strip().lower()
    mapping = {
        "gist_403": "gist_403",
        "gist_404": "gist_404",
        "rate_limit": "rate_limit",
        "gist_401": "gist_401",
    }
    return mapping.get(error_code, "cache_unavailable")


def _score_to_bar(score: float) -> str:
    value = max(1, min(5, int(round(score))))
    return "■" * value + "□" * (5 - value)


def _extract_scores(today_payload: Optional[Dict[str, Any]]) -> Dict[str, float]:
    if not isinstance(today_payload, dict):
        return {"body": 3.0, "nerves": 3.0, "sleep": 3.0}
    body = today_payload.get("body_battery") if isinstance(today_payload.get("body_battery"), dict) else {}
    stress = today_payload.get("stress") if isinstance(today_payload.get("stress"), dict) else {}
    sleep = today_payload.get("sleep") if isinstance(today_payload.get("sleep"), dict) else {}
    body_value = body.get("mostRecentValue") or body.get("chargedValue") or 50
    stress_value = stress.get("avgStressLevel") or stress.get("overallStressLevel") or 45
    sleep_seconds = sleep.get("sleepTimeSeconds") or sleep.get("totalSleepSeconds") or 7 * 3600
    sleep_hours = float(sleep_seconds) / 3600.0 if isinstance(sleep_seconds, (int, float)) else 7.0
    body_score = 1 + (max(0.0, min(100.0, float(body_value))) / 25.0)
    nerves_score = 5 - (max(0.0, min(100.0, float(stress_value))) / 25.0)
    sleep_score = max(1.0, min(5.0, sleep_hours / 1.8))
    return {"body": body_score, "nerves": nerves_score, "sleep": sleep_score}


def _status_line(scores: Dict[str, float]) -> Tuple[str, str]:
    avg = (scores["body"] + scores["nerves"] + scores["sleep"]) / 3
    if avg >= 3.7:
        return "🟢", "Собранный режим"
    if avg >= 2.7:
        return "🟡", "Ровный режим"
    return "🔴", "Бережный режим"


def _actions_for_slot(slot: str) -> list[str]:
    if slot == "morning":
        return [
            "🟢 Дай первому блоку 60–90 минут без шума.",
            "🟡 Кофеин лучше до середины дня, не растягивай до вечера.",
            "🟡 Раздели нагрузку на 2–3 ровных отрезка.",
            "🔴 Не начинай день с резкого спринта задач.",
        ]
    if slot == "midday":
        return [
            "🟢 Сделай 7 минут паузы и перезапусти фокус.",
            "🟡 Вода + короткая еда без тяжести.",
            "🟡 10–15 минут спокойной ходьбы.",
            "🔴 Не копи стресс до вечера.",
        ]
    return [
        "🟢 Приглуши свет за 1.5–2 часа до сна.",
        "🟡 Экраны в тёплый режим и тише уведомления.",
        "🟡 Спокойное завершение: душ/чтение/растяжка.",
        "🔴 Без тяжёлых разговоров и рабочих добивок перед сном.",
    ]


def _sometimes_humor(day: str, slot: str) -> str:
    seed = sum(ord(c) for c in f"{day}:{slot}")
    if seed % 5 != 0:
        return ""
    return "Коротко: картошка тоже знает цену ровному режиму."


def _build_scheduled_message(slot: str, today_payload: Optional[Dict[str, Any]], color_name: str, day: str, today_vote: Optional[Dict[str, Any]]) -> str:
    scores = _extract_scores(today_payload)
    icon, label = _status_line(scores)
    actions = _actions_for_slot(slot)
    vote_line = ""
    if slot == "evening" and isinstance(today_vote, dict):
        vote_map = {"yes": "✅", "partial": "➖", "no": "❌"}
        vote_icon = vote_map.get(str(today_vote.get("vote", "")), "➖")
        vote_line = f"\nТвой вердикт по дню: {vote_icon} — принято."

    reasons = [
        "• Ритм дня читается по сочетанию энергии и напряжения.",
        "• Лучше ровная серия шагов, чем один рывок.",
    ]
    if slot == "midday":
        reasons[1] = "• В середине дня важнее перезапуск, чем ускорение."
    elif slot == "evening":
        reasons[1] = "• Вечерний запас бережём для сна, не для финального штурма."

    ignore_price = {
        "morning": "Цена игнора: день разъедется по мелким отвлечениям.",
        "midday": "Цена игнора: к вечеру накопится лишний шум в голове.",
        "evening": "Цена игнора: сон будет рваным, а утро вязким.",
    }[slot]
    confidence = "🟢🟢🟡" if isinstance(today_payload, dict) else "🟡⚪⚪"
    humor = _sometimes_humor(day, slot)
    color_line = f"\nЦвет недели: {color_name}." if slot == "morning" else ""

    return (
        f"{icon} {label}\n"
        f"Тело: {_score_to_bar(scores['body'])}\n"
        f"Нервы: {_score_to_bar(scores['nerves'])}\n"
        f"Сон: {_score_to_bar(scores['sleep'])}\n"
        "Причины:\n"
        f"{reasons[0]}\n"
        f"{reasons[1]}\n"
        "Что делать:\n"
        + "\n".join(actions)
        + "\n"
        + ignore_price
        + f"\nУверенность: {confidence}"
        + color_line
        + vote_line
        + (f"\n{humor}" if humor else "")
    )


def _safe_today_payload(cache: Dict[str, Any], day: str) -> Optional[Dict[str, Any]]:
    raw = cache.get(day)
    return raw if isinstance(raw, dict) else None


def _build_weekly_report_message(full_history: Dict[str, Any], now_msk: dt.datetime) -> str:
    days = []
    for i in range(7):
        d = (now_msk.date() - dt.timedelta(days=i)).isoformat()
        payload = full_history.get(d)
        if isinstance(payload, dict):
            days.append(payload)

    def rng(values: list[float]) -> str:
        if not values:
            return "—"
        return f"{round(min(values))}–{round(max(values))}"

    body_vals=[]; stress_vals=[]; rhr_vals=[]; sleep_vals=[]
    for payload in days:
        bb = payload.get("body_battery") if isinstance(payload.get("body_battery"), dict) else {}
        st = payload.get("stress") if isinstance(payload.get("stress"), dict) else {}
        sl = payload.get("sleep") if isinstance(payload.get("sleep"), dict) else {}
        rhr = payload.get("rhr") if isinstance(payload.get("rhr"), dict) else {}
        b = bb.get("mostRecentValue") or bb.get("chargedValue")
        s = st.get("avgStressLevel") or st.get("overallStressLevel")
        rr = rhr.get("lastSevenDaysAvgRestingHeartRate") or rhr.get("restingHeartRate")
        ss = sl.get("sleepTimeSeconds") or sl.get("totalSleepSeconds")
        if isinstance(b,(int,float)): body_vals.append(float(b))
        if isinstance(s,(int,float)): stress_vals.append(float(s))
        if isinstance(rr,(int,float)): rhr_vals.append(float(rr))
        if isinstance(ss,(int,float)): sleep_vals.append(float(ss)/3600.0)

    if len(days) < 3:
        return "Неделя вышла с туманом данных. Видно одно: лучше держался ровный темп, когда вечер был спокойнее."

    observation = "Главный контраст: когда стресс ниже, сон стабильнее в ту же ночь."
    if body_vals and sleep_vals and (max(sleep_vals) - min(sleep_vals) >= 1.0):
        observation = "Главный контраст: длиннее сон — заметно ровнее утренний запас."

    humor = "Кратко: неделя без драмы, и это хороший жанр." if now_msk.isoweekday() % 2 == 0 else ""
    lines = [
        f"Итог недели ({iso_week_id(now_msk.date())}):",
        f"• Body Battery: {rng(body_vals)}",
        f"• Стресс: {rng(stress_vals)}",
        f"• Сон, ч: {rng(sleep_vals)}",
        f"• RHR: {rng(rhr_vals)}",
        observation,
    ]
    if humor:
        lines.append(humor)
    return "\n".join(lines)


def run_push(push_kind: str, dry_run: bool = False) -> None:
    tg_token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    now_msk = _now_msk()
    now_utc = dt.datetime.now(dt.timezone.utc)

    log.info("push_kind received=%s", push_kind)
    if push_kind == "scheduled":
        decision = _build_schedule_decision(now_msk, chat_id)
        resolved_slot = decision["slot_id"]
    else:
        resolved_slot = push_kind
        decision = _build_schedule_decision(now_msk, chat_id, override=resolved_slot)

    _log_schedule_decision(decision)
    today_str = decision["date"]

    if decision["already_sent"]:
        log.info("dedupe_skip slot=%s date=%s chat_id=%s", resolved_slot, today_str, chat_id)
        return

    if dry_run:
        log.info("send_result=dry_run would_send=true slot_id=%s", resolved_slot)
        return

    log.info("Push started: kind=%s slot=%s msk_now=%s utc_now=%s", push_kind, resolved_slot, now_msk.isoformat(), now_utc.isoformat())
    full_history, cache_meta = load_cache_with_meta()
    log.info(
        "cache_source=%s cache_keys_count=%s cache_available=%s cache_error=%s",
        cache_meta.get("source", "unknown"),
        len(full_history.keys()) if isinstance(full_history, dict) else 0,
        cache_meta.get("available", False),
        cache_meta.get("error", ""),
    )

    if (not cache_meta.get("available", False)) or (not isinstance(full_history, dict)) or (not full_history):
        reason_code = _cache_reason_code(cache_meta)
        _send_push_fallback(tg_token, chat_id, _build_fallback_message(resolved_slot))
        mark_slot_sent(chat_id=chat_id, send_date=today_str, slot=resolved_slot, sent_ts=utc_now_iso())
        log.info("dedupe_marked slot=%s date=%s chat_id=%s", resolved_slot, today_str, chat_id)
        log.info("send_result=ok fallback=true reason=%s slot_id=%s", reason_code, resolved_slot)
        return

    today_payload = _safe_today_payload(full_history, today_str)
    week_color = get_or_create_weekly_color_state()
    today_vote = get_today_vote(chat_id=chat_id, vote_date=today_str)
    msg = _build_scheduled_message(
        slot=resolved_slot,
        today_payload=today_payload,
        color_name=str(week_color.get("name_ru", "без названия")),
        day=today_str,
        today_vote=today_vote,
    )

    try:
        if resolved_slot == "morning":
            mode_tag = classify_mode_tag(today_payload)
            signal = compute_today_signal(today_payload)
            accent_hex = generate_daily_accent_hex(chat_id, today_str, week_color["week_id"], week_color["hex"])
            today_state = upsert_today_state(
                chat_id=chat_id,
                value_date=today_str,
                state_payload={
                    "status_tag": mode_tag,
                    "confidence": signal["confidence"],
                    "amplitude": signal["amplitude"],
                    "accent_hex": accent_hex,
                    "week_id": week_color["week_id"],
                },
            )
            image_started = dt.datetime.now(dt.timezone.utc)
            image_path = generate_today_card_image(
                chat_id=chat_id,
                day=today_str,
                week_id=week_color["week_id"],
                week_color_hex=week_color["hex"],
                mode_tag=mode_tag,
                accent_hex=today_state.get("accent_hex", accent_hex),
            )
            elapsed = (dt.datetime.now(dt.timezone.utc) - image_started).total_seconds()
            log.info("morning_photo_generated path=%s elapsed_s=%.3f", image_path, elapsed)
            telegram_send_photo_with_markup(tg_token, chat_id, image_path, msg, {"inline_keyboard": []})
        else:
            telegram_send(tg_token, chat_id, msg)

        mark_slot_sent(chat_id=chat_id, send_date=today_str, slot=resolved_slot, sent_ts=utc_now_iso())
        log.info("dedupe_marked slot=%s date=%s chat_id=%s", resolved_slot, today_str, chat_id)

        is_sunday_evening = resolved_slot == "evening" and now_msk.isoweekday() == 7
        if is_sunday_evening:
            week_id = iso_week_id(now_msk.date())
            if was_weekly_report_sent(chat_id=chat_id, week_id=week_id):
                log.info("weekly_report_skip week_id=%s chat_id=%s", week_id, chat_id)
            else:
                weekly_msg = _build_weekly_report_message(full_history, now_msk)
                telegram_send(tg_token, chat_id, weekly_msg)
                mark_weekly_report_sent(chat_id=chat_id, week_id=week_id, sent_ts=utc_now_iso())
                log.info("weekly_report_sent week_id=%s chat_id=%s", week_id, chat_id)

        log.info("send_result=ok fallback=%s slot_id=%s", str(today_payload is None).lower(), resolved_slot)
    except Exception:
        log.exception("Push send failed")
        _send_push_fallback(tg_token, chat_id, f"Слот {resolved_slot}: отправка не удалась, повторю позже.")
        log.info("send_result=error slot_id=%s", resolved_slot)


def run_push_self_check() -> None:
    requested_kind = "scheduled"
    now_msk = _now_msk()
    detected_kind = _resolve_scheduled_push_kind(now_msk) if requested_kind == "scheduled" else requested_kind
    cache_data, cache_meta = load_cache_with_meta()
    today_str = _now_msk().date().isoformat()
    has_today = isinstance(cache_data, dict) and bool(cache_data.get(today_str))

    print(f"requested_push_kind={requested_kind}")
    print(f"detected_push_kind={detected_kind}")
    print(f"has_today={str(has_today).lower()}")
    print(f"cache_source={cache_meta.get('source', 'unknown')}")
    print(f"cache_available={str(bool(cache_meta.get('available', False))).lower()}")

    if os.getenv("DRY_RUN", "0") == "1":
        print("dry_run=true telegram_send=skipped")


def run_cache_self_check() -> None:
    cache_data, cache_meta = load_cache_with_meta()
    today_str = _now_msk().date().isoformat()
    has_today = isinstance(cache_data, dict) and bool(cache_data.get(today_str))
    top_level_keys = sorted(list(cache_data.keys())) if isinstance(cache_data, dict) else []

    print(f"cache_source={cache_meta.get('source', 'unknown')}")
    print(f"cache_available={str(bool(cache_meta.get('available', False))).lower()}")
    print(f"cache_error={cache_meta.get('error', '')}")
    print(f"has_today={str(has_today).lower()}")
    print(f"top_level_keys={top_level_keys}")


def run_schedule_debug(at_iso: str, chat_id: Optional[str] = None) -> None:
    raw_chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "debug-chat")
    now_msk = dt.datetime.fromisoformat(at_iso)
    if now_msk.tzinfo is None:
        now_msk = now_msk.replace(tzinfo=ZoneInfo("Europe/Moscow"))
    else:
        now_msk = now_msk.astimezone(ZoneInfo("Europe/Moscow"))
    decision = _build_schedule_decision(now_msk, raw_chat_id)
    _log_schedule_decision(decision)
    would_send = decision["slot_id"] is not None and not decision["already_sent"]
    print(f"would_send={str(would_send).lower()}")


def run_schedule_self_check() -> None:
    checks = [
        "2026-02-24T08:55:00+03:00",
        "2026-02-24T13:05:00+03:00",
        "2026-02-24T19:05:00+03:00",
    ]
    for at_iso in checks:
        now_msk = dt.datetime.fromisoformat(at_iso).astimezone(ZoneInfo("Europe/Moscow"))
        test_chat_id = f"schedule-self-check-{at_iso}"
        decision = _build_schedule_decision(now_msk, test_chat_id)
        _log_schedule_decision(decision)
        slot_id = decision["slot_id"]
        if slot_id is None:
            raise RuntimeError(f"self-check failed: expected window for {at_iso}")
        if decision["already_sent"]:
            raise RuntimeError(f"self-check failed: already_sent=true for {at_iso}")
        print(f"self_check_at={at_iso} would_send=true slot_id={slot_id}")
    print("schedule-self-check ok")


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


def telegram_edit_message_reply_markup(token: str, chat_id: str, message_id: int, reply_markup: Dict[str, Any]) -> None:
    url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
    response = requests.post(
        url,
        json={"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        timeout=15,
    )
    if response.status_code != 200:
        log.warning("Failed to edit inline keyboard: %s", response.text)


def map_rarity_ru(rarity_level: str) -> str:
    if rarity_level == "rare":
        return "редкий"
    if rarity_level == "exotic":
        return "экзотический"
    return "классический"


def vote_label(vote_value: str) -> str:
    return {
        "yes": "✅ Попало",
        "partial": "➖ Частично",
        "no": "❌ Мимо",
    }.get(vote_value, "➖ Частично")


def today_vote_label(vote_value: str) -> str:
    return {
        "yes": "✅ Попало",
        "partial": "➖ Частично",
        "no": "❌ Мимо",
    }.get(vote_value, "➖ Частично")


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


def build_color_vote_keyboard(week_id: str) -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Попало", "callback_data": f"color_vote:{week_id}:yes"},
                {"text": "➖ Частично", "callback_data": f"color_vote:{week_id}:partial"},
                {"text": "❌ Мимо", "callback_data": f"color_vote:{week_id}:no"},
            ],
        ]
    }


def build_color_voted_keyboard(vote_value: str) -> Dict[str, Any]:
    return {"inline_keyboard": [[{"text": f"🗳 Ваш выбор: {vote_label(vote_value)}", "callback_data": "noop"}]]}


def classify_mode_tag(today_payload: Optional[Dict[str, Any]]) -> str:
    if not isinstance(today_payload, dict):
        return "no_data"

    body = today_payload.get("body_battery")
    stress = today_payload.get("stress")

    body_level = None
    if isinstance(body, dict):
        body_level = body.get("mostRecentValue") or body.get("chargedValue")

    stress_level = None
    if isinstance(stress, dict):
        stress_level = stress.get("avgStressLevel") or stress.get("overallStressLevel")

    if body_level is None and stress_level is None:
        return "no_data"
    if body_level is not None and body_level < 35:
        return "recovery"
    if stress_level is not None and stress_level > 62:
        return "recovery"
    if body_level is not None and body_level > 70:
        return "push"
    if stress_level is not None and stress_level < 30:
        return "push"
    return "steady"


def status_profile(mode_tag: str) -> Dict[str, str]:
    if mode_tag == "no_data":
        return {
            "label": "мягкий режим",
            "reason": "данных за день пока мало, поэтому ориентир — ровный темп",
            "hint": "сделайте 2–3 коротких блока с паузами",
            "accent_note": "Акцент дня поддерживает спокойную собранность без перегруза.",
        }
    if mode_tag == "recovery":
        return {
            "label": "восстановительный ритм",
            "reason": "фон дня мягкий: лучше короткие и предсказуемые циклы",
            "hint": "держите приоритет на одном главном деле за раз",
            "accent_note": "Акцент дня подчёркивает аккуратный и ровный ход.",
        }
    if mode_tag == "push":
        return {
            "label": "собранный темп",
            "reason": "ресурс дня читается устойчиво, можно работать плотнее",
            "hint": "закройте главное до вечера без резких рывков",
            "accent_note": "Акцент дня держит фокус на управляемой плотности.",
        }
    return {
        "label": "стабильный режим",
        "reason": "метрики показывают ровный рисунок без резких скачков",
        "hint": "сохраняйте последовательность и короткие паузы",
        "accent_note": "Акцент дня поддерживает ровную последовательность.",
    }


def compute_today_signal(today_payload: Optional[Dict[str, Any]]) -> Dict[str, float]:
    if not isinstance(today_payload, dict):
        return {"confidence": 0.2, "amplitude": 0.2}

    confidence = 0
    if isinstance(today_payload.get("body_battery"), dict):
        confidence += 1
    if isinstance(today_payload.get("stress"), dict):
        confidence += 1
    if isinstance(today_payload.get("sleep"), dict):
        confidence += 1

    body_level = None
    stress_level = None
    body = today_payload.get("body_battery")
    stress = today_payload.get("stress")
    if isinstance(body, dict):
        body_level = body.get("mostRecentValue") or body.get("chargedValue")
    if isinstance(stress, dict):
        stress_level = stress.get("avgStressLevel") or stress.get("overallStressLevel")

    amplitude = 0.4
    if isinstance(body_level, (int, float)):
        amplitude += abs(float(body_level) - 50.0) / 120.0
    if isinstance(stress_level, (int, float)):
        amplitude += abs(float(stress_level) - 45.0) / 140.0

    return {
        "confidence": round(min(1.0, 0.25 + confidence * 0.23), 2),
        "amplitude": round(min(1.0, amplitude), 2),
    }


def build_fact_of_day(mode_tag: str, today_payload: Optional[Dict[str, Any]]) -> str:
    confidence = 0
    if isinstance(today_payload, dict):
        if isinstance(today_payload.get("body_battery"), dict):
            confidence += 1
        if isinstance(today_payload.get("stress"), dict):
            confidence += 1
        if isinstance(today_payload.get("sleep"), dict):
            confidence += 1

    if confidence <= 1 or mode_tag == "no_data":
        return "Даже без полного набора метрик заметнее всего не уровень, а форма дня: ровный ритм обычно читается лучше, чем отдельные пики."
    if mode_tag == "recovery":
        return "Когда день идёт мягче обычного, контраст между утренним и вечерним темпом становится ключевым маркером состояния режима."
    if mode_tag == "push":
        return "В собранные дни важен не максимум, а устойчивость: резкие всплески чаще уступают ровной серии отрезков."
    return "Стабильные дни обычно выглядят как повторяемый рисунок нагрузки и пауз, а не как набор случайных интенсивных блоков."


def build_today_keyboard(day: str, voted: Optional[str], history_visible: bool = True) -> Dict[str, Any]:
    if voted:
        return {
            "inline_keyboard": [
                [{"text": f"🗳 Ваш выбор: {today_vote_label(voted)}", "callback_data": "noop"}]
            ]
        }

    rows = []
    if history_visible:
        rows.append([{"text": "🎨 История акцента", "callback_data": f"today_story:{day}"}])
    rows.append(
        [
            {"text": "✅ Попало", "callback_data": f"today_vote:{day}:yes"},
            {"text": "➖ Частично", "callback_data": f"today_vote:{day}:partial"},
            {"text": "❌ Мимо", "callback_data": f"today_vote:{day}:no"},
        ]
    )
    return {"inline_keyboard": rows}


def build_accent_story(today_state: Dict[str, Any]) -> str:
    status_tag = str(today_state.get("status_tag", "steady"))
    profile = status_profile(status_tag)
    week_id = str(today_state.get("week_id", "неделя"))
    variants = {
        "recovery": (
            "Чаще заметен в спокойных предметных сочетаниях и мягком свете. 🕯️",
            "Пара: матовые поверхности и тёплый серый; недельный компас — без спешки.",
        ),
        "push": (
            "Лучше всего читается в чётких контурах и интерфейсных акцентах. 🎛️",
            "Пара: графит и холодный белый; недельный компас — плотный, но управляемый темп.",
        ),
        "no_data": (
            "Уместен в нейтральной среде, где важна ясность без шума. 🧩",
            "Пара: бумажный белый и мягкий серый; недельный компас — ровный режим.",
        ),
        "steady": (
            "Обычно заметен в повседневных деталях и спокойных материалах. 🧵",
            "Пара: молочный и приглушённый синий; недельный компас — последовательность.",
        ),
    }
    life_line, combo_line = variants.get(status_tag, variants["steady"])
    return "\n".join(
        [
            "Акцент дня",
            profile["accent_note"],
            "Отсылка: спокойная дизайнерская практика конца XX — начала XXI века.",
            life_line,
            f"{combo_line} ({week_id})",
        ]
    )


def handle_today_vote_callback(tg_token: str, chat_id: str, callback_query: Dict[str, Any]) -> None:
    callback_id = callback_query.get("id")
    callback_data = callback_query.get("data", "")
    parts = callback_data.split(":")
    vote_date = parts[1] if len(parts) > 1 else dt.date.today().isoformat()
    vote_value = parts[2] if len(parts) > 2 else "partial"

    existing_vote = get_today_vote(chat_id=chat_id, vote_date=vote_date)
    if existing_vote:
        if callback_id:
            telegram_answer_callback(
                tg_token,
                callback_id,
                text=f"Уже учтено: {today_vote_label(existing_vote.get('vote', 'partial'))}",
            )
        message_id = callback_query.get("message", {}).get("message_id")
        if message_id is not None:
            telegram_edit_message_reply_markup(
                tg_token,
                chat_id,
                int(message_id),
                build_today_keyboard(vote_date, existing_vote.get("vote"), history_visible=False),
            )
        return

    saved = upsert_today_vote(chat_id=chat_id, vote_date=vote_date, vote_value=vote_value, vote_ts=utc_now_iso())
    if callback_id:
        telegram_answer_callback(tg_token, callback_id, text="Голос учтён")

    message_id = callback_query.get("message", {}).get("message_id")
    if message_id is not None:
        final_vote = vote_value if saved else "partial"
        telegram_edit_message_reply_markup(
            tg_token,
            chat_id,
            int(message_id),
            build_today_keyboard(vote_date, final_vote, history_visible=False),
        )


def handle_today_story_callback(tg_token: str, chat_id: str, callback_query: Dict[str, Any]) -> None:
    callback_id = callback_query.get("id")
    callback_data = callback_query.get("data", "")
    day = callback_data.split(":", 1)[1] if ":" in callback_data else dt.date.today().isoformat()

    state = get_today_state(chat_id=chat_id, value_date=day)
    if not state:
        week_id = iso_week_id(dt.date.fromisoformat(day))
        week_color = _color_state_for_week(week_id)
        state = upsert_today_state(
            chat_id=chat_id,
            value_date=day,
            state_payload={
                "status_tag": "steady",
                "confidence": 0.4,
                "amplitude": 0.4,
                "accent_hex": generate_daily_accent_hex(chat_id, day, week_id, week_color["hex"]),
                "week_id": week_id,
            },
        )

    telegram_send(tg_token, chat_id, build_accent_story(state))

    existing_vote = get_today_vote(chat_id=chat_id, vote_date=day)
    voted_value = existing_vote.get("vote") if existing_vote else None
    message_id = callback_query.get("message", {}).get("message_id")
    if message_id is not None:
        telegram_edit_message_reply_markup(
            tg_token,
            chat_id,
            int(message_id),
            build_today_keyboard(day, voted_value, history_visible=False),
        )

    if callback_id:
        telegram_answer_callback(tg_token, callback_id)


def handle_today_command(tg_token: str, chat_id: str) -> None:
    day = dt.date.today().isoformat()
    history = load_cache()
    today_payload = history.get(day)
    week_color = get_or_create_weekly_color_state()
    mode_tag = classify_mode_tag(today_payload)
    signal = compute_today_signal(today_payload)

    accent_hex = generate_daily_accent_hex(chat_id, day, week_color["week_id"], week_color["hex"])
    today_state = upsert_today_state(
        chat_id=chat_id,
        value_date=day,
        state_payload={
            "status_tag": mode_tag,
            "confidence": signal["confidence"],
            "amplitude": signal["amplitude"],
            "accent_hex": accent_hex,
            "week_id": week_color["week_id"],
        },
    )

    image_path = generate_today_card_image(
        chat_id=chat_id,
        day=day,
        week_id=week_color["week_id"],
        week_color_hex=week_color["hex"],
        mode_tag=mode_tag,
        accent_hex=today_state.get("accent_hex", accent_hex),
    )

    profile = status_profile(mode_tag)
    fact_block = build_fact_of_day(mode_tag, today_payload)
    caption = (
        f"Статус: {profile['label']}\n"
        f"Почему: {profile['reason']}\n"
        f"Подсказка: {profile['hint']}\n\n"
        f"🟡 Факт дня\n{fact_block}"
    )

    existing_vote = get_today_vote(chat_id=chat_id, vote_date=day)
    vote_value = existing_vote.get("vote") if existing_vote else None
    telegram_send_photo_with_markup(
        tg_token,
        chat_id,
        image_path,
        caption,
        build_today_keyboard(day, vote_value, history_visible=True),
    )

def handle_color_command(tg_token: str, chat_id: str) -> None:
    color = get_or_create_weekly_color_state()
    image_path = generate_color_card_image(color["week_id"], color["hex"])
    today = dt.date.today().isoformat()
    existing_vote = get_color_vote(chat_id=chat_id, vote_date=today)
    keyboard = build_color_keyboard(color["week_id"])
    if existing_vote:
        keyboard = build_color_voted_keyboard(existing_vote.get("vote_value", "partial"))
    telegram_send_photo_with_markup(
        tg_token,
        chat_id,
        image_path,
        build_color_caption(color),
        keyboard,
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
        today = dt.date.today().isoformat()
        existing_vote = get_color_vote(chat_id=chat_id, vote_date=today)
        if existing_vote:
            markup = build_color_voted_keyboard(existing_vote.get("vote_value", "partial"))
        else:
            markup = build_color_vote_keyboard(week_id)
        telegram_edit_message_reply_markup(tg_token, chat_id, int(message_id), markup)

    if callback_id:
        telegram_answer_callback(tg_token, callback_id)


def handle_color_vote_callback(tg_token: str, chat_id: str, callback_query: Dict[str, Any]) -> None:
    callback_id = callback_query.get("id")
    callback_data = callback_query.get("data", "")
    parts = callback_data.split(":")
    week_id = parts[1] if len(parts) > 1 else iso_week_id()
    vote_value = parts[2] if len(parts) > 2 else "partial"
    today = dt.date.today().isoformat()

    existing_vote = get_color_vote(chat_id=chat_id, vote_date=today)
    if existing_vote:
        existing_label = vote_label(existing_vote.get("vote_value", "partial"))
        if callback_id:
            telegram_answer_callback(tg_token, callback_id, text=f"Уже учтено: {existing_label}")
        message = callback_query.get("message", {})
        message_id = message.get("message_id")
        if message_id is not None:
            telegram_edit_message_reply_markup(
                tg_token,
                chat_id,
                int(message_id),
                build_color_voted_keyboard(existing_vote.get("vote_value", "partial")),
            )
        return

    inserted = upsert_color_vote(
        chat_id=chat_id,
        vote_date=today,
        vote_value=vote_value,
        week_id=week_id,
        vote_ts=utc_now_iso(),
    )
    if callback_id:
        telegram_answer_callback(tg_token, callback_id, text="Голос учтён")

    message = callback_query.get("message", {})
    message_id = message.get("message_id")
    if message_id is not None:
        final_vote = vote_value if inserted else "partial"
        telegram_edit_message_reply_markup(
            tg_token,
            chat_id,
            int(message_id),
            build_color_voted_keyboard(final_vote),
        )

    stats = get_weekly_vote_stats(week_id)
    log.info("Color vote stored: week=%s vote=%s stats=%s", week_id, vote_value, stats)


def build_help_message() -> str:
    return "Команды:\n/today\n/color\n/week\n/stats\n/help"


def handle_stats_command(tg_token: str, chat_id: str) -> None:
    week_id = iso_week_id()
    color_stats = get_week_vote_accuracy(week_id=week_id, chat_id=chat_id)
    today_stats = get_today_vote_accuracy(week_id=week_id, chat_id=chat_id)

    color_total = int(color_stats["total"])
    today_total = int(today_stats["total"])
    color_acc = round(color_stats["accuracy"] * 100) if color_total else 0
    today_acc = round(today_stats["accuracy"] * 100) if today_total else 0
    yes_by_rarity = today_stats.get("yes_by_rarity", {"common": 0, "rare": 0, "exotic": 0})

    message = (
        f"Статистика {week_id}\n"
        f"Цвет недели: ✅ {int(color_stats['yes_count'])} · ➖ {int(color_stats['partial_count'])} · ❌ {int(color_stats['no_count'])} · {color_acc}%\n"
        f"Статус дня: ✅ {int(today_stats['yes_count'])} · ➖ {int(today_stats['partial_count'])} · ❌ {int(today_stats['no_count'])} · {today_acc}%\n"
        "Совпадения ✅ по редкости:\n"
        f"классический: {int(yes_by_rarity.get('common', 0))}\n"
        f"редкий: {int(yes_by_rarity.get('rare', 0))}\n"
        f"экзотический: {int(yes_by_rarity.get('exotic', 0))}"
    )
    telegram_send(tg_token, chat_id, message)


def handle_week_command(tg_token: str, chat_id: str) -> None:
    handle_stats_command(tg_token, chat_id)


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
            elif callback_data.startswith("today_vote"):
                handle_today_vote_callback(tg_token, callback_chat_id, callback_query)
            elif callback_data.startswith("today_story"):
                handle_today_story_callback(tg_token, callback_chat_id, callback_query)
            elif callback_data == "noop":
                callback_id = callback_query.get("id")
                if callback_id:
                    telegram_answer_callback(tg_token, callback_id)
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
        if text.lower() == "/today":
            handle_today_command(tg_token, message_chat_id)
            return Response(status_code=200)
        if text.lower() == "/help":
            telegram_send(tg_token, message_chat_id, build_help_message())
            return Response(status_code=200)
        if text.lower() == "/week":
            handle_week_command(tg_token, message_chat_id)
            return Response(status_code=200)
        if text.lower() == "/stats":
            handle_stats_command(tg_token, message_chat_id)
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
    try:
        ensure_bot_commands(env("TELEGRAM_BOT_TOKEN"))
        log.info("Telegram commands ensured")
    except Exception:
        log.exception("Failed to ensure Telegram commands at startup")
    log.info("Starting web server")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 main.py [sync|push|push-self-check|cache-self-check|serve|schedule-debug|schedule-self-check|color-self-check|color-card-self-check|today-card-self-check|today-status-self-check]")
        return

    mode = sys.argv[1].strip().lower()

    if mode == "sync":
        run_sync()
    elif mode == "push":
        push_kind = "scheduled"
        dry_run = False
        for arg in sys.argv[2:]:
            normalized = arg.strip().lower()
            if normalized == "--dry-run":
                dry_run = True
            elif normalized in ["scheduled", "morning", "midday", "evening"]:
                push_kind = normalized
            elif normalized:
                print("Error: push mode args must be [scheduled|morning|midday|evening|--dry-run]")
                return
        run_push(push_kind, dry_run=dry_run)
    elif mode == "push-self-check":
        run_push_self_check()
    elif mode == "cache-self-check":
        run_cache_self_check()
    elif mode == "serve":
        run_serve()
    elif mode == "schedule-debug":
        at_value = None
        chat_id_value = None
        args = sys.argv[2:]
        idx = 0
        while idx < len(args):
            arg = args[idx]
            if arg == "--at" and idx + 1 < len(args):
                at_value = args[idx + 1]
                idx += 2
                continue
            if arg == "--chat-id" and idx + 1 < len(args):
                chat_id_value = args[idx + 1]
                idx += 2
                continue
            print("Error: schedule-debug requires --at <ISO8601> [--chat-id <id>]")
            return
        if not at_value:
            print("Error: schedule-debug requires --at <ISO8601>")
            return
        run_schedule_debug(at_value, chat_id=chat_id_value)
    elif mode == "schedule-self-check":
        run_schedule_self_check()
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
    elif mode == "today-card-self-check":
        problems = self_check_today_card()
        if problems:
            print("today-card-self-check failed")
            for problem in problems:
                print(problem)
            sys.exit(1)
        print("today-card-self-check ok")
    elif mode == "today-status-self-check":
        sample_day = "2026-05-02"
        sample_chat = "self-check"
        sample_week = iso_week_id(dt.date.fromisoformat(sample_day))
        sample_week_color = generate_weekly_color(sample_week).to_dict()
        accent_hex = generate_daily_accent_hex(sample_chat, sample_day, sample_week, sample_week_color["hex"])
        if len(accent_hex) != 7 or not accent_hex.startswith("#"):
            print("today-status-self-check failed")
            print(f"invalid accent hex: {accent_hex}")
            sys.exit(1)
        print("today-status-self-check ok")
    else:
        print(
            f"Error: Unknown mode '{mode}'. Use sync, push, push-self-check, cache-self-check, serve, schedule-debug, schedule-self-check, color-self-check, "
            "color-card-self-check, today-card-self-check, or today-status-self-check."
        )
        sys.exit(1)

if __name__ == "__main__":
    main()
    
    
