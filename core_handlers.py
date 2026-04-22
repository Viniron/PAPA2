from __future__ import annotations

from telebot import types

from access import get_registered_user
from app_context import BotApp
from bot_utils import main_menu_text


def register_handlers(app: BotApp) -> None:
    bot = app.bot

    @bot.message_handler(commands=["start"])
    def start_command(message: types.Message) -> None:
        user = get_registered_user(app, message.from_user.id)
        bot.send_message(message.chat.id, main_menu_text(user))

    @bot.callback_query_handler(func=lambda call: call.data == "noop")
    def noop_callback(call: types.CallbackQuery) -> None:
        bot.answer_callback_query(call.id)
