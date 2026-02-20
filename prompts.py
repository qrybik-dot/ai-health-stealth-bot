"""Prompt config: system prompt from env; codex rules for Daily-Insight (no secrets)."""
import os

# Loaded from GitHub Secret GEMINI_SYSTEM_PROMPT. Never hardcode the full prompt.
SYSTEM_PROMPT = os.getenv("GEMINI_SYSTEM_PROMPT", "").strip()
if not SYSTEM_PROMPT:
    raise RuntimeError("Missing GEMINI_SYSTEM_PROMPT (set it in GitHub Secrets or .env)")

# Codex: applied in user prompt so output is consistent. Not secret.
CODEX_RULES = """
- Never shame. Light teasing allowed.
- No medical diagnoses, no dosages for supplements.
- No motivational-instagram tone.
- No repeated advice unless data/context changed.
- If data incomplete/uncertain: lower confidence and state uncertainty.
- Output short, clear, not overloaded.
- v2 visuals: emoji matrix (3 lines only), max 3 reasons, behavior frame with 🟢🟡🔴, one-line cost-of-ignoring, confidence marker, one human anchor line.
"""
