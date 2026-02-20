"""Prompt config: system prompt from env; codex rules for Daily-Insight (no secrets)."""
import os

# Prompts are needed only for PUSH (Gemini generation).
# SYNC should work even if prompt is not present.
SYSTEM_PROMPT = os.getenv("GEMINI_SYSTEM_PROMPT", "").strip()

# Optional: keep compatibility if main.py imports CODEX_RULES
CODEX_RULES = os.getenv("CODEX_RULES", "").strip()
