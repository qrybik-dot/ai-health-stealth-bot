import re
from typing import Optional, Tuple

VERDICT_POSITIVE = "Марк Аврелий доволен тобой."
VERDICT_NEGATIVE = "Марк Аврелий недоволен тобой."

FALLBACK_EXPLANATIONS = {
    "positive": "В этом есть движение и польза. Лучше действовать осмысленно, чем расплываться в пустых желаниях.",
    "negative": "Это звучит слабо и без внутреннего стержня. Выбери не самое простое, а самое достойное.",
}

BAD_PATTERNS = (
    "разум спотыкается",
    "lorem ipsum",
    "as an ai",
    "я не могу",
)

POSITIVE_HINTS = {
    "спорт", "работ", "уч", "разв", "прогул", "читать", "сон", "режим", "дисциплин", "умерен", "решил", "сделаю", "трен", "проект", "помочь",
}
NEGATIVE_HINTS = {
    "лен", "вред", "униж", "агресс", "бездель", "похер", "забью", "пить", "оскорб", "тупо", "ничего", "насили", "сорвать", "прокраст",
}


def _cleanup_explanation(explanation: str) -> str:
    text = re.sub(r"\s+", " ", (explanation or "").strip())
    return text[:500].strip()


def _is_garbage_text(text: str) -> bool:
    if not text or len(text.strip()) < 6:
        return True
    lowered = text.lower()
    if any(p in lowered for p in BAD_PATTERNS):
        return True
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    if latin_chars > 0 and latin_chars / max(len(text), 1) > 0.25:
        return True
    return False


def apply_style(explanation: str, style: str = "default", custom_style: Optional[str] = None) -> str:
    base = _cleanup_explanation(explanation)
    if not base:
        return base
    s = (style or "default").strip().lower()
    if s == "bold":
        return f"Коротко: {base} Хватит тянуть — действуй по делу."
    if s == "stoic":
        return f"{base} Держи курс и убери лишнее."
    if s == "custom" and custom_style:
        return f"{base} Тон: {custom_style.strip()[:80]}."
    return base


def build_final_reply(verdict: str, explanation: str, style: str = "default", custom_style: Optional[str] = None) -> str:
    normalized = (verdict or "").strip().lower()
    if normalized not in {"positive", "negative"}:
        normalized = "negative"
    clean_explanation = _cleanup_explanation(explanation)
    if _is_garbage_text(clean_explanation):
        clean_explanation = FALLBACK_EXPLANATIONS[normalized]
    styled = apply_style(clean_explanation, style=style, custom_style=custom_style)
    header = VERDICT_POSITIVE if normalized == "positive" else VERDICT_NEGATIVE
    return f"{header}\n\n{styled}"


def parse_llm_reply(raw: str) -> Optional[Tuple[str, str]]:
    if not raw:
        return None
    verdict_match = re.search(r"VERDICT\s*:\s*(positive|negative)", raw, flags=re.IGNORECASE)
    explanation_match = re.search(r"EXPLANATION\s*:\s*(.+)", raw, flags=re.IGNORECASE | re.DOTALL)
    if not verdict_match or not explanation_match:
        return None
    verdict = verdict_match.group(1).lower().strip()
    explanation = _cleanup_explanation(explanation_match.group(1))
    if _is_garbage_text(explanation):
        return None
    return verdict, explanation


def analyze_idea_fallback(user_text: str) -> Tuple[str, str]:
    text = (user_text or "").strip().lower()
    if len(text) < 8:
        return "negative", "Мысль слишком пустая и сырая. Сформулируй её ясно, если хочешь честный суд."

    pos = sum(1 for token in POSITIVE_HINTS if token in text)
    neg = sum(1 for token in NEGATIVE_HINTS if token in text)

    if neg > pos:
        return "negative", "Это звучит слабо и без внутреннего стержня. Выбери не самое простое, а самое достойное."
    if pos > neg:
        return "positive", "В этом есть движение и польза. Лучше действовать осмысленно, чем расплываться в пустых желаниях."

    if len(text.split()) <= 2:
        return "negative", "Мысль слишком пустая и сырая. Сформулируй её ясно, если хочешь честный суд."
    return "negative", "Формулировка пока нейтральная и вялая. Добавь ясную цель и конкретный шаг, тогда будет сильнее."


def resolve_verdict_and_explanation(user_text: str, llm_raw: str) -> Tuple[str, str]:
    parsed = parse_llm_reply(llm_raw)
    if parsed is None:
        return analyze_idea_fallback(user_text)
    return parsed
