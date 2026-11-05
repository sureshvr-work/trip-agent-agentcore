"""
config.py — every secret and tunable in one place, read from the environment.

Keys NEVER live in code or in the browser. They come from .env (local) or real
env vars (prod). This module is the only thing that touches os.environ.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # load .env in development; no-op if the file isn't there


class Config:
    # ── LLM engines (the REASON step) ──
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # ── live tools ──
    GEOAPIFY_KEY = os.getenv("GEOAPIFY_KEY", "")        # geocode + places
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")    # web search
    AVIATIONSTACK_KEY = os.getenv("AVIATIONSTACK_KEY", "")  # flights
    # Open-Meteo (weather) needs no key.

    # ── plan-ready notifications (fire once, after the final plan) ──
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    NOTIFY_FROM = os.getenv("NOTIFY_FROM", "onboarding@resend.dev")
    NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO", "")        # fallback if the UI field is left blank

    # ── resilience / limits ──
    HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12"))   # seconds per call
    HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "2"))      # retries on failure/429
    MAX_TURNS = int(os.getenv("MAX_TURNS", "10"))           # loop safety cap


cfg = Config()
