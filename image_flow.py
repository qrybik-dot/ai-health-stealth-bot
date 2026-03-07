import re
from typing import Callable, Dict, Optional
from urllib.parse import quote

PROMPT_MAP: Dict[str, str] = {
    "кофе": "a cup of coffee",
    "кофейня": "cozy coffee shop interior",
    "хлеб": "fresh bread loaf",
    "солнце": "bright sun in the sky",
    "круг": "simple geometric circle on clean background",
}


def is_image_request(text: str) -> bool:
    q = (text or "").strip().lower()
    return q.startswith("нарисуй")


def extract_image_prompt(text: str) -> str:
    q = (text or "").strip()
    return re.sub(r"^нарисуй\s*", "", q, flags=re.IGNORECASE).strip()


def normalize_image_prompt(prompt_raw: str) -> str:
    raw = (prompt_raw or "").strip().lower()
    if not raw:
        return ""
    mapped = raw
    for ru, en in PROMPT_MAP.items():
        if ru in raw:
            mapped = en
            break
    return f"{mapped}, highly detailed, clean composition"


def build_image_url_primary(prompt: str) -> str:
    return f"https://image.pollinations.ai/prompt/{quote(prompt)}?nologo=true&width=1024&height=1024"


def build_image_url_fallback(prompt: str) -> str:
    return f"https://image.pollinations.ai/prompt/{quote(prompt)}?model=flux&nologo=true&width=1024&height=1024"


def send_generated_image(
    *,
    prompt_raw: str,
    logger,
    send_photo: Callable[[str], None],
    send_text: Callable[[str], None],
) -> bool:
    logger.info("Image request received: %s", prompt_raw)
    normalized = normalize_image_prompt(prompt_raw)
    logger.info("Image prompt normalized: %s", normalized)
    if not normalized:
        send_text("Нужна идея для изображения после слова «нарисуй».")
        return True

    primary_url = build_image_url_primary(normalized)
    logger.info("Image backend primary url: %s", primary_url)
    try:
        send_photo(primary_url)
        return True
    except Exception:
        logger.warning("Primary image backend failed, trying fallback")

    fallback_url = build_image_url_fallback(normalized)
    logger.info("Image backend fallback url: %s", fallback_url)
    try:
        send_photo(fallback_url)
        return True
    except Exception:
        logger.exception("Image sending failed")
        send_text("Генерация изображения сейчас недоступна. Текстовый суд работает, а визуальный — нет.")
        return True
