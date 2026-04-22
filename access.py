from __future__ import annotations

from telebot import types

from app_context import BotApp
from database import STATUS_APPROVED, STATUS_CANCELLED, STATUS_PENDING, STATUS_REJECTED


def get_registered_user(app: BotApp, telegram_id: int) -> dict | None:
    app.db.ensure_admins(app.settings.admin_ids)
    return app.db.get_user_by_telegram_id(telegram_id)


def require_approved_user(app: BotApp, message: types.Message) -> dict | None:
    user = get_registered_user(app, message.from_user.id)
    if not user:
        app.bot.reply_to(
            message,
            "Сначала зарегистрируйтесь через /register, после этого заявка уйдет администраторам.",
        )
        return None

    if user["status"] == STATUS_PENDING:
        app.bot.reply_to(message, "Ваша заявка еще рассматривается. Как только ее одобрят, я открою доступ.")
        return None

    if user["status"] == STATUS_REJECTED:
        app.bot.reply_to(message, "Ваша заявка отклонена. При необходимости отправьте новую через /register.")
        return None

    if user["status"] == STATUS_CANCELLED:
        app.bot.reply_to(message, "Регистрация отменена. Вы можете создать новую через /register.")
        return None

    return user


def require_admin(app: BotApp, message: types.Message) -> dict | None:
    user = require_approved_user(app, message)
    if not user:
        return None
    if not user["is_admin"]:
        app.bot.reply_to(message, "Эта команда доступна только администраторам.")
        return None
    return user


def ensure_callback_approved_user(app: BotApp, call: types.CallbackQuery) -> dict | None:
    user = get_registered_user(app, call.from_user.id)
    if not user or user["status"] != STATUS_APPROVED:
        app.bot.answer_callback_query(call.id, "Сначала завершите регистрацию.", show_alert=True)
        return None
    return user


def ensure_callback_admin(app: BotApp, call: types.CallbackQuery) -> dict | None:
    user = ensure_callback_approved_user(app, call)
    if not user:
        return None
    if not user["is_admin"]:
        app.bot.answer_callback_query(call.id, "У вас нет доступа к админ-разделу.", show_alert=True)
        return None
    return user
