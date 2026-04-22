from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_path: str
    admin_ids: tuple[int, ...]
    page_size: int
    activity_log_path: str


def _parse_admin_ids(raw_value: str | None) -> tuple[int, ...]:
    if not raw_value:
        return tuple()

    result: list[int] = []
    for chunk in raw_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        result.append(int(chunk))
    return tuple(result)


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError(
            "Переменная окружения BOT_TOKEN не задана. "
            "Укажите токен Telegram-бота в .env или в системных переменных."
        )

    database_path = os.getenv("DATABASE_PATH", "school_messenger.db").strip() or "school_messenger.db"
    admin_ids = _parse_admin_ids(os.getenv("BOT_ADMIN_IDS"))
    page_size = max(1, int(os.getenv("PAGE_SIZE", "6").strip() or "6"))
    activity_log_path = os.getenv("ACTIVITY_LOG_PATH", "logs/bot_activity.log").strip() or "logs/bot_activity.log"

    return Settings(
        bot_token=bot_token,
        database_path=database_path,
        admin_ids=admin_ids,
        page_size=page_size,
        activity_log_path=activity_log_path,
    )
