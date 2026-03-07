import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_flow import (
    build_image_url_fallback,
    build_image_url_primary,
    extract_image_prompt,
    is_image_request,
    send_generated_image,
)


class DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


def test_narisuy_kofe_goes_to_image_flow():
    assert is_image_request("нарисуй кофе")


def test_narisuy_krug_goes_to_image_flow():
    assert is_image_request("нарисуй круг")


def test_narisuy_empty_has_clear_error():
    sent = {"text": None}

    send_generated_image(
        prompt_raw=extract_image_prompt("нарисуй"),
        logger=DummyLogger(),
        send_photo=lambda _url: None,
        send_text=lambda txt: sent.__setitem__("text", txt),
    )
    assert "Нужна идея для изображения" in sent["text"]


def test_primary_image_url_builds_correctly():
    url = build_image_url_primary("a cup of coffee")
    assert url.startswith("https://image.pollinations.ai/prompt/")
    assert "nologo=true" in url


def test_fallback_image_url_builds_correctly():
    url = build_image_url_fallback("a cup of coffee")
    assert url.startswith("https://image.pollinations.ai/prompt/")
    assert "model=flux" in url


def test_primary_fail_calls_fallback():
    calls = []

    def send_photo(url: str):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("primary fail")

    send_generated_image(
        prompt_raw="кофе",
        logger=DummyLogger(),
        send_photo=send_photo,
        send_text=lambda _txt: None,
    )
    assert len(calls) == 2


def test_both_fail_returns_honest_text():
    sent = {"text": None}

    def send_photo(_url: str):
        raise RuntimeError("fail")

    send_generated_image(
        prompt_raw="кофе",
        logger=DummyLogger(),
        send_photo=send_photo,
        send_text=lambda txt: sent.__setitem__("text", txt),
    )
    assert sent["text"] == "Генерация изображения сейчас недоступна. Текстовый суд работает, а визуальный — нет."


def test_est_hleb_not_image_flow():
    assert not is_image_request("есть хлеб")
