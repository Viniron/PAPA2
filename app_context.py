from __future__ import annotations

from dataclasses import dataclass

import telebot

from activity_logger import ActivityLogger
from config import Settings, load_settings
from database import Database


@dataclass(slots=True)
class BotApp:
    settings: Settings
    db: Database
    bot: telebot.TeleBot
    activity_logger: ActivityLogger
    page_size: int


def create_app() -> BotApp:
    settings = load_settings()
    db = Database(settings.database_path)
    db.ensure_admins(settings.admin_ids)
    bot = telebot.TeleBot(settings.bot_token, parse_mode="HTML")
    activity_logger = ActivityLogger(settings.activity_log_path)
    return BotApp(
        settings=settings,
        db=db,
        bot=bot,
        activity_logger=activity_logger,
        page_size=settings.page_size,
    )
