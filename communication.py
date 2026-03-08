import datetime as dt
from typing import Any, Dict, List, Optional, Set, Tuple

KEY_METRICS = ("sleep", "body_battery", "stress", "rhr", "respiration", "pulse_ox", "hrv", "steps")

GENERIC_OPENERS = (
    "привет",
    "рад",
    "рада",
    "спросил",
    "спросила",
    "заглянул",
    "заглянула",
)

INTENT_PATTERNS = {
    "day_verdict": ("как мой день", "вердикт дня", "итог дня", "день как", "как прошел день"),
    "current_state": ("как я сейчас", "как сейчас", "мой статус", "мой режим"),
    "detail": ("детализ", "деталь", "разбор"),
    "metrics": ("какие метрики", "что по метрикам", "что видно по данным"),
    "history": ("за сколько", "сколько дней", "история данных", "диапазон"),
    "compare_days": ("сравни", "сравнить", "сравнение", "vs", "против"),
    "what_data": ("какие данные есть", "что есть по данным", "какие данные доступны"),
    "weekly": ("недел", "итог недели", "weekly"),
    "visual": ("покажи красиво", "сделай карточкой", "дай визуально"),
    "why": ("почему", "из-за чего", "причины"),
    "respiration": ("дыхани",),
    "oxygen": ("кислород", "spo2", "spo₂", "spo2", "спо2", "сатурац"),
    "steps": ("шаг", "ходьб"),
    "activity": ("активн", "нагруз"),
    "stress_metric": ("стресс",),
    "sleep_metric": ("сон",),
    "pulse": ("пульс", "чсс", "heart rate"),
    "hrv_metric": ("hrv", "вср", "вариаб"),
    "since_morning": ("с утра", "что изменилось с утра", "изменилось с утра"),
}

SPEECH_MODES = {"short", "facts", "roast"}

STATE_POOL = {
    "high": ["Собранный старт", "Ровный темп", "Запас есть, без перегазовок"],
    "steady": ["Рабочий ритм", "Стабильный день", "Запас есть, но тихо"],
    "border": ["Ресурс под контролем", "Темп держится на аккуратности", "Без лишнего шума"],
    "low": ["Ресурс просел — нужен тихий режим", "Нужен бережный темп", "День на экономии"],
    "overload": ["Нужна разгрузка", "Режим восстановления", "Сегодня лучше без давления"],
}

SLOT_METRIC_RELEVANCE = {
    "morning": {"sleep": 1.0, "bb_start": 1.0, "hrv_status": 0.9, "rhr": 0.8, "bb_now": 0.6, "respiration": 0.45, "spo2": 0.45},
    "midday": {"bb_now": 1.0, "bb_delta": 0.9, "stress_avg": 0.95, "stress_peak": 0.75, "steps": 0.8, "active_minutes": 0.75, "respiration": 0.4, "sleep": 0.2},
    "evening": {"bb_now": 1.0, "bb_delta": 0.9, "stress_avg": 0.95, "stress_peak": 0.8, "steps": 0.85, "active_minutes": 0.8, "sleep_guard": 0.6, "sleep": 0.25},
    "day": {"bb_now": 0.9, "stress_avg": 0.9, "sleep": 0.8, "steps": 0.7, "hrv_status": 0.7},
}


def _seed(*parts: str) -> int:
    return sum(ord(ch) for ch in "|".join(parts))


def resolve_intent(query: str) -> str:
    q = query.strip().lower()
    for intent, patterns in INTENT_PATTERNS.items():
        if any(p in q for p in patterns):
            return intent
    return "fallback"


def _safe(snapshot: Dict[str, Any], *path: str) -> Optional[Any]:
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _extract_metrics(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    return {
        "bb_now": _safe(snapshot, "body_battery", "mostRecentValue"),
        "bb_start": _safe(snapshot, "body_battery", "chargedValue"),
        "stress_avg": _safe(snapshot, "stress", "avgStressLevel") or _safe(snapshot, "stress", "overallStressLevel"),
        "stress_peak": _safe(snapshot, "stress", "maxStressLevel"),
        "sleep_seconds": _safe(snapshot, "sleep", "sleepTimeSeconds") or _safe(snapshot, "sleep", "totalSleepSeconds"),
        "hrv_status": _safe(snapshot, "hrv_status", "status"),
        "hrv_weekly_avg": _safe(snapshot, "hrv", "weeklyAvg"),
        "steps": _safe(snapshot, "steps", "totalSteps"),
        "rhr": _safe(snapshot, "rhr", "restingHeartRate"),
        "respiration_avg": _safe(snapshot, "respiration", "avgWakingRespirationValue") or _safe(snapshot, "respiration", "latestRespirationValue"),
        "spo2_avg": _safe(snapshot, "pulse_ox", "avgSpo2") or _safe(snapshot, "pulse_ox", "mostRecentValue"),
        "active_minutes": _safe(snapshot, "intensity_minutes", "moderateIntensityMinutes") or _safe(snapshot, "daily_activity", "activeSeconds"),
    }


def _fmt_int(value: Any, min_v: int = 0, max_v: int = 999999) -> Optional[int]:
    if not isinstance(value, (int, float)):
        return None
    iv = int(value)
    if iv < min_v or iv > max_v:
        return None
    return iv


def _fmt_sleep_seconds(value: Any) -> Optional[str]:
    if not isinstance(value, (int, float)):
        return None
    total_min = int(value // 60)
    if total_min <= 0 or total_min > 24 * 60:
        return None
    return f"{total_min // 60}ч {total_min % 60:02d}м"


def _score(metrics: Dict[str, Any]) -> float:
    score = 0.0
    bb_now = _fmt_int(metrics.get("bb_now"), 0, 100)
    stress = _fmt_int(metrics.get("stress_avg"), 0, 100)
    sleep = metrics.get("sleep_seconds")
    if bb_now is not None:
        score += (bb_now - 55.0) / 25.0
    if stress is not None:
        score += (40.0 - stress) / 30.0
    if isinstance(sleep, (int, float)):
        score += ((float(sleep) / 3600.0) - 7.0) / 1.8
    return round(score, 2)


def _state_bucket(score: float) -> str:
    if score >= 1.1:
        return "high"
    if score >= 0.2:
        return "steady"
    if score >= -0.4:
        return "border"
    if score >= -1.2:
        return "low"
    return "overload"


def build_verdict_label(snapshot: Optional[Dict[str, Any]], day_key: str, slot: str = "day") -> str:
    bucket = _state_bucket(_score(_extract_metrics(snapshot)))
    options = STATE_POOL[bucket]
    return options[_seed(day_key, slot, bucket) % len(options)]


def _slot_head(slot: str) -> str:
    return {
        "morning": "Старт дня",
        "midday": "Сверка в середине дня",
        "evening": "Финал дня",
        "day": "Итог дня",
        "current": "Сигнал момента",
    }.get(slot, "Сигнал дня")


def _chip_candidates(snapshot: Optional[Dict[str, Any]], slot: str) -> List[Tuple[str, float, str]]:
    m = _extract_metrics(snapshot)
    relevance = SLOT_METRIC_RELEVANCE.get(slot, SLOT_METRIC_RELEVANCE["day"])
    chips: List[Tuple[str, float, str]] = []

    bb_now = _fmt_int(m.get("bb_now"), 0, 100)
    bb_start = _fmt_int(m.get("bb_start"), 0, 100)
    if bb_now is not None:
        significance = abs(bb_now - 50) / 50.0
        chips.append(("bb_now", relevance.get("bb_now", 0.5) + significance, f"🔋 Body Battery: <b>{bb_now}</b>"))
    if bb_start is not None:
        significance = abs(bb_start - 60) / 60.0
        chips.append(("bb_start", relevance.get("bb_start", 0.4) + significance, f"🌅 Стартовый ресурс: <b>{bb_start}</b>"))
    if bb_now is not None and bb_start is not None:
        delta = bb_now - bb_start
        significance = min(1.0, abs(delta) / 35.0)
        chips.append(("bb_delta", relevance.get("bb_delta", 0.4) + significance, f"↕️ С утра: <b>{bb_start} → {bb_now}</b> ({delta:+d})"))

    stress_avg = _fmt_int(m.get("stress_avg"), 0, 100)
    stress_peak = _fmt_int(m.get("stress_peak"), 0, 100)
    if stress_avg is not None:
        significance = abs(stress_avg - 35) / 45.0
        chips.append(("stress_avg", relevance.get("stress_avg", 0.5) + significance, f"😵 Средний стресс: <b>{stress_avg}</b>"))
    if stress_peak is not None:
        significance = max(0.0, (stress_peak - 65) / 35.0)
        chips.append(("stress_peak", relevance.get("stress_peak", 0.3) + significance, f"📈 Пики стресса: до <b>{stress_peak}</b>"))

    sleep_seconds = m.get("sleep_seconds")
    sleep_fmt = _fmt_sleep_seconds(sleep_seconds)
    if sleep_fmt:
        hours = float(sleep_seconds) / 3600.0
        significance = abs(hours - 7.0) / 3.0
        chips.append(("sleep", relevance.get("sleep", 0.3) + significance, f"😴 Сон: <b>{sleep_fmt}</b>"))

    if isinstance(m.get("hrv_status"), str) and m.get("hrv_status"):
        chips.append(("hrv_status", relevance.get("hrv_status", 0.4) + 0.6, f"💓 HRV: <b>{m['hrv_status']}</b>"))

    rhr = _fmt_int(m.get("rhr"), 30, 130)
    if rhr is not None:
        chips.append(("rhr", relevance.get("rhr", 0.4) + abs(rhr - 56) / 25.0, f"🫀 Пульс покоя: <b>{rhr}</b>"))

    steps = _fmt_int(m.get("steps"), 0, 120000)
    if steps is not None:
        chips.append(("steps", relevance.get("steps", 0.3) + min(1.0, abs(steps - 8000) / 10000.0), f"🚶 Шаги: <b>{steps}</b>"))

    resp = m.get("respiration_avg")
    if isinstance(resp, (int, float)):
        chips.append(("respiration", relevance.get("respiration", 0.3) + min(1.0, abs(float(resp) - 15.0) / 6.0), f"🌬 Дыхание: <b>{float(resp):.1f}</b>/мин"))

    spo2 = m.get("spo2_avg")
    if isinstance(spo2, (int, float)):
        chips.append(("spo2", relevance.get("spo2", 0.3) + max(0.0, (97.0 - float(spo2)) / 3.0), f"🫧 SpO₂: <b>{int(spo2)}%</b>"))

    active = m.get("active_minutes")
    if isinstance(active, (int, float)):
        chips.append(("active_minutes", relevance.get("active_minutes", 0.3) + min(1.0, float(active) / 2400.0), f"🏃 Активность: <b>{int(active // 60) if active > 120 else int(active)} мин</b>"))

    return chips


def _select_slot_chips(snapshot: Optional[Dict[str, Any]], slot: str, max_items: int = 4) -> List[str]:
    candidates = _chip_candidates(snapshot, slot)
    used: Set[str] = set()
    selected: List[str] = []

    slot_order = {
        "morning": ("sleep", "bb_start", "hrv_status", "rhr", "bb_now", "respiration", "spo2"),
        "midday": ("bb_now", "bb_delta", "stress_avg", "stress_peak", "steps", "active_minutes", "respiration"),
        "evening": ("bb_now", "bb_delta", "stress_avg", "steps", "active_minutes", "stress_peak", "sleep_guard"),
    }.get(slot, ())

    # forced ordering by relevance, then significance
    for key in slot_order:
        ranked = sorted((c for c in candidates if c[0] == key), key=lambda x: x[1], reverse=True)
        if ranked and ranked[0][0] not in used:
            selected.append(ranked[0][2])
            used.add(ranked[0][0])
        if len(selected) >= max_items:
            return selected

    for key, _, text in sorted(candidates, key=lambda x: x[1], reverse=True):
        if key in used:
            continue
        # novelty penalty: midday/evening avoid overusing sleep unless strong
        if slot in ("midday", "evening") and key == "sleep" and len(selected) >= 2:
            continue
        selected.append(text)
        used.add(key)
        if len(selected) >= max_items:
            break
    return selected


def _meaning_line(slot: str, score: float) -> str:
    if score >= 0.8:
        base = "Ресурс в рабочем диапазоне: ставка на ровный темп окупится."
    elif score >= -0.1:
        base = "День держится на ритме: важнее стабильность, чем ускорения."
    else:
        base = "Ресурс просел: выигрыш сейчас в тишине и простом режиме."
    irony = " Без героизма — сегодня это и есть взрослая стратегия." if slot in ("midday", "evening") and int(score * 10) % 3 == 0 else ""
    return base + irony


def build_action_block(slot: str, score: float) -> str:
    if slot == "morning":
        do = "один фокус-блок 60–90 минут до первого хаоса"
        avoid = "не стартовать день в режиме спринта"
    elif slot == "midday":
        do = "пауза 7–10 минут без экрана и затем один приоритет"
        avoid = "не разгоняться кофеином и задачами одновременно"
    else:
        do = "приглушить свет, закрыть новые задачи, выйти в тихий режим"
        avoid = "не делать вечерних добивок"
    if score < -0.8:
        do = "15 минут тихой ходьбы + вода + упрощение плана до базового"
    return f"🎯 <b>Действие:</b> {do}.\n🚫 <b>Лимит:</b> {avoid}."


def _no_data_message(slot: str) -> str:
    return (
        f"🟡 <b>{_slot_head(slot)}</b>\n\n"
        "<b>Вердикт:</b> данных пока мало, вывод предварительный.\n"
        "• Подгружена только часть метрик Garmin\n"
        "• Точный контекст дня пока не собран\n"
        "• Решения лучше принимать в щадящем режиме\n\n"
        "🧠 <b>Смысл:</b> пока держать ритм, не форсировать.\n"
        "🎯 <b>Действие:</b> один спокойный блок и пауза 5–7 минут.\n"
        "🚫 <b>Лимит:</b> не разгонять нагрузку до следующей синхронизации."
    )


def build_data_chips(snapshot: Optional[Dict[str, Any]], max_items: int = 4, slot: str = "day") -> List[str]:
    return _select_slot_chips(snapshot, slot=slot, max_items=max_items)


def render_facts_rich(day_summary: Optional[Dict[str, Any]], slot: str = "day") -> str:
    chips = build_data_chips(day_summary, max_items=5, slot=slot)
    top = "\n".join(f"• {c}" for c in chips) if chips else "• Ключевые метрики ещё догружаются"
    extras = ["🌬 Дыхание/SpO₂/этажи/интенсивность — добавляем по мере прихода данных"]
    return (
        "📊 <b>По фактам (top-5)</b>\n"
        + top
        + "\n\n"
        + "🧾 <b>Остальное</b>\n"
        + "\n".join(f"• {line}" for line in extras)
        + "\n\n"
        + "<b>Вывод:</b> картина дня читается, лучше держать ровный режим."
    )


def render_roast(day_summary: Optional[Dict[str, Any]], history_optional: Optional[Dict[str, Any]] = None, slot: str = "day") -> str:
    facts = build_data_chips(day_summary, max_items=2, slot=slot)
    facts_block = "\n".join(f"• {line}" for line in facts) if facts else "• Метрик мало, но перегазовки всё равно не нужны"
    history_hint = ""
    if isinstance(history_optional, dict) and isinstance(history_optional.get("available_days_count"), int):
        history_hint = f" (история: {history_optional.get('available_days_count')} дн.)"
    return (
        "🥔 <b>Пожарь</b>\n"
        "Без драмы: ритм понятен, запас не бесконечный.\n\n"
        "<b>Факты:</b>\n"
        + facts_block
        + "\n\n"
        + f"<b>Гипотеза:</b> просадка больше от рваного темпа, чем от объёма{history_hint}.\n"
        + build_action_block(slot, _score(_extract_metrics(day_summary)))
    )


def build_why_message(snapshot: Optional[Dict[str, Any]]) -> str:
    reasons = build_data_chips(snapshot, max_items=3, slot="day")
    if not reasons:
        reasons = ["Данных мало, поэтому вывод с низкой уверенностью"]
    return (
        "🧩 <b>Почему так</b>\n\n"
        "<b>Коротко:</b> день определяется ритмом, а не одним показателем.\n\n"
        "<b>Причины:</b>\n"
        + "\n".join(f"• {r}" for r in reasons[:3])
        + "\n\n🎯 <b>Рычаг:</b> один 15-минутный тихий блок без переключений."
    )


def build_push_message(slot: str, snapshot: Optional[Dict[str, Any]], day_key: str, partial: bool = False, mode: str = "short") -> str:
    mode = mode if mode in SPEECH_MODES else "short"
    if partial:
        return _no_data_message(slot)
    if mode == "facts":
        return render_facts_rich(snapshot, slot=slot)
    if mode == "roast":
        return render_roast(snapshot, slot=slot)

    verdict = build_verdict_label(snapshot, day_key, slot)
    chips = build_data_chips(snapshot, max_items=4, slot=slot)
    chips_block = "\n".join(f"• {c}" for c in chips) if chips else "• Ключевые метрики ещё догружаются"
    score = _score(_extract_metrics(snapshot))

    return (
        f"🟡 <b>{_slot_head(slot)}</b>\n\n"
        f"<b>Вердикт:</b> {verdict}.\n\n"
        f"📊 <b>Факты:</b>\n{chips_block}\n\n"
        f"🧠 <b>Смысл:</b> {_meaning_line(slot, score)}\n\n"
        f"{build_action_block(slot, score)}"
    )


def build_day_verdict_message(context: Dict[str, Any], day_key: str) -> str:
    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
    if context.get("day_status") == "no_data":
        return _no_data_message("day")
    return build_push_message("day", snapshot, day_key, partial=False, mode="short")


def build_day_detail_message(context: Dict[str, Any], day_key: str) -> str:
    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
    if context.get("day_status") == "no_data":
        return _no_data_message("day")
    chips = build_data_chips(snapshot, max_items=5, slot="day")
    return (
        "📌 <b>Разбор дня</b>\n\n"
        + "\n".join(f"• {c}" for c in chips)
        + "\n\n<b>Вывод:</b> сначала факты, потом темп; резкие ускорения сегодня не окупаются."
    )


def _metric_label(metric: str) -> str:
    labels = {
        "sleep": "сон",
        "body_battery": "Body Battery",
        "stress": "стресс",
        "rhr": "пульс покоя",
        "hrv": "HRV",
        "heart_rate": "пульс",
        "steps": "шаги",
        "respiration": "дыхание",
        "pulse_ox": "SpO₂",
    }
    return labels.get(metric, metric)


def build_metrics_message(context: Dict[str, Any]) -> str:
    available = context.get("available_metrics", [])
    missing = context.get("missing_metrics", [])
    av = ", ".join(_metric_label(m) for m in available) if available else "пока нет"
    ms = ", ".join(_metric_label(m) for m in missing[:8]) if missing else "—"
    days = context.get("available_days", [])
    date_range = f"{days[0]} — {days[-1]}" if len(days) > 1 else (days[0] if days else "—")
    return (
        "📚 <b>Доступные данные</b>\n\n"
        f"<b>История:</b> {int(context.get('available_days_count', 0))} дн.\n"
        f"<b>Диапазон:</b> {date_range}\n\n"
        f"<b>Есть:</b> {av}\n"
        f"<b>Пока нет:</b> {ms}"
    )


def build_history_message(context: Dict[str, Any]) -> str:
    days = context.get("available_days", [])
    if not days:
        return "🗂 <b>История данных</b>\n\nПока нет сохранённых дней."
    date_range = f"{days[0]} — {days[-1]}" if len(days) > 1 else days[0]
    compare_line = "можно сравнить дни." if len(days) > 1 else "пока только один день, без сравнения."
    return (
        "🗂 <b>История данных</b>\n\n"
        f"<b>Доступно:</b> {len(days)} дн.\n"
        f"<b>Диапазон:</b> {date_range}.\n"
        f"<b>Сейчас:</b> {compare_line}"
    )


def render_compare_days(day1: str, day2: str, snapshot1: Optional[Dict[str, Any]], snapshot2: Optional[Dict[str, Any]]) -> str:
    m1, m2 = _extract_metrics(snapshot1), _extract_metrics(snapshot2)
    score1, score2 = _score(m1), _score(m2)
    if score2 > score1 + 0.3:
        verdict = "второй день заметно ровнее"
    elif score1 > score2 + 0.3:
        verdict = "первый день прошёл устойчивее"
    else:
        verdict = "дни близки по ритму"

    diffs: List[str] = []
    for key, label in (("bb_now", "Body Battery"), ("stress_avg", "Стресс"), ("sleep_seconds", "Сон"), ("steps", "Шаги"), ("rhr", "Пульс покоя")):
        a, b = m1.get(key), m2.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if key == "sleep_seconds":
                diffs.append(f"• {label}: {_fmt_sleep_seconds(a)} → {_fmt_sleep_seconds(b)}")
            else:
                diffs.append(f"• {label}: {int(a)} → {int(b)} ({int(b-a):+d})")
    if not diffs:
        diffs = ["• По одному из дней метрик недостаточно для точного сравнения"]

    return (
        f"🔍 <b>Сравнение {day1} и {day2}</b>\n\n"
        f"<b>Вердикт:</b> {verdict}.\n\n"
        "<b>Различия:</b>\n"
        + "\n".join(diffs[:6])
        + "\n\n<b>Вывод:</b> лучше работает стабильный ритм без рывков.\n"
        "<b>Действие:</b> на сегодня оставить один главный приоритет и паузы между блоками."
    )


def build_weekly_verdict_message(derived: Dict[str, Any], chips: List[str], quest: str) -> str:
    safe = chips + ["недостаточно данных"] * 3
    return (
        "📊 <b>Вердикт недели</b>\n\n"
        f"<b>Статус:</b> {derived.get('hero_status', 'Неделя в работе')}.\n"
        f"• {safe[0]}\n• {safe[1]}\n• {safe[2]}\n\n"
        f"🎯 <b>Фокус:</b> {quest}"
    )


def should_send_visual_bonus(now_msk: dt.datetime, day_key: str, context: Dict[str, Any], times_sent_week: int) -> bool:
    if now_msk.hour < 8 or now_msk.hour >= 23:
        return False
    if times_sent_week >= 2:
        return False
    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
    score = _score(_extract_metrics(snapshot))
    if abs(score) >= 1.1:
        return True
    if context.get("day_status") == "partial":
        return False
    return (_seed(day_key, str(now_msk.isocalendar().week)) % 7) == 0


def choose_visual_state(snapshot: Optional[Dict[str, Any]], day_key: str) -> str:
    score = _score(_extract_metrics(snapshot))
    if score >= 1.2:
        return "Turbo Potato"
    if score >= 0.4:
        return "Cruise Potato"
    if score >= -0.2:
        return "Zen Potato"
    if score >= -0.9:
        return "Soft Potato"
    if score >= -1.5:
        return "Mashed Potato"
    return "Overclocked Potato"


def tone_violations(text: str) -> List[str]:
    issues: List[str] = []
    low = text.lower()
    if any(opener in low for opener in GENERIC_OPENERS):
        issues.append("generic_or_gendered_opener")
    if len(text) > 1300:
        issues.append("too_long")
    if "**" in text:
        issues.append("markdown_artifacts")
    if "<b>" not in text:
        issues.append("missing_structure")
    if "🧠" not in text and "📊" not in text:
        issues.append("missing_data_or_meaning_block")
    return issues
