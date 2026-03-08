import os
import sys
import json
import logging
import datetime as dt
import re
import requests
import html
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
import google.generativeai as genai
from garminconnect import Garmin

load_dotenv()

from zoneinfo import ZoneInfo
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from cache import (
    callback_dedup_hit,
    get_color_vote,
    get_today_state,
    get_today_vote,
    get_today_vote_accuracy,
    get_week_vote_accuracy,
    get_weekly_vote_stats,
    build_snapshot_merge_diff,
    build_day_context,
    current_day_key,
    get_day_snapshot,
    get_day_summary,
    history_list,
    get_latest_sync_trace,
    load_cache,
    load_cache_with_meta,
    load_weekly_state,
    log_refresh_attempt,
    log_sync_trace,
    mark_slot_sent,
    mark_sent_record,
    mark_weekly_report_sent,
    prune_cache,
    save_daily_snapshot,
    save_weekly_state,
    upsert_day_snapshot,
    upsert_today_state,
    upsert_today_vote,
    upsert_color_vote,
    was_slot_sent,
    was_sent_record,
    get_today_sent_registry,
    get_sent_registry_for_date,
    get_user_prefs,
    was_weekly_report_sent,
    upsert_user_prefs,
    get_garmin_auth_state,
    upsert_bootstrap_state,
    upsert_garmin_auth_state,
    KEY_METRICS,
    METRIC_LABELS,
)
from color_engine import (
    build_color_metaphor_line,
    build_color_story,
    generate_daily_accent_hex,
    generate_today_card_image,
    generate_weekly_color,
    iso_week_id,
    generate_color_card_image,
    generate_weekly_card_image,
    self_check_color_card,
    self_check_color_engine,
    self_check_today_card,
    weekly_color_from_dict,
)


from prompts import SYSTEM_PROMPT, CODEX_RULES
from communication import (
    build_verdict_label,
    build_day_detail_message,
    build_day_verdict_message,
    build_history_message,
    build_metrics_message,
    build_push_message,
    build_weekly_verdict_message,
    build_why_message,
    render_compare_days,
    resolve_intent,
)

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


def telegram_send(token: str, chat_id: str, text: str, parse_mode: Optional[str] = None) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")


def telegram_send_with_markup(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: Dict[str, Any],
    parse_mode: Optional[str] = None,
) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
    if parse_mode:
        payload["parse_mode"] = parse_mode
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
        {"command": "refresh", "description": "обновить данные"},
        {"command": "debug_sync", "description": "диагностика синхронизации"},
        {"command": "debug_sent", "description": "что отправлено сегодня"},
    ]
    response = requests.post(url, json={"commands": commands}, timeout=15)
    if response.status_code != 200:
        raise RuntimeError(f"Telegram setMyCommands error {response.status_code}: {response.text}")
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram setMyCommands rejected: {payload}")


def utc_now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _new_run_id(prefix: str) -> str:
    return f"{prefix}-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _garmin_last_sync(raw: Dict[str, Any]) -> str:
    for key in ("last_sync_time", "lastSyncTimestampGMT", "lastSyncTimestampLocal"):
        value = raw.get(key)
        if value:
            return str(value)
    return str(raw.get("fetched_at_utc", ""))


def fetch_garmin_minimal(email: str, password: str) -> Dict[str, Any]:
    api = Garmin(email, password)
    auth_state = get_garmin_auth_state()
    tokenstore = auth_state.get("tokenstore") if isinstance(auth_state, dict) else None
    try:
        if isinstance(tokenstore, str) and tokenstore.strip():
            api.login(tokenstore=tokenstore)
        else:
            api.login()
    except Exception:
        api.login()
    try:
        serialized = api.garth.dumps() if hasattr(api, "garth") else ""
        if serialized:
            upsert_garmin_auth_state({"tokenstore": serialized})
    except Exception:
        log.warning("garmin_tokenstore_persist_failed", exc_info=True)

    today = current_day_key()
    out: Dict[str, Any] = {
        "source": "garmin",
        "date": today,
        "fetched_at_utc": utc_now_iso(),
        "last_sync_time": utc_now_iso(),
        "errors": [],
    }

    calls = {
        "body_battery": "get_body_battery",
        "stress": "get_stress_data",
        "sleep": "get_sleep_data",
        "rhr": "get_rhr_day",
        "steps": "get_steps_data",
        "heart_rate": "get_heart_rates",
        "daily_activity": "get_user_summary",
        "intensity_minutes": "get_intensity_minutes_data",
        "calories": "get_calories_data",
        "floors": "get_floors",
        "respiration": "get_respiration_data",
        "pulse_ox": "get_pulse_ox_data",
        "hrv": "get_hrv_data",
        "hrv_status": "get_hrv_status_data",
        "activity_summary": "get_activities_by_date",
    }

    for key, method_name in calls.items():
        method = getattr(api, method_name, None)
        if not callable(method):
            continue
        try:
            out[key] = method(today)
        except Exception as e:
            out["errors"].append({"metric": key, "error": str(e)})

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
    run_id = _new_run_id("sync")
    garmin_email = env("GARMIN_EMAIL")
    garmin_password = env("GARMIN_PASSWORD")
    day_key = current_day_key()
    source_fetch_ts = utc_now_iso()
    try:
        data = fetch_garmin_minimal(garmin_email, garmin_password)
        data["date"] = day_key
        before = get_day_snapshot(day_key)
        after = upsert_day_snapshot(day_key, data)
        diff = build_snapshot_merge_diff(before, after)
        trace = {
            "run_id": run_id,
            "stage": "sync",
            "source_fetch_ts": source_fetch_ts,
            "cache_write_ts": utc_now_iso(),
            "snapshot_date_key": day_key,
            "last_sync_time": _garmin_last_sync(data),
            "updated_blocks": diff["updated_blocks"],
            "old_completeness": diff["old_completeness"],
            "new_completeness": diff["new_completeness"],
            "old_confidence": diff["old_confidence"],
            "new_confidence": diff["new_confidence"],
            "had_real_updates": diff["had_real_updates"],
            "runtime_cache_source": "local",
            "runtime_cache_available": True,
            "gist_upload_status": "pending_external_workflow_step",
        }
        log_sync_trace(run_id, trace)
        log.info("Sync ok run_id=%s updated_blocks=%s", run_id, diff["updated_blocks"])
    except Exception as e:
        msg = str(e).lower()
        is_auth_problem = "login" in msg or "auth" in msg or "credential" in msg or "password" in msg
        if is_auth_problem:
            log.error("Sync failed: Garmin credentials rejected, cache untouched")
            raise RuntimeError("Garmin credentials are invalid or rejected") from e
        log.exception("Sync failed")
        error_data = {
            "source": "garmin",
            "date": day_key,
            "error": "sync_failed",
            "errors": [{"metric": "sync", "error": str(e)}],
            "fetched_at_utc": utc_now_iso(),
        }
        upsert_day_snapshot(day_key, error_data)
        log_sync_trace(run_id, {
            "run_id": run_id,
            "stage": "sync",
            "source_fetch_ts": source_fetch_ts,
            "cache_write_ts": utc_now_iso(),
            "snapshot_date_key": day_key,
            "updated_blocks": [],
            "had_real_updates": False,
            "error": str(e),
            "runtime_cache_source": "local",
            "runtime_cache_available": True,
        })
        raise


TZ_MSK_FIXED = ZoneInfo("Europe/Moscow")


def _now_msk() -> dt.datetime:
    forced = os.getenv("PUSH_NOW_MSK", "").strip()
    if forced:
        parsed = dt.datetime.fromisoformat(forced)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ_MSK_FIXED)
        return parsed.astimezone(TZ_MSK_FIXED)
    return dt.datetime.now(TZ_MSK_FIXED)


SLOT_WINDOWS: Dict[str, Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]] = {
    "morning": ((9, 15), (9, 30), (9, 45)),
    "midday": ((13, 40), (14, 0), (14, 20)),
    "evening": ((19, 40), (20, 0), (20, 20)),
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
    already_sent = _already_sent_for_slot(chat_id=chat_id, send_date=today_str, slot=slot)
    return {
        "now_msk": now_msk.isoformat(),
        "window_matched": window_slot if window_slot is not None else "none",
        "slot_id": slot,
        "already_sent": already_sent,
        "target_chat_id": chat_id,
        "date": today_str,
    }




def _already_sent_for_slot(chat_id: str, send_date: str, slot: str) -> bool:
    message_types = ["verdict"]
    if slot == "morning":
        message_types.append("color")
    return any(was_sent_record(chat_id=chat_id, send_date=send_date, slot=slot, message_type=mt) for mt in message_types)


def _state_to_asset(status_tag: str) -> str:
    mapping = {
        "push": "machine",
        "recovery": "soft_mash",
        "steady": "cruise",
        "no_data": "zen",
        "high": "battle_club",
        "border": "focused",
        "low": "soft_mash",
        "overload": "overheated",
    }
    state = mapping.get(status_tag, "zen")
    return os.path.join("assets", "coach_states", f"{state}.png")


def _build_debug_sent_message(chat_id: str, send_date: str) -> str:
    records = get_today_sent_registry(chat_id=chat_id, send_date=send_date)
    if not records:
        return f"sent-registry {send_date}: пусто"
    lines = [f"sent-registry {send_date}:"]
    for key in sorted(records.keys()):
        payload = records[key] if isinstance(records[key], dict) else {}
        lines.append(
            f"• {key} | ts={payload.get('ts','')} | source={payload.get('trigger_source','')} | run_id={payload.get('run_id','')}"
        )
    return "\n".join(lines)


def _slot_button_row(slot: str, day_key: str) -> List[Dict[str, str]]:
    return [
        {"text": "Почему?", "callback_data": f"why:{slot}:{day_key}"},
        {"text": "По фактам", "callback_data": f"facts:{slot}:{day_key}"},
        {"text": "Пожарь", "callback_data": f"roast:{slot}:{day_key}"},
        {"text": "Что делать (15м)", "callback_data": f"what15:{slot}:{day_key}"},
    ]


def _build_verdict_keyboard(slot: str, day_key: str) -> Dict[str, Any]:
    return {"inline_keyboard": [_slot_button_row(slot, day_key)]}


def _send_once(
    tg_token: str,
    chat_id: str,
    send_date: str,
    slot: str,
    message_type: str,
    trigger_source: str,
    run_id: str,
    sender: Any,
    force: bool = False,
) -> bool:
    if (not force) and was_sent_record(chat_id=chat_id, send_date=send_date, slot=slot, message_type=message_type):
        log.info("dedupe_skip key=%s|%s|%s|%s source=%s", chat_id, send_date, slot, message_type, trigger_source)
        return False
    sender()
    mark_sent_record(
        chat_id=chat_id,
        send_date=send_date,
        slot=slot,
        message_type=message_type,
        sent_ts=utc_now_iso(),
        trigger_source=trigger_source,
        run_id=run_id,
    )
    return True


def _is_admin(chat_id: str) -> bool:
    owner = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    admins_raw = os.getenv("ADMIN_CHAT_IDS", "").strip()
    admins = {item.strip() for item in admins_raw.split(",") if item.strip()}
    if owner:
        admins.add(owner)
    return chat_id in admins


def _callback_duplicate_guard(tg_token: str, chat_id: str, callback_query: Dict[str, Any]) -> bool:
    callback_id = str(callback_query.get("id", "")).strip()
    callback_data = str(callback_query.get("data", "")).strip()
    message_id = str((callback_query.get("message") or {}).get("message_id", "")).strip()
    button_id = callback_data.split(":", 1)[0] if callback_data else "unknown"
    key = f"{message_id}|{button_id}"
    if not message_id:
        key = callback_data or callback_id
    if not key:
        return False
    is_dup = callback_dedup_hit(chat_id=chat_id, callback_key=key, ttl_seconds=45)
    if is_dup and callback_id:
        telegram_answer_callback(tg_token, callback_id, text="Уже показал выше")
        log.info("callback_dedup_skip chat_id=%s callback=%s", chat_id, key)
    return is_dup

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
    slot_title = _slot_title(slot)
    return (
        f"🟡 <b>{slot_title}</b>\n\n"
        "<i>Оценка предварительная: данных пока недостаточно.</i>\n"
        "Главный смысл: пока держим ровный режим без резких решений.\n"
        "<b>Лучшее действие:</b> один спокойный блок и короткая пауза.\n"
        "<b>Ограничение:</b> не добавляй резкую нагрузку до следующей синхронизации.\n"
        "<b>Надёжность:</b> низкая (неполный набор метрик).\n"
        "Система обновит вывод автоматически после новых данных Garmin."
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


def _slot_title(slot: str) -> str:
    return {
        "morning": "Старт дня",
        "midday": "Сверка в середине дня",
        "evening": "Мягкое завершение дня",
    }.get(slot, "Сигнал дня")


def _metric_present(value: Any) -> bool:
    return isinstance(value, dict) and any(v is not None for v in value.values())


def _evaluate_data_quality(today_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    available = []
    if isinstance(today_payload, dict):
        available = [metric for metric in KEY_METRICS if _metric_present(today_payload.get(metric))]
    missing = [metric for metric in KEY_METRICS if metric not in available]
    present = len(available)
    if present <= 1:
        quality_label = "низкая"
        is_partial = True
    elif present <= 2:
        quality_label = "средняя"
        is_partial = True
    else:
        quality_label = "высокая"
        is_partial = False
    return {
        "is_partial": is_partial,
        "present": present,
        "quality_label": quality_label,
        "missing_metrics": missing,
        "available_metrics": available,
        "missing_labels": [METRIC_LABELS[m] for m in missing],
        "available_labels": [METRIC_LABELS[m] for m in available],
    }


def _confidence_text(today_payload: Optional[Dict[str, Any]], quality: Dict[str, Any]) -> str:
    if quality["is_partial"]:
        return f"Надёжность оценки: {quality['quality_label']} (данные неполные)."
    if isinstance(today_payload, dict):
        return "Надёжность оценки: высокая (сигнал собран по ключевым метрикам)."
    return "Надёжность оценки: низкая (данных недостаточно)."


def _build_partial_data_variant(slot: str, quality: Dict[str, Any]) -> str:
    slot_text = _slot_title(slot)
    missing_labels = quality.get("missing_labels", []) if isinstance(quality, dict) else []
    available_labels = quality.get("available_labels", []) if isinstance(quality, dict) else []
    have_line = ", ".join(available_labels) if available_labels else "пока нет"
    missing_line = ", ".join(missing_labels[:4]) if missing_labels else "—"
    return (
        f"🟡 <b>{slot_text}</b>\n\n"
        "<b>Статус:</b> предварительная оценка\n\n"
        f"<b>Уже есть:</b> {have_line}\n"
        f"<b>Не хватает:</b> {missing_line}\n"
        f"<b>Ключевые метрики:</b> {quality['present']} из {len(KEY_METRICS)}\n\n"
        "<b>Лучшее действие:</b> один короткий спокойный блок и пауза 5–7 минут.\n"
        "<b>Ограничение:</b> не повышай нагрузку до следующей синхронизации.\n"
        f"<b>Надёжность:</b> {quality['quality_label']}.\n"
        "Автообновление: вывод уточнится после новых данных Garmin."
    )


def build_morning_push(scores: Dict[str, float], confidence_line: str, color_name: str, color_story_lines: list[str], day: str) -> str:
    icon, label = _status_line(scores)
    message = (
        f"{icon} <b>{_slot_title('morning')}</b>\n\n"
        f"<i>Старт читается как {label.lower()}.</i>\n"
        "Главный смысл: первую половину дня лучше вести в ровном темпе.\n"
        "<b>Лучшее действие:</b> один фокус-блок 60–90 минут без отвлечений.\n"
        "<b>Ограничение:</b> не начинай день с резкого спринта задач.\n"
        f"<b>Надёжность:</b> {confidence_line.replace('Надёжность оценки: ', '').replace('.', '')}."
    )
    return message


def build_day_push(scores: Dict[str, float], confidence_line: str) -> str:
    icon, label = _status_line(scores)
    return (
        f"{icon} <b>{_slot_title('midday')}</b>\n\n"
        f"<i>Это коррекция курса: сейчас {label.lower()}.</i>\n"
        "Главный смысл: середина дня показывает запас на вторую половину.\n"
        "<b>Лучшее действие:</b> пауза 7 минут без экрана и затем один приоритетный блок.\n"
        "<b>Ограничение:</b> не добирай темп резким ускорением.\n"
        f"<b>Надёжность:</b> {confidence_line.replace('Надёжность оценки: ', '').replace('.', '')}."
    )


def build_evening_push(scores: Dict[str, float], confidence_line: str, today_vote: Optional[Dict[str, Any]], day: str) -> str:
    icon, label = _status_line(scores)
    vote_line = ""
    if isinstance(today_vote, dict):
        vote_map = {"yes": "✅", "partial": "🤷", "no": "❌"}
        vote_icon = vote_map.get(str(today_vote.get("vote", "")), "🤷")
        vote_line = f"\nТвой отклик по дню: {vote_icon}."
    message = (
        f"{icon} <b>{_slot_title('evening')}</b>\n\n"
        f"<i>Финал дня: {label.lower()}.</i>\n"
        "Главный смысл: ресурс лучше направить в мягкое торможение.\n"
        "<b>Лучшее действие:</b> приглуши свет и снизь шум за 1.5–2 часа до сна.\n"
        "<b>Ограничение:</b> без рабочих добивок и тяжёлых разговоров перед сном.\n"
        f"<b>Надёжность:</b> {confidence_line.replace('Надёжность оценки: ', '').replace('.', '')}."
        f"{vote_line}"
    )
    return message


def build_morning_color_caption(color: Dict[str, Any]) -> str:
    color_obj = weekly_color_from_dict(color)
    rarity_label = map_rarity_ru(color_obj.rarity_level)
    focus_line = build_color_metaphor_line(color_obj)
    return (
        "🎨 <b>Цвет дня</b>\n\n"
        f"{html.escape(color_obj.name_ru)} — {html.escape(focus_line)}.\n"
        f"<b>HEX:</b> {html.escape(color_obj.hex)}\n"
        f"<b>Тема недели:</b> {html.escape(color_obj.week_id)} · {html.escape(rarity_label)}\n"
        "<b>Фокус:</b> держать ровный темп и не дробить внимание."
    )


def _build_scheduled_message(
    slot: str,
    today_payload: Optional[Dict[str, Any]],
    color_name: str,
    color_story_lines: list[str],
    day: str,
    today_vote: Optional[Dict[str, Any]],
    speech_mode: str = "short",
) -> str:
    quality = _evaluate_data_quality(today_payload)
    return build_push_message(
        slot=slot,
        snapshot=today_payload,
        day_key=day,
        partial=bool(quality["is_partial"]),
        mode=speech_mode,
    )


def _safe_today_payload(cache: Dict[str, Any], day: str) -> Optional[Dict[str, Any]]:
    raw = cache.get(day)
    return raw if isinstance(raw, dict) else None


def collect_weekly_data(full_history: Dict[str, Any], now_msk: dt.datetime) -> List[Dict[str, Any]]:
    days: List[Dict[str, Any]] = []
    for delta in range(6, -1, -1):
        day = (now_msk.date() - dt.timedelta(days=delta)).isoformat()
        payload = full_history.get(day)
        if not isinstance(payload, dict):
            payload = {}
        days.append({"date": day, "payload": payload})
    return days


def _weekly_source_fingerprint(weekly_days: List[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for row in weekly_days:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        rows.append(
            "|".join(
                [
                    str(row.get("date", "")),
                    str(payload.get("last_sync_time", "")),
                    str(payload.get("fetched_at_utc", "")),
                    str(payload.get("data_completeness", "")),
                    str(payload.get("sleep", "")),
                    str(payload.get("stress", "")),
                    str(payload.get("body_battery", "")),
                    str(payload.get("rhr", "")),
                ]
            )
        )
    return "\n".join(rows)


def _day_signal(payload: Dict[str, Any]) -> Dict[str, Any]:
    body = payload.get("body_battery") if isinstance(payload.get("body_battery"), dict) else {}
    stress = payload.get("stress") if isinstance(payload.get("stress"), dict) else {}
    sleep = payload.get("sleep") if isinstance(payload.get("sleep"), dict) else {}

    body_level = body.get("mostRecentValue") or body.get("chargedValue")
    stress_level = stress.get("avgStressLevel") or stress.get("overallStressLevel")
    sleep_seconds = sleep.get("sleepTimeSeconds") or sleep.get("totalSleepSeconds")
    completeness = payload.get("data_completeness")
    if not isinstance(completeness, (int, float)):
        present = sum(1 for value in [body_level, stress_level, sleep_seconds] if isinstance(value, (int, float)))
        completeness = round(present / 3, 2)

    if completeness < 0.5:
        return {"status": "partial", "score": 0.0, "completeness": completeness}

    score = 0.0
    if isinstance(body_level, (int, float)):
        score += (float(body_level) - 50.0) / 40.0
    if isinstance(stress_level, (int, float)):
        score += (45.0 - float(stress_level)) / 35.0
    if isinstance(sleep_seconds, (int, float)):
        score += ((float(sleep_seconds) / 3600.0) - 7.0) / 2.0

    if score >= 0.85:
        status = "best"
    elif score <= -0.5:
        status = "tense"
    else:
        status = "moderate"
    return {"status": status, "score": round(score, 2), "completeness": completeness}


def derive_weekly_status(weekly_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    points = []
    scores: List[float] = []
    partial_count = 0
    no_data_count = 0
    best_count = 0
    tense_count = 0
    available_days = 0

    for row in weekly_days:
        payload = row["payload"] if isinstance(row.get("payload"), dict) else {}
        signal = _day_signal(payload)
        has_any_data = any(isinstance(payload.get(metric), dict) and bool(payload.get(metric)) for metric in ("sleep", "stress", "body_battery", "rhr"))
        if not has_any_data:
            no_data_count += 1
            points.append({"date": row["date"], "status": "no_data"})
            continue
        available_days += 1
        points.append({"date": row["date"], "status": signal["status"]})
        if signal["status"] == "partial":
            partial_count += 1
            continue
        scores.append(signal["score"])
        if signal["status"] == "best":
            best_count += 1
        if signal["status"] == "tense":
            tense_count += 1

    if available_days < 3:
        hero = f"Ранний черновик недели ({available_days} дн.)"
    elif available_days < 7:
        hero = f"Неделя по доступным дням ({available_days})"
    elif len(scores) >= 4:
        spread = max(scores) - min(scores)
        if spread >= 2.0:
            hero = "Неделя с перегибами"
        elif scores[-1] < scores[0] - 0.7:
            hero = "Темп просел к концу"
        elif best_count >= 3 and tense_count <= 1:
            hero = "Неделя ровная"
        else:
            hero = "Ритм менялся по дням"
    else:
        hero = "Неделя предварительная"

    return {
        "hero_status": hero,
        "day_points": points,
        "incomplete_days": partial_count,
        "partial_days": partial_count,
        "no_data_days": no_data_count,
        "available_days": available_days,
        "scores": scores,
        "best_days": best_count,
        "tense_days": tense_count,
        "strongest_period": "день",
        "period_ratio": {"утро": 0.0, "день": 0.0, "вечер": 0.0},
        "stability": round((sum(scores) / len(scores)) if scores else 0.0, 2),
    }


def build_human_weekly_chips(derived: Dict[str, Any], week_id: str, chat_id: str) -> List[str]:
    color_acc = get_week_vote_accuracy(week_id, chat_id=chat_id)
    total = int(color_acc.get("total", 0))
    hit_score = int(color_acc.get("yes_count", 0)) + int(color_acc.get("partial_count", 0))
    color_chip = f"🎨 Цветовой отклик: {hit_score} из {max(1, total)} дней"
    available_chip = f"📅 Доступных дней: {derived.get('available_days', 0)}"
    quality_chip = f"☁️ Неполных: {derived.get('incomplete_days', 0)}; без данных: {derived.get('no_data_days', 0)}"
    return [color_chip, available_chip, quality_chip]


def generate_weekly_quest(derived: Dict[str, Any], weekly_days: List[Dict[str, Any]]) -> str:
    period = derived["strongest_period"]
    partial_days = derived["partial_days"]
    stability = float(derived.get("stability", 0.0))
    best = int(derived.get("best_days", 0))
    tense = int(derived.get("tense_days", 0))

    templates = {
        "evening_weak": [
            "Два вечера заверши на 30 минут раньше обычного.",
            "В ближайшие 2 вечера не открывай новый тяжёлый блок после 20:00.",
            "Сделай 2 мягких завершения дня: без поздних задач и резких переключений.",
        ],
        "low_data": [
            "Собери 3 дня подряд полную синхронизацию до вечера и сравни итог дня.",
            "Проверь 2 вечера подряд: полная синхронизация до 21:00 и без пропусков.",
            "Сделай мини-эксперимент: 3 дня с полной синхронизацией и одним режимом сна.",
        ],
        "good_rhythm": [
            "Повтори лучший сценарий недели ещё 2 раза в ближайшие дни.",
            "Удержи сильный утренний паттерн минимум в 2 будних днях.",
            "Закрепи рабочий ритм: два дня с тем же стартом и мягким завершением.",
        ],
        "rough_week": [
            "Сократи лишние переключения: один день с фокусом на 2 главных блока.",
            "Сделай 1 день со спокойным стартом без тяжёлого блока в первый час.",
            "Убери один поздний тяжёлый блок и проверь, как меняется вечерний фон.",
        ],
    }

    if partial_days >= 3:
        return templates["low_data"][partial_days % len(templates["low_data"])]
    if period == "утро" and tense >= 2:
        return templates["evening_weak"][tense % len(templates["evening_weak"])]
    if stability >= 0.42 and best >= 3:
        return templates["good_rhythm"][best % len(templates["good_rhythm"])]
    return templates["rough_week"][(best + tense) % len(templates["rough_week"])]


def build_weekly_map_lines(day_points: List[Dict[str, Any]], weekly_days: List[Dict[str, Any]]) -> List[str]:
    icon_map = {"best": "🟢", "moderate": "🟡", "tense": "🟠", "partial": "🟣", "no_data": "⚪"}
    payload_by_day = {
        str(row.get("date", "")): (row.get("payload") if isinstance(row.get("payload"), dict) else {})
        for row in weekly_days
    }
    lines: List[str] = []
    for point in day_points:
        day = str(point.get("date", ""))
        status = str(point.get("status", "no_data"))
        payload = payload_by_day.get(day, {})
        battery = payload.get("body_battery", {}) if isinstance(payload.get("body_battery"), dict) else {}
        bb = battery.get("mostRecentValue")
        value = str(int(bb)) if isinstance(bb, (int, float)) else "нет"
        lines.append(f"{icon_map.get(status, '⚪')} {day[5:]} · {value}")
    return lines




def build_weekly_caption(derived: Dict[str, Any], chips: List[str], quest: str) -> str:
    return build_weekly_verdict_message(derived, chips, quest)


def build_weekly_keyboard(week_id: str) -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Мой паттерн", "callback_data": f"weekly_pattern:{week_id}"},
                {"text": "Что улучшить", "callback_data": f"weekly_improve:{week_id}"},
            ],
            [{"text": "История цвета", "callback_data": f"color_story:{week_id}"}],
        ]
    }


def build_pattern_response(derived: Dict[str, Any]) -> str:
    return (
        f"Мой паттерн недели: {derived['hero_status'].lower()}. "
        f"Сильнее выглядел период «{derived['strongest_period']}», "
        f"дней с напряжением: {derived['tense_days']}, неполных дней: {derived['partial_days']}."
    )


def build_improvement_response(derived: Dict[str, Any], quest: str) -> str:
    steps = [
        "1) Держи один стабильный старт дня без спешки.",
        "2) Снизь вечерние переключения хотя бы в 2 днях.",
        f"3) Фокус недели: {quest}",
    ]
    if derived["partial_days"] >= 2:
        steps[1] = "2) Добавь 2–3 дня с полной синхронизацией до вечера."
    return "Что улучшить:\n" + "\n".join(steps)

def build_weekly_payload(full_history: Dict[str, Any], now_msk: dt.datetime, chat_id: str) -> Dict[str, Any]:
    weekly_days = collect_weekly_data(full_history, now_msk)
    derived = derive_weekly_status(weekly_days)
    week_id = iso_week_id(now_msk.date())
    chips = build_human_weekly_chips(derived, week_id, chat_id)
    quest = generate_weekly_quest(derived, weekly_days)
    caption = build_weekly_caption(derived, chips, quest)
    map_lines = build_weekly_map_lines(derived.get("day_points", []), weekly_days)
    caption = caption + "\n\n🗺 <b>Карта недели</b>\n" + "\n".join(map_lines)
    return {
        "week_id": week_id,
        "derived": derived,
        "chips": chips,
        "quest": quest,
        "caption": caption,
        "map_lines": map_lines,
        "source_fingerprint": _weekly_source_fingerprint(weekly_days),
    }


def send_weekly_report(tg_token: str, chat_id: str, full_history: Dict[str, Any], now_msk: dt.datetime) -> None:
    payload = build_weekly_payload(full_history, now_msk, chat_id)
    week_id = payload["week_id"]
    save_weekly_state(
        week_id,
        {
            "week_id": week_id,
            "hero_status": payload["derived"]["hero_status"],
            "quest": payload["quest"],
            "strongest_period": payload["derived"]["strongest_period"],
            "partial_days": payload["derived"]["partial_days"],
            "stability": payload["derived"]["stability"],
            "chips": payload["chips"],
            "source_fingerprint": payload.get("source_fingerprint", ""),
        },
    )
    telegram_send_with_markup(
        tg_token,
        chat_id,
        payload["caption"],
        build_weekly_keyboard(week_id),
        parse_mode="HTML",
    )


def run_push(push_kind: str, dry_run: bool = False) -> None:
    tg_token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    now_msk = _now_msk()
    now_utc = dt.datetime.now(dt.timezone.utc)
    run_id = _new_run_id("push")
    trigger_source = "schedule" if push_kind == "scheduled" else "manual"

    prune_summary = prune_cache()
    log.info("cache_prune summary=%s", prune_summary)
    log.info("push_kind received=%s", push_kind)
    if push_kind == "scheduled":
        decision = _build_schedule_decision(now_msk, chat_id)
        resolved_slot = decision["slot_id"]
    else:
        resolved_slot = push_kind
        decision = _build_schedule_decision(now_msk, chat_id, override=resolved_slot)

    _log_schedule_decision(decision)
    log.info("push_clock now_utc=%s now_msk=%s", now_utc.isoformat(), now_msk.isoformat())
    today_str = decision["date"]

    if decision["already_sent"]:
        log.info("dedupe_skip slot=%s date=%s chat_id=%s", resolved_slot, today_str, chat_id)
        return

    if dry_run:
        log.info("send_result=dry_run would_send=true slot_id=%s", resolved_slot)
        return

    log.info("Push started: kind=%s slot=%s msk_now=%s utc_now=%s", push_kind, resolved_slot, now_msk.isoformat(), now_utc.isoformat())
    deferred_slot = None
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
        if resolved_slot == "morning":
            week_color = get_or_create_weekly_color_state()
            accent_hex = generate_daily_accent_hex(chat_id, today_str, week_color["week_id"], week_color["hex"])
            image_path = generate_today_card_image(
                chat_id=chat_id,
                day=today_str,
                week_id=week_color["week_id"],
                week_color_hex=week_color["hex"],
                mode_tag="no_data",
                accent_hex=accent_hex,
            )
            telegram_send_photo_with_markup(
                tg_token,
                chat_id,
                image_path,
                _build_fallback_message(resolved_slot),
                {"inline_keyboard": []},
                parse_mode="HTML",
            )
        else:
            _send_push_fallback(tg_token, chat_id, _build_fallback_message(resolved_slot))
        if deferred_slot is not None:
            mark_sent_record(chat_id=chat_id, send_date=today_str, slot=deferred_slot, message_type="verdict", sent_ts=utc_now_iso(), trigger_source=trigger_source, run_id=run_id)
            log.info("dedupe_marked slot=%s date=%s chat_id=%s type=verdict run_id=%s", deferred_slot, today_str, chat_id, run_id)
        else:
            mark_sent_record(chat_id=chat_id, send_date=today_str, slot=resolved_slot, message_type="verdict", sent_ts=utc_now_iso(), trigger_source=trigger_source, run_id=run_id)
            log.info("dedupe_marked slot=%s date=%s chat_id=%s type=verdict run_id=%s", resolved_slot, today_str, chat_id, run_id)
        log.info("send_result=ok fallback=true reason=%s slot_id=%s cache_source=%s cache_available=%s", reason_code, resolved_slot, cache_meta.get("source", "unknown"), cache_meta.get("available", False))
        return

    day_summary = get_day_summary(today_str, cache_data=full_history, chat_id=chat_id)
    day_context = build_day_context(day_key=today_str, cache_data=full_history)
    today_payload = day_summary.get("snapshot") if isinstance(day_summary.get("snapshot"), dict) else _safe_today_payload(full_history, today_str)
    quality = {
        "is_partial": day_summary.get("completeness_state") != "FULL",
        "present": day_context.get("key_metrics_present_count", 0),
        "quality_label": "высокая" if day_context.get("day_status") == "ready" else ("средняя" if day_context.get("key_metrics_present_count", 0) >= 2 else "низкая"),
        "missing_labels": [METRIC_LABELS.get(m, m) for m in day_context.get("missing_metrics", []) if m in KEY_METRICS],
        "available_labels": [METRIC_LABELS.get(m, m) for m in day_context.get("available_metrics", []) if m in KEY_METRICS],
    }
    if isinstance(today_payload, dict):
        log.info("cache_freshness date=%s last_sync_time=%s fetched_at_utc=%s", today_str, today_payload.get("last_sync_time", ""), today_payload.get("fetched_at_utc", ""))
    if push_kind == "scheduled" and resolved_slot == "morning" and quality.get("is_partial", True):
        if not was_slot_sent(chat_id=chat_id, send_date=today_str, slot="morning_deferred"):
            deferred_slot = "morning_deferred"
            mark_sent_record(
                chat_id=chat_id,
                send_date=today_str,
                slot="morning_deferred",
                message_type="verdict",
                sent_ts=utc_now_iso(),
                trigger_source="deferred_window",
                run_id=run_id,
            )
            log.info("deferred_window_open slot=morning present=%s", quality.get("present", 0))
    should_catch_up_morning = (
        push_kind == "scheduled"
        and resolved_slot != "morning"
        and was_slot_sent(chat_id=chat_id, send_date=today_str, slot="morning_deferred")
        and not was_slot_sent(chat_id=chat_id, send_date=today_str, slot="morning")
        and not quality.get("is_partial", True)
        and now_msk.hour <= 15
    )
    if should_catch_up_morning:
        log.info("deferred_catchup_triggered from_slot=%s", resolved_slot)
        resolved_slot = "morning"
    week_color = get_or_create_weekly_color_state()
    color_story_text = build_color_story(weekly_color_from_dict(week_color))
    color_story_lines = [line.strip() for line in color_story_text.splitlines()[1:] if line.strip()][:4]
    today_vote = get_today_vote(chat_id=chat_id, vote_date=today_str)
    speech_mode = str(get_user_prefs(chat_id).get("speech_mode", "short"))
    msg = _build_scheduled_message(
        slot=resolved_slot,
        today_payload=today_payload,
        color_name=str(week_color.get("name_ru", "без названия")),
        color_story_lines=color_story_lines,
        day=today_str,
        today_vote=today_vote,
        speech_mode=speech_mode,
    )

    try:
        if resolved_slot == "morning":
            color_image_path = generate_color_card_image(week_color["week_id"], week_color["hex"])
            today_color_vote = get_color_vote(chat_id=chat_id, vote_date=today_str)
            color_keyboard = build_color_keyboard(week_color["week_id"])
            if today_color_vote:
                color_keyboard = build_color_voted_keyboard(today_color_vote.get("vote_value", "partial"))
            _send_once(
                tg_token=tg_token,
                chat_id=chat_id,
                send_date=today_str,
                slot=resolved_slot,
                message_type="color",
                trigger_source=trigger_source,
                run_id=run_id,
                sender=lambda: telegram_send_photo_with_markup(
                    tg_token,
                    chat_id,
                    color_image_path,
                    build_morning_color_caption(week_color),
                    color_keyboard,
                    parse_mode="HTML",
                ),
            )

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
            mascot_asset = _state_to_asset(mode_tag)
            verdict_sent = _send_once(
                tg_token=tg_token,
                chat_id=chat_id,
                send_date=today_str,
                slot=(deferred_slot or resolved_slot),
                message_type="verdict",
                trigger_source=trigger_source,
                run_id=run_id,
                sender=lambda: telegram_send_photo_with_markup(
                    tg_token,
                    chat_id,
                    mascot_asset,
                    msg,
                    _build_verdict_keyboard("morning", today_str),
                    parse_mode="HTML",
                ) if os.path.exists(mascot_asset) else telegram_send_with_markup(
                    tg_token,
                    chat_id,
                    msg,
                    _build_verdict_keyboard("morning", today_str),
                    parse_mode="HTML",
                ),
            )
        else:
            verdict_sent = _send_once(
                tg_token=tg_token,
                chat_id=chat_id,
                send_date=today_str,
                slot=(deferred_slot or resolved_slot),
                message_type="verdict",
                trigger_source=trigger_source,
                run_id=run_id,
                sender=lambda: telegram_send_with_markup(tg_token, chat_id, msg, _build_verdict_keyboard(resolved_slot, today_str), parse_mode="HTML"),
            )

        if not verdict_sent:
            return

        is_sunday_evening = resolved_slot == "evening" and now_msk.isoweekday() == 7
        if is_sunday_evening:
            week_id = iso_week_id(now_msk.date())
            if was_weekly_report_sent(chat_id=chat_id, week_id=week_id):
                log.info("weekly_report_skip week_id=%s chat_id=%s", week_id, chat_id)
            else:
                send_weekly_report(tg_token, chat_id, full_history, now_msk)
                mark_weekly_report_sent(chat_id=chat_id, week_id=week_id, sent_ts=utc_now_iso())
                mark_sent_record(chat_id=chat_id, send_date=today_str, slot="evening", message_type="weekly", sent_ts=utc_now_iso(), trigger_source=trigger_source, run_id=run_id)
                log.info("weekly_report_sent week_id=%s chat_id=%s run_id=%s", week_id, chat_id, run_id)

        log.info("send_result=ok fallback=%s slot_id=%s", str(today_payload is None).lower(), resolved_slot)
    except Exception:
        log.exception("Push send failed")
        _send_push_fallback(tg_token, chat_id, f"Слот {resolved_slot}: отправка не удалась, повторю позже.")
        mark_sent_record(
            chat_id=chat_id,
            send_date=today_str,
            slot=(deferred_slot or resolved_slot),
            message_type="verdict",
            sent_ts=utc_now_iso(),
            trigger_source="retry_fallback",
            run_id=run_id,
        )
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
        now_msk = now_msk.replace(tzinfo=TZ_MSK_FIXED)
    else:
        now_msk = now_msk.astimezone(TZ_MSK_FIXED)
    decision = _build_schedule_decision(now_msk, raw_chat_id)
    _log_schedule_decision(decision)
    would_send = decision["slot_id"] is not None and not decision["already_sent"]
    print(f"would_send={str(would_send).lower()}")


def run_schedule_self_check() -> None:
    checks = [
        "2026-02-24T08:31:00+03:00",
        "2026-02-24T13:06:00+03:00",
        "2026-02-24T19:12:00+03:00",
    ]
    for at_iso in checks:
        now_msk = dt.datetime.fromisoformat(at_iso).astimezone(TZ_MSK_FIXED)
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
    parse_mode: Optional[str] = None,
) -> Optional[int]:
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
    }
    if parse_mode:
        data["parse_mode"] = parse_mode
    with open(photo_path, "rb") as photo_file:
        response = requests.post(
            url,
            data=data,
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
        "🎨 <b>Тема недели</b>\n\n"
        f"{color_obj.name_ru} · {color_obj.hex}\n"
        f"<b>Фокус:</b> {build_color_metaphor_line(color_obj)}\n"
        f"<b>Редкость:</b> {rarity_label}"
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
    prune_summary = prune_cache()
    log.info("cache_prune summary=%s", prune_summary)
    day = current_day_key()
    day_summary = get_day_summary(day)
    today_payload = day_summary.get("snapshot") if isinstance(day_summary.get("snapshot"), dict) else {}
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
        parse_mode="HTML",
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
        parse_mode="HTML",
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




def handle_weekly_pattern_callback(tg_token: str, chat_id: str, callback_query: Dict[str, Any]) -> None:
    callback_id = callback_query.get("id")
    callback_data = callback_query.get("data", "")
    week_id = callback_data.split(":", 1)[1] if ":" in callback_data else iso_week_id()
    state = load_weekly_state().get(week_id, {})
    derived = {
        "hero_status": str(state.get("hero_status", "Неделя предварительная")),
        "strongest_period": str(state.get("strongest_period", "утро")),
        "tense_days": int(state.get("tense_days", 0)),
        "partial_days": int(state.get("partial_days", 0)),
    }
    telegram_send(tg_token, chat_id, build_pattern_response(derived))
    if callback_id:
        telegram_answer_callback(tg_token, callback_id)


def handle_weekly_improve_callback(tg_token: str, chat_id: str, callback_query: Dict[str, Any]) -> None:
    callback_id = callback_query.get("id")
    callback_data = callback_query.get("data", "")
    week_id = callback_data.split(":", 1)[1] if ":" in callback_data else iso_week_id()
    state = load_weekly_state().get(week_id, {})
    derived = {
        "partial_days": int(state.get("partial_days", 0)),
    }
    quest = str(state.get("quest", "Сделай 1 спокойный день со стабильным ритмом."))
    telegram_send(tg_token, chat_id, build_improvement_response(derived, quest))
    if callback_id:
        telegram_answer_callback(tg_token, callback_id)
def build_help_message() -> str:
    return "Команды:\n/today\n/color\n/week\n/stats\n/refresh\n/backfill 90\n/debug_sync\n/debug_sent\n/help"


def _parse_backfill_days(text: str) -> Optional[int]:
    parts = text.strip().lower().split()
    if len(parts) != 2 or parts[0] != "/backfill":
        return None
    try:
        days = int(parts[1])
    except ValueError:
        return None
    if days <= 0:
        return None
    return min(days, 90)


def fetch_last_days(days: int = 30) -> Dict[str, Dict[str, Any]]:
    garmin_email = env("GARMIN_EMAIL")
    garmin_password = env("GARMIN_PASSWORD")
    api = Garmin(garmin_email, garmin_password)
    auth_state = get_garmin_auth_state()
    tokenstore = auth_state.get("tokenstore") if isinstance(auth_state, dict) else None
    try:
        if isinstance(tokenstore, str) and tokenstore.strip():
            api.login(tokenstore=tokenstore)
        else:
            api.login()
    except Exception:
        api.login()
    try:
        serialized = api.garth.dumps() if hasattr(api, "garth") else ""
        if serialized:
            upsert_garmin_auth_state({"tokenstore": serialized})
    except Exception:
        log.warning("garmin_tokenstore_persist_failed", exc_info=True)

    now_msk = _now_msk()
    out: Dict[str, Dict[str, Any]] = {}
    calls = {
        "body_battery": "get_body_battery",
        "stress": "get_stress_data",
        "sleep": "get_sleep_data",
        "rhr": "get_rhr_day",
        "steps": "get_steps_data",
        "heart_rate": "get_heart_rates",
        "daily_activity": "get_user_summary",
        "intensity_minutes": "get_intensity_minutes_data",
        "calories": "get_calories_data",
        "floors": "get_floors",
        "respiration": "get_respiration_data",
        "pulse_ox": "get_pulse_ox_data",
        "hrv": "get_hrv_data",
        "hrv_status": "get_hrv_status_data",
        "activity_summary": "get_activities_by_date",
    }
    for delta in range(days):
        day = (now_msk.date() - dt.timedelta(days=delta)).isoformat()
        payload: Dict[str, Any] = {
            "source": "garmin",
            "date": day,
            "fetched_at_utc": utc_now_iso(),
            "last_sync_time": utc_now_iso(),
            "errors": [],
        }
        for key, method_name in calls.items():
            method = getattr(api, method_name, None)
            if not callable(method):
                continue
            try:
                payload[key] = method(day)
            except Exception as e:
                payload["errors"].append({"metric": key, "error": str(e)})
        out[day] = payload
    return out


def fetch_range(days: int) -> Dict[str, Dict[str, Any]]:
    safe_days = max(1, min(int(days), 90))
    return fetch_last_days(safe_days)


def run_backfill(days: int = 30) -> int:
    day_payloads = fetch_range(days)
    stored = 0
    for day_key in sorted(day_payloads.keys()):
        upsert_day_snapshot(day_key, day_payloads[day_key])
        stored += 1
    log.info("backfill stored days=%s requested=%s", stored, days)
    return stored


def ensure_history_bootstrap(target_days: int = 90, chat_id: Optional[str] = None) -> Dict[str, Any]:
    scope_chat_id = (chat_id or os.getenv("DEFAULT_CHAT_ID", "").strip() or "default")
    safe_target = max(1, min(int(target_days), 90))
    history = load_cache()
    available = len(history_list(history))

    if available >= safe_target:
        payload = {
            "last_checked_ts": utc_now_iso(),
            "target_days": safe_target,
            "history_days_seen": available,
            "status": "ready",
            "backfill_triggered": False,
        }
        upsert_bootstrap_state(payload, chat_id=scope_chat_id)
        return payload

    stored = run_backfill(safe_target)
    history_after = load_cache()
    available_after = len(history_list(history_after))
    payload = {
        "last_checked_ts": utc_now_iso(),
        "target_days": safe_target,
        "history_days_seen": available_after,
        "history_days_seen_before": available,
        "status": "backfilled",
        "backfill_triggered": True,
        "backfill_stored_days": stored,
    }
    upsert_bootstrap_state(payload, chat_id=scope_chat_id)
    return payload


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
    telegram_send(tg_token, chat_id, message, parse_mode="HTML")


def handle_week_command(tg_token: str, chat_id: str) -> None:
    history = load_cache()
    now_msk = _now_msk()
    send_weekly_report(tg_token, chat_id, history, now_msk)


def _collect_updated_blocks(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    blocks: List[str] = []
    keys = [
        "sleep",
        "body_battery",
        "stress",
        "steps",
        "heart_rate",
        "rhr",
        "daily_activity",
        "respiration",
        "pulse_ox",
        "hrv",
        "intensity_minutes",
        "calories",
        "floors",
        "activity_summary",
    ]

    before_flags = before.get("missing_flags") if isinstance(before.get("missing_flags"), dict) else {}
    after_flags = after.get("missing_flags") if isinstance(after.get("missing_flags"), dict) else {}

    for key in keys:
        before_value = before.get(key)
        after_value = after.get(key)
        became_available = before_value in (None, {}, []) and after_value not in (None, {}, [])
        improved_missing_flag = before_flags.get(key) is True and after_flags.get(key) is False
        changed_value = before_value != after_value
        if became_available or improved_missing_flag or changed_value:
            blocks.append(key)
    return blocks


def refresh_available_data() -> Dict[str, Any]:
    run_id = _new_run_id("refresh")
    day_key = current_day_key()
    source_fetch_ts = utc_now_iso()
    before = get_day_snapshot(day_key)

    data = fetch_garmin_minimal(env("GARMIN_EMAIL"), env("GARMIN_PASSWORD"))
    data["date"] = day_key
    after = upsert_day_snapshot(day_key, data)
    diff = build_snapshot_merge_diff(before, after)

    updated_blocks = diff["updated_blocks"]
    missing_flags = after.get("missing_flags") if isinstance(after.get("missing_flags"), dict) else {}
    missing_now = [k for k, v in missing_flags.items() if v]

    trace = {
        "run_id": run_id,
        "stage": "refresh",
        "source_fetch_ts": source_fetch_ts,
        "cache_write_ts": utc_now_iso(),
        "snapshot_date_key": day_key,
        "last_sync_time": _garmin_last_sync(data),
        "updated_blocks": updated_blocks,
        "old_completeness": diff["old_completeness"],
        "new_completeness": diff["new_completeness"],
        "old_confidence": diff["old_confidence"],
        "new_confidence": diff["new_confidence"],
        "had_real_updates": diff["had_real_updates"],
        "runtime_cache_source": "local",
        "runtime_cache_available": True,
        "missing_now": missing_now,
        "gist_upload_ts": "",
        "gist_upload_status": "not_attempted_in_refresh_command",
    }
    log_sync_trace(run_id, trace)

    return {
        "run_id": run_id,
        "before": before,
        "after": after,
        "updated_blocks": updated_blocks,
        "has_updates": bool(updated_blocks),
        "old_completeness": diff["old_completeness"],
        "new_completeness": diff["new_completeness"],
        "old_confidence": diff["old_confidence"],
        "new_confidence": diff["new_confidence"],
        "last_sync_time": _garmin_last_sync(data),
        "had_real_updates": diff["had_real_updates"],
    }


def build_refresh_result_message(result: Dict[str, Any]) -> str:
    updated = result.get("updated_blocks", [])
    after = result.get("after", {})
    completeness = result.get("new_completeness", after.get("data_completeness"))
    missing_flags = after.get("missing_flags") if isinstance(after.get("missing_flags"), dict) else {}

    human_map = {
        "sleep": "сон",
        "body_battery": "Body Battery",
        "stress": "стресс",
        "steps": "шаги",
        "heart_rate": "пульс",
        "rhr": "RHR",
        "daily_activity": "дневная активность",
        "respiration": "дыхание",
        "pulse_ox": "Pulse Ox",
        "hrv": "HRV",
        "intensity_minutes": "интенсивные минуты",
        "calories": "калории",
        "floors": "этажи",
        "activity_summary": "сводка активности",
    }

    if not updated:
        missing_now = [human_map.get(k, k) for k, is_missing in missing_flags.items() if is_missing]
        if missing_now:
            return (
                "Новых блоков пока нет: Garmin Connect ещё не отдал свежие данные. "
                "Сейчас всё ещё не хватает: "
                + ", ".join(missing_now[:4])
                + "."
            )
        return "Данные уже актуальны: после последней синхронизации новых изменений не появилось."

    labels = [human_map.get(key, key) for key in updated[:6]]
    missing_now = [human_map.get(k, k) for k, is_missing in missing_flags.items() if is_missing]
    if missing_now:
        return (
            "Данные обновились частично: пришли "
            + ", ".join(labels)
            + ", но ещё ждём "
            + ", ".join(missing_now[:4])
            + "."
        )

    if isinstance(completeness, (int, float)) and float(completeness) < 0.95:
        return "Обновил данные: " + ", ".join(labels) + ". Картина почти полная, жду финальные сигналы Garmin."

    return "Обновил данные: " + ", ".join(labels) + ". Картина дня полная, итог пересчитан."


def build_debug_sync_message() -> str:
    cache_data, cache_meta = load_cache_with_meta()
    day_key = current_day_key()
    day_payload = cache_data.get(day_key) if isinstance(cache_data, dict) else {}
    missing_flags = day_payload.get("missing_flags") if isinstance(day_payload, dict) and isinstance(day_payload.get("missing_flags"), dict) else {}
    missing_blocks = [k for k, v in missing_flags.items() if v]
    diagnostics = day_payload.get("sync_diagnostics") if isinstance(day_payload, dict) and isinstance(day_payload.get("sync_diagnostics"), dict) else {}
    metric_diagnostics = diagnostics.get("metrics") if isinstance(diagnostics.get("metrics"), dict) else {}
    trace = get_latest_sync_trace()

    gist_status = "-"
    if cache_meta.get("source") == "local_fresher_than_gist":
        gist_status = "local snapshot новее gist; после рестарта в другом runtime возможен откат к gist"
    elif cache_meta.get("source") == "local_fallback":
        gist_status = f"gist недоступен ({cache_meta.get('error', 'unknown')}); runtime держится на local"
    elif cache_meta.get("source") == "gist":
        gist_status = "gist используется как primary source"

    lines = [
        "Debug sync:",
        f"• cache source: {cache_meta.get('source', 'unknown')}",
        f"• cache available: {str(bool(cache_meta.get('available', False))).lower()}",
        f"• cache error: {cache_meta.get('error', '') or '-'}",
        f"• cache fallback reason: {cache_meta.get('fallback_reason', '-')}",
        f"• source of truth note: {gist_status}",
        f"• date key: {day_key}",
    ]
    if isinstance(day_payload, dict) and day_payload:
        lines.extend([
            f"• last sync time: {day_payload.get('last_sync_time', '-')}",
            f"• completeness: {day_payload.get('data_completeness', '-')}",
            f"• confidence: {day_payload.get('confidence', '-')}",
        ])
    if trace:
        lines.extend([
            f"• latest run id: {trace.get('run_id', '-')}",
            f"• latest stage: {trace.get('stage', '-')}",
            f"• source fetch ts: {trace.get('source_fetch_ts', '-')}",
            f"• cache write ts: {trace.get('cache_write_ts', '-')}",
            f"• gist upload ts: {trace.get('gist_upload_ts', '-')}",
            f"• gist upload status: {trace.get('gist_upload_status', '-')}",
            f"• runtime cache source: {trace.get('runtime_cache_source', '-')}",
            f"• updated blocks: {', '.join(trace.get('updated_blocks', [])[:6]) or '-'}",
            f"• completeness: {trace.get('old_completeness', '-')} -> {trace.get('new_completeness', '-')}",
            f"• confidence: {trace.get('old_confidence', '-')} -> {trace.get('new_confidence', '-')}",
            f"• had real updates: {str(bool(trace.get('had_real_updates', False))).lower()}",
        ])
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if chat_id:
        sent_today = get_today_sent_registry(chat_id=chat_id, send_date=day_key)
        lines.append("• sent registry entries today: " + str(len(sent_today)))
        for slot in ("morning", "midday", "evening"):
            for message_type in ("color", "verdict", "weekly"):
                key = f"{day_key}|{chat_id}|{slot}|{message_type}"
                lines.append(f"  - {slot}/{message_type}: {'yes' if key in sent_today else 'no'}")

    if missing_blocks:
        lines.append("• still missing: " + ", ".join(missing_blocks[:6]))
        for metric in missing_blocks:
            diag = metric_diagnostics.get(metric) if isinstance(metric_diagnostics, dict) else None
            if not isinstance(diag, dict):
                continue
            lines.append(
                "  - "
                + metric
                + ": raw="
                + ("yes" if diag.get("raw_present") else "no")
                + ", normalized="
                + ("yes" if diag.get("normalized_present") else "no")
                + f", expected_day={diag.get('expected_date_key', '-')}, raw_dates={','.join(diag.get('raw_dates', [])[:3]) or '-'}, reason={diag.get('reason', '-')}" 
            )
    return "\n".join(lines)


def handle_refresh_command(tg_token: str, chat_id: str) -> None:
    try:
        log.info("manual_refresh_started chat_id=%s now_msk=%s", chat_id, _now_msk().isoformat())
        result = refresh_available_data()
        message = build_refresh_result_message(result)
        today = current_day_key()
        log.info("manual_refresh_result chat_id=%s run_id=%s had_updates=%s updated_blocks=%s", chat_id, result.get("run_id", ""), bool(result.get("has_updates", False)), result.get("updated_blocks", []))
        log_refresh_attempt(
            chat_id=chat_id,
            refresh_date=today,
            had_updates=bool(result.get("has_updates", False)),
            updated_blocks=result.get("updated_blocks", []),
            message=message,
            refresh_ts=utc_now_iso(),
        )
        telegram_send(tg_token, chat_id, message)
    except Exception:
        log.exception("refresh command failed")
        err_text = str(sys.exc_info()[1] or "")
        if "Missing env var" in err_text or "credentials" in err_text.lower():
            telegram_send(
                tg_token,
                chat_id,
                "Не удалось обновить Garmin: проверь GARMIN_EMAIL / GARMIN_PASSWORD в окружении. Кэш не изменял.",
            )
            return
        telegram_send(
            tg_token,
            chat_id,
            "Не удалось обновить данные прямо сейчас. Можно повторить /refresh позже.",
        )


def _metric_name_list(metric_keys: List[str]) -> str:
    return ", ".join(METRIC_LABELS.get(key, key) for key in metric_keys)


def _sanitize_user_text(text: str) -> str:
    clean = text or ""
    clean = clean.replace("```", "").replace("`", "")
    clean = clean.replace("**", "").replace("__", "").replace("~~", "")
    clean = re.sub(r"^#{1,6}\s*", "", clean, flags=re.MULTILINE)
    clean = re.sub(r"^>\s?", "", clean, flags=re.MULTILINE)
    for bad in ("рад, что ты спросил", "рад что ты спросил", "рада, что ты спросил", "рада что ты спросил"):
        clean = clean.replace(bad, "")
    return clean.strip()


def _format_metrics_availability(context: Dict[str, Any]) -> str:
    return build_metrics_message(context)


def _safe_value(snapshot: Dict[str, Any], path: List[str]) -> Optional[Any]:
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _format_detailed_analysis(context: Dict[str, Any]) -> str:
    return build_day_detail_message(context, str(context.get("target_day", current_day_key())))


def _format_history_answer(context: Dict[str, Any]) -> str:
    return build_history_message(context)


RU_MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}


def _format_ru_date(day: dt.date) -> str:
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    return f"{day.day} {months[day.month - 1]}"


def _resolve_relative_day(q: str, now_day: dt.date) -> Optional[dt.date]:
    if "позавчера" in q:
        return now_day - dt.timedelta(days=2)
    if "вчера" in q:
        return now_day - dt.timedelta(days=1)
    if "сегодня" in q:
        return now_day
    return None


def _parse_explicit_ru_date(q: str, now_day: dt.date) -> Optional[dt.date]:
    m = re.search(r"\b(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?\b", q)
    if not m:
        return None
    day = int(m.group(1))
    month_token = m.group(2)
    year_token = m.group(3)
    month = None
    for stem, value in RU_MONTHS.items():
        if month_token.startswith(stem):
            month = value
            break
    if month is None:
        return None
    year = int(year_token) if year_token else now_day.year
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _resolve_target_date(query: str, now_day: dt.date) -> Optional[dt.date]:
    q = query.strip().lower()
    explicit = _parse_explicit_ru_date(q, now_day)
    if explicit:
        return explicit
    return _resolve_relative_day(q, now_day)


def _is_current_date_only_query(q: str) -> bool:
    if "какое число" in q or "какая дата" in q:
        return True
    if "сегодня" in q and "число" in q:
        return True
    return False


def _is_date_data_query(q: str) -> bool:
    if any(token in q for token in ("данные", "стат", "показ")):
        return True
    return bool(_resolve_target_date(q, _now_msk().date()))


def _format_day_data_answer(target_date: dt.date, context: Dict[str, Any]) -> str:
    return build_day_verdict_message(context, target_date.isoformat())


def _snapshot_value(snapshot: Dict[str, Any], *path: str) -> Optional[Any]:
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _render_metric_answer(intent: str, context: Dict[str, Any]) -> Optional[str]:
    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
    day_key = str(context.get("day_key", current_day_key()))

    respiration = _snapshot_value(snapshot, "respiration", "avgWakingRespirationValue") or _snapshot_value(snapshot, "respiration", "latestRespirationValue")
    spo2 = _snapshot_value(snapshot, "pulse_ox", "avgSpo2") or _snapshot_value(snapshot, "pulse_ox", "mostRecentValue")
    stress = _snapshot_value(snapshot, "stress", "avgStressLevel") or _snapshot_value(snapshot, "stress", "overallStressLevel")
    sleep_s = _snapshot_value(snapshot, "sleep", "sleepTimeSeconds") or _snapshot_value(snapshot, "sleep", "totalSleepSeconds")
    steps = _snapshot_value(snapshot, "steps", "totalSteps")
    pulse = _snapshot_value(snapshot, "rhr", "restingHeartRate")
    hrv_status = _snapshot_value(snapshot, "hrv_status", "status")
    bb_now = _snapshot_value(snapshot, "body_battery", "mostRecentValue")
    bb_start = _snapshot_value(snapshot, "body_battery", "chargedValue")

    if intent in ("respiration", "oxygen"):
        parts = ["🌬 <b>Дыхание и кислород</b>"]
        if isinstance(respiration, (int, float)):
            parts.append(f"• Дыхание: <b>{float(respiration):.1f}</b>/мин")
        if isinstance(spo2, (int, float)):
            parts.append(f"• SpO₂: <b>{int(spo2)}%</b>")
        if len(parts) == 1:
            return "🌬 <b>Дыхание и кислород</b><br>Данных за день пока недостаточно."
        extra = []
        if isinstance(stress, (int, float)):
            extra.append(f"стресс {int(stress)}")
        if isinstance(bb_now, (int, float)):
            extra.append(f"Body Battery {int(bb_now)}")
        if extra:
            parts.append("Ещё вижу: " + ", ".join(extra) + ".")
        return "<br>".join(parts)

    if intent == "stress_metric":
        if not isinstance(stress, (int, float)):
            return "😵 <b>Стресс</b><br>Пока нет достаточно данных по стрессу."
        peak = _snapshot_value(snapshot, "stress", "maxStressLevel")
        tail = f", пики до {int(peak)}" if isinstance(peak, (int, float)) else ""
        return f"😵 <b>Стресс</b><br>• Средний: <b>{int(stress)}</b>{tail}<br>• Контекст: день {day_key}."

    if intent == "sleep_metric":
        if not isinstance(sleep_s, (int, float)):
            return "😴 <b>Сон</b><br>Нет полного блока сна за этот день."
        h = int(sleep_s // 3600)
        m = int((sleep_s % 3600) // 60)
        return f"😴 <b>Сон</b><br>• Длительность: <b>{h}ч {m:02d}м</b><br>• Влияние: это задаёт потолок ресурса на день."

    if intent == "steps":
        if not isinstance(steps, (int, float)):
            return "🚶 <b>Шаги</b><br>Шаги за день ещё не подтянулись."
        return f"🚶 <b>Шаги</b><br>• Сейчас: <b>{int(steps)}</b><br>• Смысл: шаги поддерживают тонус, но не заменяют восстановление."

    if intent == "activity":
        active = _snapshot_value(snapshot, "intensity_minutes", "moderateIntensityMinutes")
        if not isinstance(active, (int, float)):
            return "🏃 <b>Активность</b><br>Данных по интенсивности пока мало."
        return f"🏃 <b>Активность</b><br>• Интенсивные минуты: <b>{int(active)}</b><br>• Смысл: держать ровный объём без рывков."

    if intent == "pulse":
        if not isinstance(pulse, (int, float)):
            return "🫀 <b>Пульс</b><br>Пульс покоя за день пока не зафиксирован."
        return f"🫀 <b>Пульс покоя</b><br>• Значение: <b>{int(pulse)}</b><br>• Контекст: используем как фон восстановления."

    if intent == "hrv_metric":
        if not hrv_status:
            return "💓 <b>HRV</b><br>Статус HRV за день отсутствует."
        return f"💓 <b>HRV</b><br>• Статус: <b>{hrv_status}</b><br>• Контекст: показывает устойчивость к нагрузке."

    if intent == "since_morning":
        if not (isinstance(bb_start, (int, float)) and isinstance(bb_now, (int, float))):
            return "↕️ <b>Что изменилось с утра</b><br>Недостаточно данных для оценки динамики."
        delta = int(bb_now - bb_start)
        trend = "просадка" if delta < 0 else "рост"
        return f"↕️ <b>Что изменилось с утра</b><br>• Body Battery: <b>{int(bb_start)} → {int(bb_now)}</b> ({delta:+d})<br>• Коротко: {trend} ресурса по ходу дня."

    return None


def _build_what15_message(slot: str, snapshot: Optional[Dict[str, Any]]) -> str:
    bb = _snapshot_value(snapshot or {}, "body_battery", "mostRecentValue")
    low = isinstance(bb, (int, float)) and bb < 35
    if slot == "morning":
        return "🎯 <b>Что делать за 15 минут</b><br>1) 2 мин — вода и тишина.<br>2) 10 мин — один приоритет без переключений.<br>3) 3 мин — короткая пауза и план следующего шага."
    if slot == "midday":
        return "🎯 <b>Что делать за 15 минут</b><br>1) 5 мин — пройтись в спокойном темпе.<br>2) 7 мин — закрыть один мелкий хвост.<br>3) 3 мин — дыхание 4-6 и возврат к главной задаче."
    if low:
        return "🎯 <b>Что делать за 15 минут</b><br>1) 4 мин — тишина без экрана.<br>2) 6 мин — мягкая ходьба.<br>3) 5 мин — душ/вода и завершение новых задач."
    return "🎯 <b>Что делать за 15 минут</b><br>1) 3 мин — приглушить свет и уведомления.<br>2) 7 мин — спокойный ритуал закрытия дня.<br>3) 5 мин — подготовка ко сну без экрана."


def _route_structured_reply(query: str, context: Dict[str, Any], history_cache: Dict[str, Any], chat_id: str = "") -> Optional[str]:
    q = query.strip().lower()
    if "какие данные" in q and ("сколько" in q or "за сколько" in q):
        return build_metrics_message(context)
    intent = resolve_intent(q)
    speech_mode = str(get_user_prefs(chat_id).get("speech_mode", "short")) if chat_id else "short"
    if intent == "metrics":
        return _format_metrics_availability(context)
    if intent == "detail":
        return _format_detailed_analysis(context)
    if intent == "history":
        return _format_history_answer(context)
    if intent == "what_data":
        return build_metrics_message(context)
    metric_reply = _render_metric_answer(intent, context)
    if metric_reply:
        return metric_reply
    if intent == "day_verdict":
        return build_push_message(
            slot="day",
            snapshot=context.get("snapshot"),
            day_key=str(context.get("day_key", current_day_key())),
            partial=context.get("day_status") != "ready",
            mode=speech_mode,
        )
    if intent == "current_state":
        return build_push_message("midday", context.get("snapshot"), str(context.get("day_key", current_day_key())), partial=context.get("day_status") != "ready")
    if intent == "weekly":
        history = load_cache()
        payload = build_weekly_payload(history, _now_msk(), chat_id="ad-hoc")
        return payload["caption"]
    if intent == "compare_days":
        days = list(context.get("available_days", []))
        if len(days) < 2:
            return _format_history_answer(context)
        d1, d2 = days[-2], days[-1]
        s1 = build_day_context(day_key=d1, cache_data=history_cache).get("snapshot")
        s2 = build_day_context(day_key=d2, cache_data=history_cache).get("snapshot")
        return render_compare_days(d1, d2, s1, s2)
    if _is_current_date_only_query(q):
        today = _now_msk().date()
        return f"Сегодня {_format_ru_date(today)} ({today.isoformat()})."
    if _is_date_data_query(q):
        target_date = _resolve_target_date(query, _now_msk().date())
        if target_date is None:
            return None
        day_context = build_day_context(day_key=target_date.isoformat(), cache_data=history_cache)
        return _format_day_data_answer(target_date, day_context)
    return None


def build_chat_prompt(cache: Dict[str, Any], query: str) -> str:
    """Builds the user prompt for conversational chat fallback."""
    return (
        "Language: Russian by default unless user requested otherwise.\n"
        "Format: max 5 short blocks, no markdown syntax, no greetings unless needed.\n"
        "Answer concrete question first, then optional block 'Ещё вижу'.\n"
        "Avoid gendered forms and medical/motivational tone.\n"
        f"User query: {query}\n"
        "Data history JSON:\n"
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


@app.get("/miniapp", response_class=HTMLResponse)
def miniapp_page():
    with open("miniapp/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/miniapp/api/dashboard")
def miniapp_dashboard():
    now = _now_msk()
    history = load_cache()
    day_key = now.date().isoformat()
    context = build_day_context(day_key=day_key, cache_data=history)
    weekly_payload = build_weekly_payload(history, now, chat_id=os.getenv("TELEGRAM_CHAT_ID", "miniapp"))
    return JSONResponse(
        {
            "status": build_verdict_label(context.get("snapshot"), day_key, "day"),
            "sync": (context.get("snapshot") or {}).get("last_sync_time", "нет"),
            "timeline": ["утро", "день", "вечер"],
            "color": get_or_create_weekly_color_state(),
            "week_map": weekly_payload.get("map_lines", []),
        }
    )


@app.post("/miniapp/api/prefs")
async def miniapp_prefs(request: Request):
    payload = await request.json()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "miniapp")
    allowed = {"speech_mode", "visual_bonus", "sarcasm", "short_only"}
    data = {k: v for k, v in payload.items() if k in allowed}
    upsert_user_prefs(chat_id, data)
    return JSONResponse({"ok": True, "saved": data})


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
            if _callback_duplicate_guard(tg_token, callback_chat_id, callback_query):
                return Response(status_code=200)
            if callback_data.startswith("color_story"):
                handle_color_story_callback(tg_token, callback_chat_id, callback_query)
            elif callback_data.startswith("color_vote"):
                handle_color_vote_callback(tg_token, callback_chat_id, callback_query)
            elif callback_data.startswith("today_vote"):
                handle_today_vote_callback(tg_token, callback_chat_id, callback_query)
            elif callback_data.startswith("today_story"):
                handle_today_story_callback(tg_token, callback_chat_id, callback_query)
            elif callback_data.startswith("weekly_pattern"):
                handle_weekly_pattern_callback(tg_token, callback_chat_id, callback_query)
            elif callback_data.startswith("weekly_improve"):
                handle_weekly_improve_callback(tg_token, callback_chat_id, callback_query)
            elif callback_data == "noop":
                callback_id = callback_query.get("id")
                if callback_id:
                    telegram_answer_callback(tg_token, callback_id)
            elif callback_data.startswith("why:"):
                parts = callback_data.split(":")
                day_key = parts[2].strip() if len(parts) >= 3 else current_day_key()
                day_summary = get_day_summary(day_key)
                telegram_send(tg_token, callback_chat_id, _sanitize_user_text(build_why_message(day_summary.get("snapshot"))), parse_mode="HTML")
            elif callback_data.startswith("facts:"):
                parts = callback_data.split(":")
                if len(parts) >= 3:
                    slot = parts[1].strip().lower()
                    day_key = parts[2].strip()
                    day_summary = get_day_summary(day_key)
                    snapshot = day_summary.get("snapshot") if isinstance(day_summary.get("snapshot"), dict) else {}
                    partial = day_summary.get("completeness_state") != "FULL"
                    message = build_push_message(slot=slot, snapshot=snapshot, day_key=day_key, partial=partial, mode="facts")
                    telegram_send(tg_token, callback_chat_id, _sanitize_user_text(message), parse_mode="HTML")
            elif callback_data.startswith("roast:"):
                parts = callback_data.split(":")
                if len(parts) >= 3:
                    slot = parts[1].strip().lower()
                    day_key = parts[2].strip()
                    day_summary = get_day_summary(day_key)
                    snapshot = day_summary.get("snapshot") if isinstance(day_summary.get("snapshot"), dict) else {}
                    partial = day_summary.get("completeness_state") != "FULL"
                    message = build_push_message(slot=slot, snapshot=snapshot, day_key=day_key, partial=partial, mode="roast")
                    telegram_send(tg_token, callback_chat_id, _sanitize_user_text(message), parse_mode="HTML")
            elif callback_data.startswith("what15:"):
                parts = callback_data.split(":")
                slot = parts[1].strip().lower() if len(parts) >= 2 else "day"
                day_key = parts[2].strip() if len(parts) >= 3 else current_day_key()
                day_summary = get_day_summary(day_key)
                snapshot = day_summary.get("snapshot") if isinstance(day_summary.get("snapshot"), dict) else {}
                telegram_send(tg_token, callback_chat_id, _sanitize_user_text(_build_what15_message(slot, snapshot)), parse_mode="HTML")
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
            telegram_send(tg_token, message_chat_id, build_help_message(), parse_mode="HTML")
            return Response(status_code=200)
        if text.lower() == "/week":
            handle_week_command(tg_token, message_chat_id)
            return Response(status_code=200)
        if text.lower() == "/stats":
            handle_stats_command(tg_token, message_chat_id)
            return Response(status_code=200)
        if text.lower() == "/refresh":
            handle_refresh_command(tg_token, message_chat_id)
            return Response(status_code=200)
        if text.lower() == "/debug_sync":
            telegram_send(tg_token, message_chat_id, build_debug_sync_message())
            return Response(status_code=200)
        if text.lower() == "/debug_sent":
            telegram_send(tg_token, message_chat_id, _build_debug_sent_message(message_chat_id, current_day_key()))
            return Response(status_code=200)
        backfill_days = _parse_backfill_days(text)
        if backfill_days is not None:
            if not _is_admin(message_chat_id):
                telegram_send(tg_token, message_chat_id, "Команда доступна только владельцу/админу.")
                return Response(status_code=200)
            stored_days = run_backfill(backfill_days)
            telegram_send(tg_token, message_chat_id, f"Backfill готов: сохранено {stored_days} дней.")
            return Response(status_code=200)

        # Load unified day context from cache and use deterministic handlers first
        history_cache = load_cache()
        context = build_day_context(cache_data=history_cache)
        response_msg = _route_structured_reply(text, context, history_cache, chat_id=message_chat_id)
        if response_msg is None:
            response_msg = generate_chat_message(
                env("GEMINI_API_KEY"), env("GEMINI_MODEL"), history_cache, text
            )

        telegram_send(tg_token, message_chat_id, _sanitize_user_text(response_msg), parse_mode="HTML")

    except Exception:
        log.exception("Webhook processing failed")
        # Silently fail to prevent error loops with Telegram,
        # but log the error for debugging.

    return Response(status_code=200)


# --- CLI ---
def run_serve() -> None:
    """Starts the Uvicorn server."""
    try:
        bootstrap = ensure_history_bootstrap(target_days=90)
        log.info("history bootstrap status=%s seen=%s target=%s", bootstrap.get("status"), bootstrap.get("history_days_seen"), bootstrap.get("target_days"))
    except Exception:
        log.exception("History bootstrap failed")
    try:
        ensure_bot_commands(env("TELEGRAM_BOT_TOKEN"))
        log.info("Telegram commands ensured")
    except Exception:
        log.exception("Failed to ensure Telegram commands at startup")
    log.info("Starting web server")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 main.py [sync|backfill|push|push-self-check|cache-self-check|debug-sync|serve|schedule-debug|schedule-self-check|color-self-check|color-card-self-check|today-card-self-check|today-status-self-check]")
        return

    mode = sys.argv[1].strip().lower()

    if mode == "sync":
        run_sync()
    elif mode == "backfill":
        days = 30
        if len(sys.argv) >= 3:
            try:
                days = max(1, min(90, int(sys.argv[2])))
            except ValueError:
                print("Error: backfill requires integer days")
                return
        stored = run_backfill(days)
        print(f"backfill done: requested={days} stored={stored}")
    elif mode == "push":
        push_kind = "scheduled"
        explicit_slot = None
        dry_run = False
        args = sys.argv[2:]
        idx = 0
        while idx < len(args):
            arg = args[idx]
            normalized = arg.strip().lower()
            if normalized == "--dry-run":
                dry_run = True
                idx += 1
                continue
            if normalized == "--slot" and idx + 1 < len(args):
                slot_value = args[idx + 1].strip().lower()
                if slot_value not in {"morning", "midday", "evening"}:
                    print("Error: --slot must be morning|midday|evening")
                    return
                explicit_slot = slot_value
                idx += 2
                continue
            elif normalized in ["scheduled", "morning", "midday", "evening"]:
                push_kind = normalized
                idx += 1
                continue
            elif normalized:
                print("Error: push mode args must be [scheduled|morning|midday|evening|--slot <slot>|--dry-run]")
                return
            idx += 1
        if push_kind == "scheduled" and explicit_slot:
            push_kind = explicit_slot
        run_push(push_kind, dry_run=dry_run)
    elif mode == "push-self-check":
        run_push_self_check()
    elif mode == "cache-self-check":
        run_cache_self_check()
    elif mode == "debug-sync":
        print(build_debug_sync_message())
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
            f"Error: Unknown mode '{mode}'. Use sync, backfill, push, push-self-check, cache-self-check, debug-sync, serve, schedule-debug, schedule-self-check, color-self-check, "
            "color-card-self-check, today-card-self-check, or today-status-self-check."
        )
        sys.exit(1)

if __name__ == "__main__":
    main()
    
    
