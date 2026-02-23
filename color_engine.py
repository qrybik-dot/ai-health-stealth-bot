import colorsys
import datetime as dt
import hashlib
import random
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Tuple

RARE_COLOR_NAMES_RU: List[str] = [
    "Капут-мортуум",
    "Сольферино",
    "Селадон",
    "Маренго",
    "Шартрез",
    "Кокеликот",
    "Смальта",
    "Электрик",
    "Прюнелевый",
    "Занаду",
    "Вантаблэк",
    "Бедра испуганной нимфы",
    "Бистр",
    "Вердепом",
    "Гелиотроп",
    "Глясе",
    "Голубьинный",
    "Гриз-де-лин",
    "Жонкилевый",
    "Изабелловый",
    "Индиго",
    "Киноварь",
    "Кобальтовый",
    "Кордован",
    "Лазуритовый",
    "Маджента",
    "Малахитовый",
    "Мов",
    "Навахо",
    "Оливин",
    "Орпимент",
    "Пюсовый",
    "Розовый кварц",
    "Сангрия",
    "Сизый",
    "Синопия",
    "Терракота",
    "Тиффани",
    "Ультрамарин",
    "Фалунский красный",
    "Фисташковый туман",
    "Хаки-дрилл",
    "Церулеум",
    "Шеол",
    "Экрю",
    "Яшмовый",
]

RARE_NOTES = {
    "Капут-мортуум": "Название пришло из живописи: этим пигментом передавали глубокие землистые тени.",
    "Сольферино": "Цвет получил известность в XIX веке и долго считался смелым акцентом для ткани.",
    "Селадон": "Селадон связывают с керамикой Восточной Азии: мягкий тон для спокойных поверхностей.",
    "Шартрез": "Шартрез уходит корнями в палитру французских монастырских травяных настоек.",
    "Вантаблэк": "В культуре это имя стало символом предельной темноты и визуальной тишины.",
    "Маренго": "Маренго исторически ассоциируется с строгими костюмными тканями и сдержанностью.",
}


@dataclass
class WeeklyColor:
    week_id: str
    hex: str
    hsl: Dict[str, int]
    name_ru: str
    rarity_level: str
    is_rare_name: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def iso_week_id(today: dt.date | None = None) -> str:
    d = today or dt.date.today()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _seed_for_week(week_id: str) -> int:
    digest = hashlib.sha256(week_id.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _hsl_to_hex(h: float, s: float, l: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l / 100.0, s / 100.0)
    return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


def _pick_rarity(rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.08:
        return "exotic"
    if roll < 0.30:
        return "rare"
    return "common"


def _hue_family(h: int) -> Tuple[str, str]:
    families = [
        (15, ("красно", "красный")),
        (45, ("оранжево", "оранжевый")),
        (70, ("жёлто", "жёлтый")),
        (150, ("зелёно", "зелёный")),
        (200, ("бирюзово", "бирюзовый")),
        (250, ("сине", "синий")),
        (290, ("фиолетово", "фиолетовый")),
        (340, ("пурпурно", "пурпурный")),
        (361, ("красно", "красный")),
    ]
    for border, pair in families:
        if h < border:
            return pair
    return "нейтрально", "нейтральный"


def _descriptive_name_ru(h: int, s: int, l: int) -> str:
    prefix, base = _hue_family(h)
    if s < 28:
        if l < 35:
            return "Графитовый нейтральный"
        if l > 70:
            return "Светлый дымчато-серый"
        return "Спокойный серо-нейтральный"

    depth = ""
    if l < 34:
        depth = "Глубокий "
    elif l > 68:
        depth = "Светлый "

    if s > 72:
        return f"{depth}{base}".strip().capitalize()

    second_prefix, second_base = _hue_family((h + 28) % 360)
    if second_base == base:
        return f"{depth}{base}".strip().capitalize()
    return f"{depth}{prefix}-{second_base}".strip().capitalize()


def generate_weekly_color(week_id: str) -> WeeklyColor:
    rng = random.Random(_seed_for_week(week_id))

    hue = int(rng.random() * 360)
    sat = int(48 + rng.random() * 38)  # 48..86
    lig = int(40 + rng.random() * 30)  # 40..70

    rarity_level = _pick_rarity(rng)
    rare_name_roll = rng.random()
    use_rare_name = rare_name_roll < 0.18

    if use_rare_name:
        name_ru = RARE_COLOR_NAMES_RU[rng.randrange(len(RARE_COLOR_NAMES_RU))]
    else:
        name_ru = _descriptive_name_ru(hue, sat, lig)

    return WeeklyColor(
        week_id=week_id,
        hex=_hsl_to_hex(hue, sat, lig),
        hsl={"h": hue, "s": sat, "l": lig},
        name_ru=name_ru,
        rarity_level=rarity_level,
        is_rare_name=use_rare_name,
    )




def weekly_color_from_dict(payload: Dict[str, Any]) -> WeeklyColor:
    return WeeklyColor(
        week_id=str(payload["week_id"]),
        hex=str(payload["hex"]),
        hsl={
            "h": int(payload["hsl"]["h"]),
            "s": int(payload["hsl"]["s"]),
            "l": int(payload["hsl"]["l"]),
        },
        name_ru=str(payload["name_ru"]),
        rarity_level=str(payload["rarity_level"]),
        is_rare_name=bool(payload.get("is_rare_name", False)),
    )

def build_color_signal_line(color: WeeklyColor) -> str:
    signals = [
        "На этой неделе он про ровный темп и аккуратный фокус.",
        "Сигнал недели: держать ритм без резких рывков.",
        "Оттенок недели про спокойную собранность в делах дня.",
        "Хороший фон для недели, где важны последовательность и баланс режима.",
    ]
    idx = _seed_for_week(color.week_id + color.hex) % len(signals)
    return signals[idx]


def build_color_story(color: WeeklyColor) -> str:
    rng = random.Random(_seed_for_week(color.week_id + "story"))
    templates = [
        "Этот оттенок обычно воспринимается как знак устойчивого ритма: без суеты, но с ясным курсом.",
        "В палитре недели он работает как тихий ориентир — не давит, а помогает держать структуру дня.",
        "Его характер — про аккуратную энергию: когда задачи двигаются последовательно и без перегруза.",
        "Такой цвет хорошо сочетается с режимом, где важны повторяемость и чистый фокус на главном.",
    ]
    lines = rng.sample(templates, 2)
    if rng.random() < 0.35:
        lines.append("Это редкий момент для палитры: оттенок звучит выразительнее обычного, но остаётся собранным.")

    if color.is_rare_name and color.name_ru in RARE_NOTES:
        lines.append(RARE_NOTES[color.name_ru])

    return " ".join(lines[:3])


def self_check_color_engine() -> List[str]:
    problems: List[str] = []
    for week in range(1, 11):
        week_id = f"2026-W{week:02d}"
        color = generate_weekly_color(week_id)
        if not color.hex.startswith("#") or len(color.hex) != 7:
            problems.append(f"{week_id}: invalid hex {color.hex}")
        if not (0 <= color.hsl["h"] <= 359):
            problems.append(f"{week_id}: invalid hue")
        if not (40 <= color.hsl["l"] <= 75):
            problems.append(f"{week_id}: risky lightness")
        if len(color.name_ru.strip()) < 4:
            problems.append(f"{week_id}: name too short")
    return problems
