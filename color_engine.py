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


def _hex_to_hsl(hex_color: str) -> Tuple[int, int, int]:
    r, g, b = _hex_to_rgb(hex_color)
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return int(h * 360) % 360, int(s * 100), int(l * 100)


def generate_daily_accent_hex(chat_id: str, day: str, week_id: str, week_color_hex: str) -> str:
    week_h, _, _ = _hex_to_hsl(week_color_hex)
    seed = _seed_for_week(f"{chat_id}|{day}|{week_id}")
    rng = random.Random(seed)
    variants = ["complementary", "triad", "split_lo", "split_hi", "controlled"]
    variant = variants[rng.randrange(len(variants))]

    if variant == "complementary":
        hue = (week_h + 180) % 360
    elif variant == "triad":
        hue = (week_h + 120) % 360
    elif variant == "split_lo":
        hue = (week_h + 150) % 360
    elif variant == "split_hi":
        hue = (week_h + 210) % 360
    else:
        hue = (week_h + 90 + rng.randint(-35, 35)) % 360

    sat = 52 + rng.randint(0, 24)
    lig = 45 + rng.randint(0, 20)
    accent_hex = _hsl_to_hex(hue, sat, lig)
    if accent_hex.upper() == week_color_hex.upper():
        accent_hex = _hsl_to_hex((hue + 37) % 360, sat, lig)
    return accent_hex


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
        "Заметьте его в одежде и интерфейсных акцентах: 👕🎛️",
        "Часто встречается в бумаге и печати: 📄🖨️",
        "Хорошо читается в фасадах и бытовом свете: 🧱💡",
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
            drift = (x_ratio - 0.5) * 12 + (ratio - 0.5) * 16
            r = max(0, min(255, int(base_r + drift - 8 + ratio * 16)))
            g = max(0, min(255, int(base_g + drift * 0.65 + x_ratio * 14)))
            b = max(0, min(255, int(base_b + drift * 0.75 - ratio * 10)))
            noise = rng.randint(-3, 3)
            px[x, y] = (
                max(0, min(255, r + noise)),
                max(0, min(255, g + noise)),
                max(0, min(255, b + noise)),
            )

    draw = ImageDraw.Draw(image, "RGBA")
    x1 = rng.randint(40, 220)
    y1 = rng.randint(80, 260)
    x2 = x1 + rng.randint(640, 860)
    y2 = y1 + rng.randint(560, 740)
    draw.ellipse((x1, y1, x2, y2), fill=(255, 255, 255, rng.randint(30, 46)))

    ax1 = rng.randint(740, 900)
    ay1 = rng.randint(140, 260)
    ax2 = ax1 + rng.randint(130, 210)
    ay2 = ay1 + rng.randint(130, 230)
    draw.rounded_rectangle((ax1, ay1, ax2, ay2), radius=rng.randint(34, 78), fill=(26, 26, 30, rng.randint(44, 68)))

    image.save(path, format="PNG", optimize=True)
    return str(path)


def _mix_rgb(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] * (1 - t) + b[0] * t),
        int(a[1] * (1 - t) + b[1] * t),
        int(a[2] * (1 - t) + b[2] * t),
    )


def _build_daily_palette(week_color_hex: str, accent_hex: str, mode_tag: str) -> Tuple[Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]]:
    base = _hex_to_rgb(week_color_hex)
    accent_base = _hex_to_rgb(accent_hex)
    if mode_tag == "no_data":
        gray = int(base[0] * 0.3 + base[1] * 0.59 + base[2] * 0.11)
        start = (max(36, gray - 26), max(36, gray - 20), max(40, gray - 14))
        end = (min(186, gray + 28), min(186, gray + 30), min(192, gray + 34))
        accent = _mix_rgb(accent_base, (180, 180, 186), 0.5)
        return start, end, accent
    if mode_tag == "recovery":
        return _mix_rgb(base, (235, 235, 242), 0.46), _mix_rgb(base, (250, 250, 252), 0.63), _mix_rgb(accent_base, (255, 255, 255), 0.35)
    if mode_tag == "push":
        return _mix_rgb(base, (18, 20, 26), 0.32), _mix_rgb(base, (10, 12, 18), 0.22), _mix_rgb(accent_base, (255, 255, 255), 0.14)
    return _mix_rgb(base, (228, 230, 240), 0.36), _mix_rgb(base, (248, 248, 252), 0.54), _mix_rgb(accent_base, (255, 255, 255), 0.2)


def generate_today_card_image(
    chat_id: str,
    day: str,
    week_id: str,
    week_color_hex: str,
    mode_tag: str,
    accent_hex: str,
    out_dir: str = "generated/today_cards",
) -> str:
    from PIL import Image, ImageDraw

    os.makedirs(out_dir, exist_ok=True)
    safe_chat = str(chat_id).replace("/", "_")
    path = Path(out_dir) / f"{safe_chat}_{day}_{mode_tag}_{week_id}.png"
    if path.exists():
        return str(path)

    w, h = 1080, 1080
    seed = _seed_for_week(f"{chat_id}|{day}|{mode_tag}|{week_id}|{accent_hex}")
    rng = random.Random(seed)
    start, end, accent = _build_daily_palette(week_color_hex, accent_hex, mode_tag)
    shape_count = {"recovery": 1, "steady": 2, "push": 3, "no_data": 1}.get(mode_tag, 2)

    try:
        image = Image.new("RGB", (w, h))
        px = image.load()
        for y in range(h):
            y_ratio = y / (h - 1)
            for x in range(w):
                x_ratio = x / (w - 1)
                mix = y_ratio * 0.75 + x_ratio * 0.25
                r, g, b = _mix_rgb(start, end, mix)
                grain = rng.randint(-3, 3)
                px[x, y] = (
                    max(0, min(255, r + grain)),
                    max(0, min(255, g + grain)),
                    max(0, min(255, b + grain)),
                )

        draw = ImageDraw.Draw(image, "RGBA")
        bx1 = rng.randint(60, 180)
        by1 = rng.randint(130, 260)
        bx2 = bx1 + rng.randint(620, 860)
        by2 = by1 + rng.randint(560, 780)
        draw.ellipse((bx1, by1, bx2, by2), fill=(*accent, 72 if mode_tag != "push" else 58))

        if shape_count >= 2:
            sx1 = rng.randint(760, 900)
            sy1 = rng.randint(120, 260)
            sx2 = sx1 + rng.randint(120, 210)
            sy2 = sy1 + rng.randint(120, 230)
            draw.rounded_rectangle((sx1, sy1, sx2, sy2), radius=rng.randint(30, 80), fill=(*_mix_rgb(accent, (20, 20, 24), 0.75), 70 if mode_tag == "push" else 48))
        if shape_count >= 3:
            tx1 = rng.randint(160, 300)
            ty1 = rng.randint(760, 880)
            tx2 = tx1 + rng.randint(200, 320)
            ty2 = ty1 + rng.randint(90, 170)
            draw.ellipse((tx1, ty1, tx2, ty2), fill=(255, 255, 255, 34))

        image.save(path, format="PNG", optimize=True)
        return str(path)
    except Exception:
        fallback = Image.new("RGB", (w, h))
        fp = fallback.load()
        for y in range(h):
            mix = y / (h - 1)
            row = _mix_rgb(start, end, mix)
            for x in range(w):
                fp[x, y] = row
        fallback.save(path, format="PNG", optimize=True)
        return str(path)


def self_check_today_card() -> List[str]:
    problems: List[str] = []
    try:
        from PIL import Image
    except ModuleNotFoundError:
        print("Pillow not installed in this environment; skipping image generation self-check.")
        return []

    day = "2026-05-02"
    week_id = "2026-W18"
    mode_tags = ["recovery", "steady", "push", "no_data"]
    for mode_tag in mode_tags:
        path = generate_today_card_image(
            chat_id=f"check_{mode_tag}",
            day=day,
            week_id=week_id,
            week_color_hex="#6B7FA6",
            mode_tag=mode_tag,
            accent_hex="#D96A4A",
        )
        if not os.path.exists(path):
            problems.append(f"{mode_tag}: image not created")
            continue
        mtime1 = os.path.getmtime(path)
        with Image.open(path) as img:
            if img.size != (1080, 1080):
                problems.append(f"{mode_tag}: invalid image size {img.size}")
            if img.format != "PNG":
                problems.append(f"{mode_tag}: invalid image format {img.format}")

        path2 = generate_today_card_image(
            chat_id=f"check_{mode_tag}",
            day=day,
            week_id=week_id,
            week_color_hex="#6B7FA6",
            mode_tag=mode_tag,
            accent_hex="#D96A4A",
        )
        mtime2 = os.path.getmtime(path2)
        if path != path2 or mtime1 != mtime2:
            problems.append(f"{mode_tag}: cache miss on second generation")
    return problems


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
        print("Pillow not installed in this environment; skipping image generation self-check.")
        return []
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
