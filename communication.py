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
    "food": ("поесть", "еда", "завтрак", "обед", "ужин", "перекус"),
    "load_advice": ("трен", "спорт", "нагруз", "можно ли", "интенсив"),
    "what15": ("15 минут", "15м", "что сделать сейчас", "что делать сейчас"),
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

SLOT_FOCUS = {
    "morning": "восстановление после сна и запас на первую половину дня",
    "midday": "сдвиг ресурса с утра и короткая коррекция курса",
    "evening": "закрытие дня, снижение шума и подготовка восстановления",
    "day": "общий ритм дня без ложной точности",
}


def _seed(*parts: str) -> int:
    return sum(ord(ch) for ch in "|".join(parts))


def resolve_intent(query: str) -> str:
    q = query.strip().lower()
    metric_first = (
        "respiration",
        "oxygen",
        "steps",
        "activity",
        "stress_metric",
        "sleep_metric",
        "pulse",
        "hrv_metric",
        "since_morning",
    )
    for intent in metric_first:
        patterns = INTENT_PATTERNS.get(intent, ())
        if any(p in q for p in patterns):
            return intent
    for intent, patterns in INTENT_PATTERNS.items():
        if intent in metric_first:
            continue
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


def _first_value(snapshot: Dict[str, Any], *paths: Tuple[str, ...]) -> Optional[Any]:
    for path in paths:
        value = _safe(snapshot, *path)
        if value not in (None, "", {}, []):
            return value
    return None


def _best_steps(snapshot: Dict[str, Any]) -> Optional[Any]:
    candidates = (
        _safe(snapshot, "steps", "totalSteps"),
        _safe(snapshot, "steps", "steps"),
        _safe(snapshot, "daily_steps", "totalSteps"),
        _safe(snapshot, "daily_steps", "steps"),
        _safe(snapshot, "daily_activity", "totalSteps"),
        _safe(snapshot, "daily_activity", "steps"),
        _safe(snapshot, "activity_summary", "totalSteps"),
        _safe(snapshot, "activity_summary", "steps"),
    )
    numeric = [value for value in candidates if isinstance(value, (int, float)) and 0 <= value <= 120000]
    return max(numeric) if numeric else None


def _extract_metrics(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    active_seconds = _first_value(snapshot, ("daily_activity", "activeSeconds"), ("daily_activity", "activeTimeSeconds"))
    moderate = _first_value(snapshot, ("intensity_minutes", "moderateMinutes"), ("intensity_minutes", "moderateIntensityMinutes"))
    vigorous = _first_value(snapshot, ("intensity_minutes", "vigorousMinutes"), ("intensity_minutes", "vigorousIntensityMinutes"))
    active_minutes = None
    if isinstance(moderate, (int, float)) or isinstance(vigorous, (int, float)):
        active_minutes = float(moderate or 0) + float(vigorous or 0)
    elif isinstance(active_seconds, (int, float)):
        active_minutes = float(active_seconds) / 60.0
    return {
        "bb_now": _first_value(snapshot, ("body_battery", "mostRecentValue"), ("daily_activity", "bodyBatteryMostRecentValue")),
        "bb_start": _first_value(snapshot, ("body_battery", "chargedValue"), ("daily_activity", "bodyBatteryChargedValue"), ("daily_activity", "bodyBatteryAtWakeTime")),
        "stress_avg": _first_value(snapshot, ("stress", "avgStressLevel"), ("stress", "overallStressLevel"), ("daily_activity", "averageStressLevel")),
        "stress_peak": _safe(snapshot, "stress", "maxStressLevel"),
        "sleep_seconds": _first_value(snapshot, ("sleep", "sleepTimeSeconds"), ("sleep", "totalSleepSeconds"), ("sleep", "dailySleepDTO", "sleepTimeSeconds")),
        "hrv_status": _first_value(snapshot, ("hrv_status", "status"), ("sleep", "hrvStatus", "status")),
        "hrv_weekly_avg": _first_value(snapshot, ("hrv", "weeklyAvg"), ("hrv", "hrvSummary", "weeklyAvg"), ("sleep", "avgOvernightHrv")),
        "steps": _best_steps(snapshot),
        "rhr": _first_value(snapshot, ("rhr", "restingHeartRate"), ("heart_rate", "restingHeartRate"), ("sleep", "restingHeartRate")),
        "respiration_avg": _first_value(snapshot, ("respiration", "avgWakingRespirationValue"), ("respiration", "latestRespirationValue"), ("daily_activity", "avgWakingRespirationValue")),
        "spo2_avg": _first_value(snapshot, ("pulse_ox", "avgSpo2"), ("pulse_ox", "mostRecentValue"), ("daily_activity", "averageSpo2")),
        "active_minutes": active_minutes,
        "moderate_minutes": moderate,
        "vigorous_minutes": vigorous,
        "active_kcal": _safe(snapshot, "daily_activity", "activeKilocalories"),
        "floors": _first_value(snapshot, ("daily_activity", "floorsAscended"), ("floors", "floorsAscended")),
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
        chips.append(("active_minutes", relevance.get("active_minutes", 0.3) + min(1.0, float(active) / 240.0), f"🏃 Активность: <b>{int(round(active))} мин</b>"))

    active_kcal = _fmt_int(m.get("active_kcal"), 0, 5000)
    if active_kcal is not None:
        chips.append(("active_kcal", 0.45 + min(1.0, active_kcal / 900.0), f"🔥 Активные ккал: <b>{active_kcal}</b>"))

    floors = _fmt_int(m.get("floors"), 0, 200)
    if floors is not None and floors > 0:
        chips.append(("floors", 0.35 + min(1.0, floors / 25.0), f"↗️ Этажи: <b>{floors}</b>"))

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
        # midday/evening: сон не повторяем по умолчанию, кроме сценариев почти без данных
        if slot in ("midday", "evening") and key == "sleep" and len(selected) >= 1:
            continue
        selected.append(text)
        used.add(key)
        if len(selected) >= max_items:
            break
    return selected


def _meaning_line(slot: str, score: float) -> str:
    if slot == "morning":
        if score >= 0.8:
            return "Ночь дала рабочий запас: первую половину дня можно вести собранно, без разгона."
        if score >= -0.1:
            return "Старт нормальный, но запас не бесконечный: темп важнее рывков."
        return "Восстановление слабое: утро лучше вести в режиме экономии."
    if slot == "midday":
        if score >= 0.8:
            return "День пока держится: задача — не растратить запас хаотичными переключениями."
        if score >= -0.1:
            return "Середина дня просит коррекцию: короткая пауза даст больше, чем ещё один рывок."
        return "Ресурс просел к середине дня: пора упрощать план, не добирать темп силой."
    if slot == "evening":
        if score >= 0.8:
            return "День закрывается ровно: лучший выигрыш сейчас — не разгонять вечер."
        if score >= -0.1:
            return "Финал рабочий, но восстановление надо защитить от позднего шума."
        return "К вечеру запас тонкий: новые задачи лучше не открывать."
    if score >= 0.8:
        base = "Ресурс в рабочем диапазоне: ставка на ровный темп окупится."
    elif score >= -0.1:
        base = "День держится на ритме: важнее стабильность, чем ускорения."
    else:
        base = "Ресурс просел: выигрыш сейчас в тишине и простом режиме."
    irony = " Без героизма — сегодня это и есть взрослая стратегия." if slot in ("midday", "evening") and int(score * 10) % 3 == 0 else ""
    return base + irony


def build_action_block(slot: str, score: float, metrics: Optional[Dict[str, Any]] = None) -> str:
    metrics = metrics or {}
    bb_now = _fmt_int(metrics.get("bb_now"), 0, 100)
    stress = _fmt_int(metrics.get("stress_avg"), 0, 100)
    sleep_seconds = metrics.get("sleep_seconds")
    steps = _fmt_int(metrics.get("steps"), 0, 120000)

    if slot == "morning":
        do = "один фокус-блок 60–90 минут до первого хаоса"
        avoid = "не стартовать день в режиме спринта"
        if isinstance(sleep_seconds, (int, float)) and sleep_seconds < 6 * 3600:
            do = "свет, вода и один короткий фокус-блок вместо тяжёлого старта"
            avoid = "не компенсировать короткий сон перегазовкой"
        elif bb_now is not None and bb_now >= 70:
            do = "поставить главный блок на первую половину дня"
            avoid = "не тратить хороший старт на мелкие переключения"
    elif slot == "midday":
        do = "пауза 7–10 минут без экрана и затем один приоритет"
        avoid = "не разгоняться кофеином и задачами одновременно"
        if stress is not None and stress >= 60:
            do = "7 минут без экрана, вода, затем одна простая задача"
            avoid = "не добавлять шум поверх высокого стресса"
        elif steps is not None and steps < 2500:
            do = "10–15 минут спокойной ходьбы и возврат к одному блоку"
            avoid = "не сидеть до вечера без сброса"
    else:
        do = "приглушить свет, закрыть новые задачи, выйти в тихий режим"
        avoid = "не делать вечерних добивок"
        if stress is not None and stress >= 60:
            do = "закрыть входящие, приглушить стимулы, оставить только бытовое"
            avoid = "не тащить дневной стресс в ночь"
        elif bb_now is not None and bb_now >= 60:
            do = "закрыть день спокойно и сохранить запас на завтра"
            avoid = "не превращать хороший ресурс в поздний спринт"
    if score < -0.8:
        do = "15 минут тихой ходьбы + вода + упрощение плана до базового"
    return f"🎯 <b>Действие:</b> {do}.\n🚫 <b>Лимит:</b> {avoid}."


def _no_data_message(slot: str) -> str:
    fallback_action = {
        "morning": "собрать спокойный старт и не повышать нагрузку до синхронизации",
        "midday": "сделать короткую паузу и вести вторую половину дня без рывка",
        "evening": "закрыть день мягко, без новых задач и позднего шума",
    }.get(slot, "один спокойный блок и пауза 5–7 минут")
    return (
        f"🟡 <b>{_slot_head(slot)}</b>\n\n"
        "<b>Вердикт:</b> данных пока мало, вывод предварительный.\n"
        "• Подгружена только часть метрик Garmin\n"
        "• Точный контекст дня пока не собран\n"
        "• Решения лучше принимать в щадящем режиме\n\n"
        "🧠 <b>Смысл:</b> пока держать ритм, не форсировать.\n"
        f"🎯 <b>Действие:</b> {fallback_action}.\n"
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
        "Спокойно и по делу: ритм читается, запас не бесконечный.\n\n"
        "<b>Факты:</b>\n"
        + facts_block
        + "\n\n"
        + f"<b>Гипотеза:</b> просадка чаще от рваного режима, чем от объёма{history_hint}.\n"
        + build_action_block(slot, _score(_extract_metrics(day_summary)), _extract_metrics(day_summary))
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
    metrics = _extract_metrics(snapshot)
    score = _score(metrics)

    return (
        f"🟡 <b>{_slot_head(slot)}</b>\n\n"
        f"<b>Вердикт:</b> {verdict}.\n\n"
        f"<b>Фокус слота:</b> {SLOT_FOCUS.get(slot, SLOT_FOCUS['day'])}.\n\n"
        f"📊 <b>Факты:</b>\n{chips_block}\n\n"
        f"🧠 <b>Смысл:</b> {_meaning_line(slot, score)}\n\n"
        f"{build_action_block(slot, score, metrics)}"
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
    limitation = ""
    if context.get("day_status") == "partial":
        missing = context.get("missing_metrics", [])
        missing_labels = [_metric_label(str(metric)) for metric in missing[:4]]
        missing_line = ", ".join(missing_labels) if missing_labels else "часть ключевых метрик"
        limitation = (
            "\n\n⚠️ <b>Ограничения:</b> разбор частичный — "
            f"не хватает: {missing_line}."
        )
    return (
        "📌 <b>Разбор дня</b>\n\n"
        + "\n".join(f"• {c}" for c in chips)
        + limitation
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


def _history_day_keys(history_cache: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    if not isinstance(history_cache, dict):
        return keys
    for key, payload in history_cache.items():
        if not isinstance(key, str) or key.startswith("_") or not isinstance(payload, dict):
            continue
        try:
            dt.date.fromisoformat(key)
        except ValueError:
            continue
        keys.append(key)
    return sorted(keys)


def _avg(values: List[float]) -> Optional[float]:
    clean = [float(v) for v in values if isinstance(v, (int, float))]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _range(values: List[float], formatter=None) -> str:
    clean = [float(v) for v in values if isinstance(v, (int, float))]
    if not clean:
        return "нет данных"
    fmt = formatter or (lambda value: str(int(round(value))))
    return f"{fmt(min(clean))}–{fmt(max(clean))}"


def build_period_summary_message(history_cache: Dict[str, Any], days: int = 30, title: str = "Месяц") -> str:
    day_keys = _history_day_keys(history_cache)[-max(1, days):]
    rows: List[Dict[str, Any]] = []
    for day in day_keys:
        snapshot = history_cache.get(day)
        metrics = _extract_metrics(snapshot if isinstance(snapshot, dict) else {})
        has_data = any(value is not None for value in metrics.values())
        if not has_data:
            continue
        rows.append({"day": day, "snapshot": snapshot, "metrics": metrics, "score": _score(metrics)})
    if not rows:
        return f"🗓 <b>{title}</b>\n\nДанных за период пока нет."

    best = max(rows, key=lambda row: row["score"])
    hard = min(rows, key=lambda row: row["score"])
    stress_avg = _avg([row["metrics"].get("stress_avg") for row in rows])
    bb_range = _range([row["metrics"].get("bb_now") for row in rows])
    sleep_avg = _avg([row["metrics"].get("sleep_seconds") for row in rows])
    steps_avg = _avg([row["metrics"].get("steps") for row in rows])
    enough = len(rows) >= min(14, days)
    status = "рабочая картина" if enough else "черновик: истории мало"
    sleep_line = "нет данных" if sleep_avg is None else _fmt_sleep_seconds(sleep_avg) or "нет данных"
    stress_line = "нет данных" if stress_avg is None else f"средний около {int(round(stress_avg))}"
    steps_line = "нет данных" if steps_avg is None else f"средние {int(round(steps_avg))}/день"
    focus = (
        "искать повторяющийся паттерн: сон → стресс → ресурс"
        if enough
        else "накопить хотя бы 14 дней, вывод пока без лишней уверенности"
    )

    return (
        f"🗓 <b>{title}</b>\n\n"
        f"<b>Статус:</b> {status}. Данных: {len(rows)}/{days} дней.\n"
        f"<b>Диапазон:</b> {day_keys[0]} — {day_keys[-1]}.\n"
        f"<b>Сон:</b> средний {sleep_line}.\n"
        f"<b>Стресс:</b> {stress_line}.\n"
        f"<b>Ресурс:</b> {bb_range}.\n"
        f"<b>Шаги:</b> {steps_line}.\n\n"
        f"<b>Лучший день:</b> {best['day']} — индекс {int(round(best['score'] * 10 + 50))}.\n"
        f"<b>Сложный день:</b> {hard['day']} — индекс {int(round(hard['score'] * 10 + 50))}.\n\n"
        f"🎯 <b>Фокус:</b> {focus}."
    )


def build_food_guidance_message(snapshot: Optional[Dict[str, Any]]) -> str:
    metrics = _extract_metrics(snapshot)
    facts = build_data_chips(snapshot, max_items=3, slot="midday")
    stress = _fmt_int(metrics.get("stress_avg"), 0, 100)
    bb_now = _fmt_int(metrics.get("bb_now"), 0, 100)
    active_minutes = _fmt_int(metrics.get("active_minutes"), 0, 600)
    if not facts:
        return (
            "🍽 <b>Еда сейчас</b>\n\n"
            "<b>По данным:</b> фактов за день мало.\n"
            "<b>Практично:</b> простой приём еды + вода, без экспериментов на пустом баке."
        )
    if stress is not None and stress >= 60:
        advice = "стресс высокий — лучше простая еда, вода и без тяжёлых экспериментов"
    elif bb_now is not None and bb_now < 35:
        advice = "ресурс низкий — ровная еда полезнее, чем героизм на кофе"
    elif active_minutes is not None and active_minutes >= 45:
        advice = "активности уже прилично — нормальный приём еды и вода, без добивки сладким"
    else:
        advice = "ресурс терпимый — держать обычный простой режим, без догоняться сладким как стратегией"
    return (
        "🍽 <b>Еда сейчас</b>\n\n"
        "<b>Факты:</b>\n"
        + "\n".join(f"• {line}" for line in facts)
        + f"\n\n<b>Практично:</b> {advice}."
    )


def build_load_guidance_message(snapshot: Optional[Dict[str, Any]]) -> str:
    metrics = _extract_metrics(snapshot)
    facts = build_data_chips(snapshot, max_items=3, slot="midday")
    stress = _fmt_int(metrics.get("stress_avg"), 0, 100)
    bb_now = _fmt_int(metrics.get("bb_now"), 0, 100)
    sleep_seconds = metrics.get("sleep_seconds")
    active_minutes = _fmt_int(metrics.get("active_minutes"), 0, 600)
    soft = (
        (bb_now is not None and bb_now < 40)
        or (stress is not None and stress >= 60)
        or (isinstance(sleep_seconds, (int, float)) and sleep_seconds < 6 * 3600)
    )
    mode = "лёгкий формат" if soft else "умеренный формат выглядит ок"
    limit = "без интенсивности и без вечерней добивки" if soft else "не превращать нормальный день в тест на выживание"
    if active_minutes is not None and active_minutes >= 60:
        limit = "активность уже набрана, дальше только спокойный формат"
    facts_block = "\n".join(f"• {line}" for line in facts) if facts else "• данных мало"
    activity_line = f"\n<b>Активность:</b> {active_minutes} мин." if active_minutes is not None else ""
    return (
        "🏃 <b>Нагрузка</b>\n\n"
        f"<b>По режиму:</b> {mode}.\n"
        "<b>Факты:</b>\n"
        + facts_block
        + activity_line
        + f"\n\n<b>Лимит:</b> {limit}."
    )


def build_mode_guidance_message(snapshot: Optional[Dict[str, Any]], slot: str = "midday") -> str:
    metrics = _extract_metrics(snapshot)
    score = _score(metrics)
    return (
        "🧭 <b>Режим сейчас</b>\n\n"
        f"<b>Фокус:</b> {SLOT_FOCUS.get(slot, SLOT_FOCUS['midday'])}.\n"
        f"<b>Смысл:</b> {_meaning_line(slot, score)}\n\n"
        + build_action_block(slot, score, metrics)
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
