import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurelius import (
    VERDICT_NEGATIVE,
    VERDICT_POSITIVE,
    analyze_idea_fallback,
    build_final_reply,
    resolve_verdict_and_explanation,
)


def _header(reply: str) -> str:
    return reply.splitlines()[0]


def test_build_final_reply_positive_header():
    reply = build_final_reply("positive", "Полезная мысль.")
    assert _header(reply) == VERDICT_POSITIVE


def test_build_final_reply_negative_header():
    reply = build_final_reply("negative", "Слабая мысль.")
    assert _header(reply) == VERDICT_NEGATIVE


def test_style_cannot_change_verdict_line():
    for style in ("default", "bold", "stoic", "custom"):
        reply = build_final_reply("negative", "Слабая мысль.", style=style, custom_style="суровый сержант")
        assert _header(reply) == VERDICT_NEGATIVE


def test_empty_explanation_fallback_used():
    reply = build_final_reply("positive", "   ")
    assert "В этом есть движение и польза" in reply


def test_llm_garbage_uses_deterministic_fallback():
    verdict, explanation = resolve_verdict_and_explanation("есть хлеб", "NONSENSE")
    assert verdict in {"positive", "negative"}
    assert explanation


def test_analyze_est_hleb_returns_valid_final_answer():
    verdict, explanation = analyze_idea_fallback("есть хлеб")
    reply = build_final_reply(verdict, explanation)
    assert reply
    assert _header(reply) in {VERDICT_POSITIVE, VERDICT_NEGATIVE}


def test_analyze_skating_returns_valid_final_answer():
    verdict, explanation = analyze_idea_fallback("кататься на коньках сегодня")
    reply = build_final_reply(verdict, explanation)
    assert reply
    assert _header(reply) in {VERDICT_POSITIVE, VERDICT_NEGATIVE}
