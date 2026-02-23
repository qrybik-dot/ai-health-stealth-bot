import colorsys
import datetime as dt
import hashlib
import os
import random
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple


COLOR_STORY_DICT: Dict[str, Dict[str, str]] = {
    "Капут-мортуум": {"period_hint": "конец XIX века", "domain_hint": "живопись"},
    "Сольферино": {"period_hint": "вторая половина XIX века", "domain_hint": "текстиль"},
    "Селадон": {"period_hint": "раннее Новое время", "domain_hint": "керамика"},
    "Маренго": {"period_hint": "XIX век", "domain_hint": "мужской костюм"},
    "Шартрез": {"period_hint": "XVIII–XIX века", "domain_hint": "декоративная графика"},
    "Кокеликот": {"period_hint": "конец XIX века", "domain_hint": "плакат"},
    "Смальта": {"period_hint": "XIX век", "domain_hint": "эмаль и стекло"},
    "Электрик": {"period_hint": "XX век", "domain_hint": "типографика"},
    "Прюнелевый": {"period_hint": "XIX век", "domain_hint": "городская мода"},
    "Занаду": {"period_hint": "вторая половина XX века", "domain_hint": "интерьер"},
    "Вантаблэк": {
        "period_hint": "XXI век",
        "domain_hint": "инженерная оптика",
        "hard_fact": "Имя Vantablack закрепилось в инженерной оптике 2010-х как сверхтёмного покрытия.",
    },
    "Бедра испуганной нимфы": {"period_hint": "XVIII век", "domain_hint": "салонная мода"},
    "Бистр": {"period_hint": "XVIII–XIX века", "domain_hint": "графика и рисунок"},
    "Вердепом": {"period_hint": "конец XIX века", "domain_hint": "декор"},
    "Гелиотроп": {"period_hint": "рубеж XIX–XX веков", "domain_hint": "парфюмерная упаковка"},
    "Глясе": {"period_hint": "XX век", "domain_hint": "интерьерный текстиль"},
    "Голубьинный": {"period_hint": "начало XX века", "domain_hint": "городская форма"},
    "Гриз-де-лин": {"period_hint": "XIX век", "domain_hint": "типографика"},
    "Жонкилевый": {"period_hint": "XIX век", "domain_hint": "прикладное искусство"},
    "Изабелловый": {"period_hint": "XVIII–XIX века", "domain_hint": "военная форма"},
    "Индиго": {"period_hint": "XIX век", "domain_hint": "текстиль"},
    "Киноварь": {"period_hint": "XIX век", "domain_hint": "живопись и печать"},
    "Кобальтовый": {"period_hint": "XIX–XX века", "domain_hint": "керамика и эмаль"},
    "Кордован": {"period_hint": "XX век", "domain_hint": "кожаные изделия"},
    "Лазуритовый": {"period_hint": "конец XIX века", "domain_hint": "станковая живопись"},
    "Маджента": {"period_hint": "XIX век", "domain_hint": "типографика"},
    "Малахитовый": {"period_hint": "эпоха модерна", "domain_hint": "архитектурный декор"},
    "Мов": {"period_hint": "XIX век", "domain_hint": "мода"},
    "Навахо": {"period_hint": "XX век", "domain_hint": "дизайн интерьера"},
    "Оливин": {"period_hint": "XX век", "domain_hint": "промышленный дизайн"},
    "Орпимент": {"period_hint": "XIX век", "domain_hint": "живопись"},
    "Пюсовый": {"period_hint": "рубеж XIX–XX веков", "domain_hint": "городская одежда"},
    "Розовый кварц": {"period_hint": "конец XX века", "domain_hint": "косметика"},
    "Сангрия": {"period_hint": "XX век", "domain_hint": "упаковка напитков"},
    "Сизый": {"period_hint": "начало XX века", "domain_hint": "военная форма"},
    "Синопия": {"period_hint": "XIX век", "domain_hint": "академический рисунок"},
    "Терракота": {"period_hint": "конец XIX века", "domain_hint": "архитектура"},
    "Тиффани": {"period_hint": "начало XX века", "domain_hint": "ювелирный брендинг"},
    "Ультрамарин": {"period_hint": "XIX век", "domain_hint": "живопись"},
    "Фалунский красный": {"period_hint": "XIX век", "domain_hint": "фасадная архитектура"},
    "Фисташковый туман": {"period_hint": "конец XX века", "domain_hint": "интерьер"},
    "Хаки-дрилл": {"period_hint": "начало XX века", "domain_hint": "военная форма"},
    "Церулеум": {"period_hint": "XIX век", "domain_hint": "живопись"},
    "Шеол": {"period_hint": "XX век", "domain_hint": "сценический свет"},
    "Экрю": {"period_hint": "XIX–XX века", "domain_hint": "текстиль"},
    "Яшмовый": {"period_hint": "эпоха модерна", "domain_hint": "декоративное искусство"},
}

RARE_COLOR_NAMES_RU: List[str] = list(COLOR_STORY_DICT.keys())


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


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    raw = hex_color.lstrip("#")
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))


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


def build_color_metaphor_line(color: WeeklyColor) -> str:
    variants = [
        "как спокойный метроном для недели",
        "как мягкий фокус без лишнего шума",
        "как ровный фон для собранного режима",
        "как тихий вектор на устойчивый темп",
    ]
    return variants[_seed_for_week(color.week_id + "metaphor") % len(variants)]


def _build_period_domain_line(color: WeeklyColor) -> str:
    item = COLOR_STORY_DICT.get(color.name_ru)
    if item and item.get("hard_fact"):
        return item["hard_fact"]
    if item:
        return f"Первые устойчивые упоминания — {item['period_hint']}, чаще в сфере {item['domain_hint']}."
    common_domain = ["типографике", "архитектуре", "городской одежде", "предметном дизайне"]
    idx = _seed_for_week(color.week_id + "common_domain") % len(common_domain)
    return f"Название закреплялось в Европе XIX–XX вв., чаще в {common_domain[idx]}."


def _build_real_life_line(color: WeeklyColor) -> str:
    variants = [
        "Чаще замечается в упаковке, тканях и интерфейсных акцентах. 🎛️🧥",
        "Обычно встречается в обуви, аксессуарах и бытовых деталях. 👟💡",
        "Хорошо читается в фасадах, полиграфии и предметах интерьера. 🧱🏗️",
    ]
    return variants[_seed_for_week(color.week_id + "real_life") % len(variants)]


def _build_combo_line(color: WeeklyColor) -> str:
    variants = [
        "Сочетается с графитовым и молочным; компас недели — ровный ритм без перегруза.",
        "Хорошо работает с тёплым песочным и мягким серым; компас недели — умеренный темп и ясный фокус.",
        "Надёжная пара с холодным белым и глубоким синим; компас недели — последовательность и спокойный режим.",
    ]
    return variants[_seed_for_week(color.week_id + "combo") % len(variants)]


def build_color_story(color: WeeklyColor) -> str:
    return "\n".join(
        [
            f"{color.name_ru} ({color.hex})",
            _build_period_domain_line(color),
            _build_real_life_line(color),
            _build_combo_line(color),
        ]
    )


def generate_color_card_image(week_id: str, hex_color: str, out_dir: str = "artifacts/color_cards") -> str:
    from PIL import Image, ImageDraw
    os.makedirs(out_dir, exist_ok=True)
    path = Path(out_dir) / f"{week_id}.png"
    if path.exists():
        return str(path)

    rng = random.Random(_seed_for_week(week_id + hex_color))
    w, h = 1080, 1080
    base_r, base_g, base_b = _hex_to_rgb(hex_color)

    image = Image.new("RGB", (w, h))
    px = image.load()

    for y in range(h):
        ratio = y / (h - 1)
        for x in range(w):
            x_ratio = x / (w - 1)
            drift = (x_ratio - 0.5) * 18 + (ratio - 0.5) * 24
            r = max(0, min(255, int(base_r + drift - 16 + ratio * 28)))
            g = max(0, min(255, int(base_g + drift * 0.8 + x_ratio * 22)))
            b = max(0, min(255, int(base_b + drift * 0.9 - ratio * 18)))
            noise = rng.randint(-5, 5)
            px[x, y] = (
                max(0, min(255, r + noise)),
                max(0, min(255, g + noise)),
                max(0, min(255, b + noise)),
            )

    draw = ImageDraw.Draw(image, "RGBA")
    for _ in range(1 + rng.randint(0, 1)):
        x1 = rng.randint(80, 460)
        y1 = rng.randint(120, 720)
        x2 = x1 + rng.randint(320, 620)
        y2 = y1 + rng.randint(220, 420)
        alpha = rng.randint(25, 45)
        draw.ellipse((x1, y1, x2, y2), fill=(255, 255, 255, alpha))

    for _ in range(1 + rng.randint(0, 1)):
        x1 = rng.randint(120, 700)
        y1 = rng.randint(200, 860)
        x2 = x1 + rng.randint(180, 320)
        y2 = y1 + rng.randint(130, 260)
        alpha = rng.randint(30, 65)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=rng.randint(36, 90), fill=(20, 20, 24, alpha))

    image.save(path, format="PNG", optimize=True)
    return str(path)


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


def self_check_color_card() -> List[str]:
    problems: List[str] = []
    try:
        from PIL import Image
    except ModuleNotFoundError:
        return ["Pillow is not installed"]
    week_id = "2026-W18"
    color = generate_weekly_color(week_id)
    path = generate_color_card_image(week_id, color.hex)
    if not color.hex.startswith("#") or len(color.hex) != 7:
        problems.append("invalid hex in generated weekly color")
    if not os.path.exists(path):
        problems.append(f"image not created: {path}")
        return problems
    with Image.open(path) as img:
        if img.size != (1080, 1080):
            problems.append(f"invalid image size: {img.size}")
        if img.format != "PNG":
            problems.append(f"invalid image format: {img.format}")
    return problems
