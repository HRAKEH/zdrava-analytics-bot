"""Validated config, exclusively from environment variables."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo
import os
import re


def owners(raw: str) -> tuple[int, ...]:
    values: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if token:
            if not token.isdecimal() or int(token) < 1:
                raise ValueError("TELEGRAM_OWNER_IDS must contain positive numeric IDs")
            values.add(int(token))
    return tuple(sorted(values))


def optional_goal(raw: str) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    if not raw.isdecimal() or int(raw) <= 0:
        raise ValueError("METRIKA_OZON_GOAL_ID must be a positive integer")
    return int(raw)


def flag(raw: str) -> bool:
    return raw.strip().lower() in {"1", "yes", "true", "on", "да"}


@dataclass(frozen=True)
class Config:
    telegram_token: str = field(repr=False)
    owner_ids: tuple[int, ...]
    metrika_counter_id: int
    metrika_token: str = field(repr=False)
    metrika_ozon_goal_id: int | None
    gsc_property: str
    gsc_service_account_file: Path
    gsc_service_account_b64: str = field(repr=False)
    data_dir: Path
    timezone: str
    daily_report_time: str
    daily_report_enabled: bool
    http_timeout: float

    @classmethod
    def from_env(cls) -> "Config":
        counter_raw = os.getenv("METRIKA_COUNTER_ID", "113941063")
        if not counter_raw.isdecimal() or int(counter_raw) <= 0:
            raise ValueError("METRIKA_COUNTER_ID must be a positive integer")
        timezone = os.getenv("TIMEZONE", "Europe/Moscow")
        ZoneInfo(timezone)  # Fail early if invalid.
        report_time = os.getenv("DAILY_REPORT_TIME", "09:00")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", report_time):
            raise ValueError("DAILY_REPORT_TIME must be HH:MM (24 hour)")
        timeout = float(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))
        if not (5 <= timeout <= 120):
            raise ValueError("HTTP_TIMEOUT_SECONDS must be between 5 and 120")
        return cls(
            telegram_token=(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")).strip(),
            owner_ids=owners(os.getenv("TELEGRAM_OWNER_IDS", os.getenv("TELEGRAM_OWNER_ID", ""))),
            metrika_counter_id=int(counter_raw),
            metrika_token=os.getenv("METRIKA_TOKEN", "").strip(),
            metrika_ozon_goal_id=optional_goal(os.getenv("METRIKA_OZON_GOAL_ID", "")),
            gsc_property=os.getenv("GSC_PROPERTY", "sc-domain:zdrava.top").strip(),
            gsc_service_account_file=Path(os.getenv("GSC_SERVICE_ACCOUNT_FILE", "/app/data/gsc-service-account.json")),
            gsc_service_account_b64=os.getenv("GSC_SERVICE_ACCOUNT_B64", "").strip(),
            data_dir=Path(os.getenv("DATA_DIR", "/app/data")),
            timezone=timezone,
            daily_report_time=report_time,
            daily_report_enabled=flag(os.getenv("DAILY_REPORT_ENABLED", "true")),
            http_timeout=timeout,
        )

    def validate_startup(self) -> None:
        if not self.telegram_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not self.owner_ids:
            raise ValueError("TELEGRAM_OWNER_IDS is required (see /id)")
