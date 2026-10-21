"""Settings, read from the environment once at start-up. A missing or wrong required setting stops the app at once."""
import os
from dataclasses import dataclass

MODES = ("live", "mock")


@dataclass(frozen=True)
class Settings:
    mode: str
    default_city: str
    version: str
    timeout_s: float


def load() -> Settings:
    mode = os.getenv("WEATHER_MODE", "")
    if mode not in MODES:
        raise RuntimeError(f"WEATHER_MODE must be one of {', '.join(MODES)} (got {mode!r})")
    return Settings(mode=mode, default_city=os.getenv("DEFAULT_CITY", "Hyderabad"), version=os.getenv("APP_VERSION", "dev"),
                    timeout_s=float(os.getenv("WEATHER_TIMEOUT_S", "5")))
