import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "sqlite:///./morti_news_digest.db"
    )
    fuzzy_duplicate_threshold: int = int(
        os.getenv("FUZZY_DUPLICATE_THRESHOLD", "66")
    )
    fuzzy_lookback_hours: int = int(os.getenv("FUZZY_LOOKBACK_HOURS", "72"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
