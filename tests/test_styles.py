import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurelius import VERDICT_NEGATIVE, build_final_reply
from main import _resolve_style_command


def test_otvechai_derzko_sets_bold_mode():
    out = _resolve_style_command("отвечай дерзко")
    assert out and out["style"] == "bold"


def test_rezhim_obychny_sets_default_mode():
    out = _resolve_style_command("режим обычный")
    assert out and out["style"] == "default"


def test_rezhim_stoik_sets_stoic_mode():
    out = _resolve_style_command("режим стоик")
    assert out and out["style"] == "stoic"


def test_custom_style_mode_detected():
    out = _resolve_style_command("отвечай как суровый сержант")
    assert out and out["style"] == "custom"


def test_verdict_line_unchanged_for_all_styles():
    for style in ("default", "bold", "stoic", "custom"):
        reply = build_final_reply("negative", "Слабая мысль.", style=style, custom_style="суровый сержант")
        assert reply.splitlines()[0] == VERDICT_NEGATIVE
