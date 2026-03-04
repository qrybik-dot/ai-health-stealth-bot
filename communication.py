import datetime as dt
from typing import Any, Dict, List, Optional

KEY_METRICS = ("sleep", "body_battery", "stress", "rhr")

GENERIC_OPENERS = (
    "отличный вопрос",
    "давай посмотрим",
    "рада, что ты спросил",
)

INTENT_PATTERNS = {
    "day_verdict": ("как мой день", "вердикт дня", "итог дня", "день как"),
    "current_state": ("как я сейчас", "как сейчас", "мой статус", "мой режим"),
    "detail": ("детализ", "деталь", "разбор"),
    "metrics": ("какие метрики", "что по метрикам", "что видно по данным"),
    "history": ("за сколько", "сколько дней", "история данных", "диапазон"),
    "weekly": ("недел", "итог недели", "weekly"),
    "visual": ("покажи красиво", "сделай карточкой", "дай визуально"),
    "why": ("почему", "из-за чего", "причины"),
}

SPEECH_MODES = {"short", "facts", "roast"}

STATE_POOL = {
    "high": ["Машина 🏎", "Турбокартоха", "Ровный болид", "Боевой клубень"],
    "steady": ["Рабочий клубень", "Ровный режим", "Едет без скрипа", "Картоха в форме"],
    "border": ["На честном топливе", "Ещё едет, но без понтов", "Без форсажа", "На морально-волевых"],
    "low": ["Тёплое пюре 🫠", "Подуставший гарнир", "Варёный режим", "Почти столовка"],
    "overload": ["Чистое пюре 🫠", "Овощной отдел открыт", "Размазня, но с достоинством", "Гарнир, не двигатель"],
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
    bb_now = _safe(snapshot, "body_battery", "mostRecentValue")
    bb_start = _safe(snapshot, "body_battery", "chargedValue")
    stress_avg = _safe(snapshot, "stress", "avgStressLevel")
    stress_peak = _safe(snapshot, "stress", "maxStressLevel")
    sleep_seconds = _safe(snapshot, "sleep", "sleepTimeSeconds") or _safe(snapshot, "sleep", "totalSleepSeconds")
    hrv_status = _safe(snapshot, "hrv_status", "status")
    steps = _safe(snapshot, "steps", "totalSteps")
    rhr = _safe(snapshot, "rhr", "restingHeartRate")
    return {
        "bb_now": bb_now,
        "bb_start": bb_start,
        "stress_avg": stress_avg,
        "stress_peak": stress_peak,
        "sleep_seconds": sleep_seconds,
        "hrv_status": hrv_status,
        "steps": steps,
        "rhr": rhr,
    }


def _score(metrics: Dict[str, Any]) -> float:
    score = 0.0
    if isinstance(metrics.get("bb_now"), (int, float)):
        score += (float(metrics["bb_now"]) - 55.0) / 25.0
    if isinstance(metrics.get("stress_avg"), (int, float)):
        score += (40.0 - float(metrics["stress_avg"])) / 30.0
    if isinstance(metrics.get("sleep_seconds"), (int, float)):
        score += ((float(metrics["sleep_seconds"]) / 3600.0) - 7.0) / 1.8
    return round(score, 2)




def _fmt_int(value: Any, min_v: int = 0, max_v: int = 999) -> Optional[int]:
    if not isinstance(value, (int, float)):
        return None
    iv = int(value)
    if iv < min_v or iv > max_v:
        return None
    return iv


def _safe_rhr(value: Any) -> Optional[int]:
    return _fmt_int(value, 30, 130)


def _fmt_sleep_seconds(value: Any) -> Optional[str]:
    if not isinstance(value, (int, float)):
        return None
    total_min = int(value // 60)
    if total_min <= 0 or total_min > 24 * 60:
        return None
    return f"{total_min // 60}ч {total_min % 60:02d}м"


def _reason_lines(snapshot: Optional[Dict[str, Any]]) -> List[str]:
    m = _extract_metrics(snapshot)
    reasons: List[str] = []
    bb_now = _fmt_int(m.get("bb_now"), 0, 100)
    if bb_now is not None:
        trend = "низкий" if bb_now < 40 else ("высокий" if bb_now >= 70 else "средний")
        reasons.append(f"• 🔋 Battery {bb_now} ({trend} уровень ресурса)")
    stress_avg = _fmt_int(m.get("stress_avg"), 0, 100)
    if stress_avg is not None:
        direction = "высокий" if stress_avg >= 45 else "контролируемый"
        reasons.append(f"• 😵 Стресс {stress_avg} ({direction})")
    sleep_seconds = m.get("sleep_seconds")
    sleep = _fmt_sleep_seconds(sleep_seconds)
    if sleep is not None:
        sleep_hours = int(float(sleep_seconds) // 3600) if isinstance(sleep_seconds, (int, float)) else 0
        direction = "короткий" if sleep_hours < 7 else "достаточный"
        reasons.append(f"• 😴 Сон {sleep} ({direction})")
    if len(reasons) < 3:
        rhr = _safe_rhr(m.get("rhr"))
        if rhr is not None:
            reasons.append(f"• ❤️ RHR {rhr} (фон восстановления)")
    while len(reasons) < 3:
        reasons.append("• 📉 Данных мало: вывод предварительный")
    return reasons[:3]


def build_why_message(snapshot: Optional[Dict[str, Any]]) -> str:
    reasons = _reason_lines(snapshot)
    return (
        "🧩 <b>Почему так:</b> день держится, но запас ресурса зависит от ритма, а не от рывков.\n\n"
        "<b>Причины:</b>\n"
        + "\n".join(reasons)
        + "\n\n🎯 <b>Рычаг:</b> 15 минут ровного темпа без переключений и затем короткая пауза."
    )

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
    metrics = _extract_metrics(snapshot)
    bucket = _state_bucket(_score(metrics))
    options = STATE_POOL[bucket]
    return options[_seed(day_key, slot, bucket) % len(options)]


def build_mode_phrase(slot: str, verdict_label: str) -> str:
    heads = {
        "morning": "Вердикт утра",
        "midday": "Вердикт середины дня",
        "evening": "Вердикт вечера",
        "day": "Вердикт дня",
        "current": "Вердикт момента",
        "weekly": "Вердикт недели",
    }
    return f"🥔 <b>{heads.get(slot, 'Вердикт')}</b>\n\nСегодня ты в режиме <b>{verdict_label}</b>."


def build_data_chips(snapshot: Optional[Dict[str, Any]], max_items: int = 4) -> List[str]:
    m = _extract_metrics(snapshot)
    chips: List[str] = []
    bb_now = _fmt_int(m.get("bb_now"), 0, 100)
    bb_start = _fmt_int(m.get("bb_start"), 0, 100)
    if bb_now is not None:
        if bb_start is not None:
            chips.append(f"🔋 Battery: <b>{bb_start} → {bb_now}</b>")
        else:
            chips.append(f"🔋 Battery: <b>{bb_now}</b>")
    stress_avg = _fmt_int(m.get("stress_avg"), 0, 100)
    stress_peak = _fmt_int(m.get("stress_peak"), 0, 100)
    if stress_avg is not None:
        if stress_peak is not None:
            chips.append(f"😵 Стресс: <b>{stress_avg}</b>, пики до <b>{stress_peak}</b>")
        else:
            chips.append(f"😵 Стресс: <b>{stress_avg}</b>")
    sleep_fmt = _fmt_sleep_seconds(m.get("sleep_seconds"))
    if sleep_fmt:
        chips.append(f"🛌 Сон: <b>{sleep_fmt}</b>")
    if m.get("hrv_status"):
        chips.append(f"❤️ HRV: <b>{m['hrv_status']}</b>")
    else:
        rhr = _fmt_int(m.get("rhr"), 25, 220)
        if rhr is not None:
            chips.append(f"🫀 RHR: <b>{rhr}</b>")
    steps = _fmt_int(m.get("steps"), 0, 120000)
    if steps is not None:
        chips.append(f"🚶 Шаги: <b>{steps}</b>")
    return chips[:max_items]


def _no_data_message(slot: str) -> str:
    return (
        f"🥔 <b>{build_mode_phrase(slot, 'Предварительный режим').split('</b>')[0].replace('Сегодня ты в режиме <b>Предварительный режим', '').replace('🥔 <b>', '')}</b>\n\n"
        "📊 <b>Данных маловато</b>\n"
        "Есть только часть метрик, поэтому вердикт пока черновой.\n\n"
        "🎯 <b>Что делать:</b> один спокойный блок и пауза 5–7 минут.\n"
        "🚫 <b>Чего не делать:</b> не разгонять день до следующей синхронизации."
    )


def build_action_block(slot: str, score: float) -> str:
    if slot == "morning":
        do = "сделай один длинный фокус-блок, пока мотор не начал спорить с реальностью"
        avoid = "не стартуй день как будто уже финал дедлайна"
    elif slot == "midday":
        do = "короткая пауза без экрана, потом один нормальный блок"
        avoid = "не пытайся героически догнать всё разом"
    else:
        do = "снижай темп: свет тише, шум ниже, задач новых не открывай"
        avoid = "без вечерних добивок и разговоров на максималках"
    if score < -0.8:
        do = "режим экономии: только базовые задачи и мягкое завершение"
    return f"🎯 <b>Что делать:</b> {do}.\n🚫 <b>Чего не делать:</b> {avoid}."


def render_facts_rich(day_summary: Optional[Dict[str, Any]]) -> str:
    chips = build_data_chips(day_summary, max_items=5)
    top = "\n".join(f"• {c}" for c in chips) if chips else "• ключевые метрики ещё не догружены"
    extras = [
        "дыхание/SpO2/этажи/интенсивность/тренировки — показываем по мере прихода данных",
    ]
    return (
        "📊 <b>По фактам (top-5)</b>\n"
        + top
        + "\n\n"
        + "🧾 <b>Остальное</b>\n• "
        + "\n• ".join(extras)
        + "\n\n"
        + "<b>Вывод:</b> держим ровный режим, без лишних ускорений."
    )


def render_roast(day_summary: Optional[Dict[str, Any]], history_optional: Optional[Dict[str, Any]] = None, slot: str = "day") -> str:
    chips = build_data_chips(day_summary, max_items=2)
    facts = "\n".join(f"• {c}" for c in chips) if chips else "• метрик мало, но ритм дня всё равно читается"
    history_hint = ""
    if isinstance(history_optional, dict) and isinstance(history_optional.get("available_days_count"), int):
        history_hint = f" (история: {history_optional.get('available_days_count')} дн.)"
    return (
        "🥔 <b>Пожарь</b>\n"
        "Темп бодрый, но день не для заносов.\n\n"
        "<b>Факты:</b>\n"
        + facts
        + "\n\n"
        + f"<b>Гипотеза:</b> просадка больше связана с рваным ритмом, чем с объёмом нагрузки{history_hint}.\n"
        + build_action_block(slot, _score(_extract_metrics(day_summary)))
    )


def build_push_message(
    slot: str,
    snapshot: Optional[Dict[str, Any]],
    day_key: str,
    partial: bool = False,
    mode: str = "short",
) -> str:
    mode = mode if mode in SPEECH_MODES else "short"
    if partial:
        return _no_data_message(slot)
    verdict = build_verdict_label(snapshot, day_key, slot)
    metrics = _extract_metrics(snapshot)
    score = _score(metrics)
    chips = build_data_chips(snapshot, max_items=3)
    chips_block = "\n".join(f"• {c}" for c in chips) if chips else "• 📊 Ключевые метрики ещё догружаются"
    line = "Ещё едет, но без понтов." if score < 0 else "Мотор живой, но коробку лучше не рвать."
    if mode == "facts":
        return render_facts_rich(snapshot)
    if mode == "roast":
        return render_roast(snapshot, slot=slot)
    return (
        f"{build_mode_phrase(slot, verdict)}\n\n"
        f"📊 <b>По фактам:</b>\n{chips_block}\n\n"
        f"🧠 <b>Смысл:</b> {line}\n\n"
        f"{build_action_block(slot, score)}"
    )


def build_day_verdict_message(context: Dict[str, Any], day_key: str) -> str:
    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
    if context.get("day_status") == "no_data":
        return _no_data_message("day")
    verdict = build_verdict_label(snapshot, day_key, "day")
    chips = build_data_chips(snapshot)
    chips_block = "\n".join(f"• {chip}" for chip in chips[:4]) if chips else "• 📊 Нужен следующий sync для плотной картины"
    return (
        f"🥔 <b>Вердикт дня</b>\n\n"
        f"Ты сегодня в режиме <b>{verdict}</b>.\n\n"
        f"🔋 <b>По фактам:</b>\n{chips_block}\n\n"
        "🧠 <b>Что это значит:</b> день забрал ресурс не драмой, а суммой мелких нагрузок.\n\n"
        f"{build_action_block('evening', _score(_extract_metrics(snapshot)))}"
    )


def build_day_detail_message(context: Dict[str, Any], day_key: str) -> str:
    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
    if context.get("day_status") == "no_data":
        return _no_data_message("day")
    chips = build_data_chips(snapshot, max_items=5)
    limits = ""
    if context.get("key_metrics_present_count", 0) < 3:
        limits = "\n\n<b>Ограничения:</b> разбор частичный, часть ключевых метрик ещё не приехала."
    return (
        "📌 <b>Разбор за день</b>\n\n"
        "<b>Что хорошо:</b> старт дня собрался без развала по ритму.\n"
        "<b>Что съело ресурс:</b> основная просадка чаще приходит от стресса, а не от шагов.\n"
        f"<b>Факты:</b> {'; '.join(chips[:4]) if chips else 'метрики частично доступны'}"
        f"{limits}\n\n"
        "<b>Практический вывод:</b> сегодня докатить ровно, а не устраивать ралли на остатках батарейки."
    )


def build_metrics_message(context: Dict[str, Any]) -> str:
    available = context.get("available_metrics", [])
    missing = context.get("missing_metrics", [])
    av = ", ".join(_metric_label(m) for m in available) if available else "пока нет"
    ms = ", ".join(_metric_label(m) for m in missing[:6]) if missing else "—"
    return (
        "📊 <b>Что уже видно по данным</b>\n\n"
        f"<b>Есть:</b> {av}.\n"
        f"<b>Пока нет:</b> {ms}.\n\n"
        "<b>Итог:</b> картина достаточная, чтобы дать вердикт без гадания на картофельной гуще."
    )


def build_history_message(context: Dict[str, Any]) -> str:
    days = context.get("available_days", [])
    if not days:
        return "🗂 <b>История данных</b>\n\nПока нет сохранённых дней."
    date_range = f"{days[0]} — {days[-1]}" if len(days) > 1 else days[0]
    compare_line = "можно сравнить дни между собой." if len(days) > 1 else "пока только один день, без сравнений."
    return (
        "🗂 <b>История данных</b>\n\n"
        f"<b>Доступно:</b> {len(days)} дн.\n"
        f"<b>Диапазон:</b> {date_range}.\n\n"
        f"<b>Сейчас можно:</b> {compare_line}"
    )


def _metric_label(metric: str) -> str:
    labels = {
        "sleep": "сон",
        "body_battery": "Body Battery",
        "stress": "стресс",
        "rhr": "RHR",
        "hrv": "ВСР",
        "heart_rate": "пульс",
        "steps": "шаги",
        "respiration": "дыхание",
        "pulse_ox": "SpO2",
    }
    return labels.get(metric, metric)


def should_send_visual_bonus(now_msk: dt.datetime, day_key: str, context: Dict[str, Any], times_sent_week: int) -> bool:
    if now_msk.hour < 8 or now_msk.hour >= 23:
        return False
    if times_sent_week >= 2:
        return False
    snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
    metrics = _extract_metrics(snapshot)
    score = _score(metrics)
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


def build_weekly_verdict_message(derived: Dict[str, Any], chips: List[str], quest: str) -> str:
    return (
        "📊 <b>Вердикт недели</b>\n\n"
        f"<b>Статус:</b> {derived.get('hero_status', 'Неделя в работе')}.\n"
        f"• {chips[0]}\n"
        f"• {chips[1]}\n"
        f"• {chips[2]}\n\n"
        f"🎯 <b>Фокус:</b> {quest}"
    )


def tone_violations(text: str) -> List[str]:
    issues: List[str] = []
    low = text.lower()
    if any(opener in low for opener in GENERIC_OPENERS):
        issues.append("generic_opener")
    if len(text) > 1300:
        issues.append("too_long")
    if "<b>" not in text:
        issues.append("missing_structure")
    if "🧠" not in text and "📊" not in text:
        issues.append("missing_data_or_meaning_block")
    return issues
