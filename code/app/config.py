"""Paths and settings. Every value comes from the environment (code/.env is loaded by __main__)."""
import os
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = CODE_DIR.parent


def env(name: str, default: str = "") -> str:
    """Read a setting; an empty value in .env counts as unset."""
    value = os.environ.get(name, "").strip()
    return value or default


def data_dir() -> Path:
    return Path(env("DATA_DIR", str(REPO_DIR))).resolve()


def mock_system_dir() -> Path:
    return Path(env("MOCK_SYSTEM_DIR", str(REPO_DIR / "mock_system"))).resolve()


def out_dir() -> Path:
    return CODE_DIR / "out"


# Models. Groq is primary, Gemini is the fallback provider.
ORCHESTRATOR_MODEL = lambda: env("ORCHESTRATOR_MODEL", "openai/gpt-oss-20b")
SPECIALIST_MODEL = lambda: env("SPECIALIST_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODEL = lambda: env("FALLBACK_MODEL", "gemini-2.5-flash")

# Per-case budget. A case that hits it finishes on tool results alone.
MAX_CALLS_PER_CASE = lambda: int(env("MAX_CALLS_PER_CASE", "8"))
MAX_TOKENS_PER_CASE = lambda: int(env("MAX_TOKENS_PER_CASE", "40000"))
MAX_ATTEMPTS_PER_AGENT = 2

# Policy thresholds (AP-POL). Kept here so every check reads the same numbers.
PRICE_BAND_PCT = 2.0            # §3.3, inclusive
GST_HEADER_TOLERANCE = 1.0      # §3.5, inclusive, rupees
DUPLICATE_DATE_WINDOW_DAYS = 7  # §4.1, inclusive
BANK_CHANGE_WINDOW_DAYS = 30    # §5.1, 0..30 days before invoice date, inclusive
APPROVAL_THRESHOLD = 500000.0   # §5.1 and §8.2, strictly more than
