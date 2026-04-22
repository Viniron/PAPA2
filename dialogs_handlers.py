from __future__ import annotations

from telebot import types

from access import ensure_callback_approved_user, require_approved_user
from app_context import BotApp
from bot_utils import add_pagination_buttons, clamp_page, offset_for_page, remove_message, send_screen, short_preview
from database import format_role
from messaging_handlers import prompt_for_message, show_message


def render_dialogs_page(
    app: BotApp,
    chat_id: int,
    telegram_id: int,
    page: int = 0,
    call: types.CallbackQuery | None = None,
) -> None:
    total = app.db.count_dialogs_for_user(telegram_id)
    page = clamp_page(app, page, total)
    dialogs = app.db.list_dialogs_for_user(
        telegram_id,
        limit=app.page_size,
        offset=offset_for_page(app, page),
    )

    if not dialogs:
        send_screen(app, chat_id, "У вас пока нет диалогов.", None, call)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in dialogs:
        unread_prefix = f"●{item['unread_count']} " if item["unread_count"] else ""
        preview = short_preview(item["last_preview_text"], item["last_content_type"])
        label = f"{unread_prefix}{item['partner_name']} | {preview}"
        markup.add(
            types.InlineKeyboardButton(
                label,
                callback_data=f"dialog_open:{item['partner_user_id']}:0:{page}",
            )
        )

    add_pagination_buttons(
        app,
        markup,
        page,
        total,
        f"dialogs_page:{page - 1}",
        f"dialogs_page:{page + 1}",
    )
    send_screen(app, chat_id, "Ваши диалоги.", markup, call)


def render_dialog_page(
    app: BotApp,
    chat_id: int,
    telegram_id: int,
    partner_user_id: int,
    page: int = 0,
    list_page: int = 0,
    call: types.CallbackQuery | None = None,
) -> None:
    partner = app.db.get_dialog_partner(telegram_id, partner_user_id)
    if not partner:
        send_screen(app, chat_id, "Диалог недоступен.", None, call)
        return

    total = app.db.count_dialog_messages(telegram_id, partner_user_id)
    page = clamp_page(app, page, total)
    messages = app.db.list_dialog_messages(
        telegram_id,
        partner_user_id,
        limit=app.page_size,
        offset=offset_for_page(app, page),
    )

    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in messages:
        is_mine = item["sender_telegram_id"] == telegram_id
        prefix = "Вы" if is_mine else partner["full_name"]
        unread_prefix = "● " if item["recipient_telegram_id"] == telegram_id and not item["is_read"] else ""
        preview = short_preview(item["preview_text"], item["content_type"])
        label = f"{unread_prefix}{prefix} | {preview}"
        markup.add(
            types.InlineKeyboardButton(
                label,
                callback_data=f"dialog_msg:{item['id']}:{partner_user_id}:{page}:{list_page}",
            )
        )

    add_pagination_buttons(
        app,
        markup,
        page,
        total,
        f"dialog_open:{partner_user_id}:{page - 1}:{list_page}",
        f"dialog_open:{partner_user_id}:{page + 1}:{list_page}",
    )
    markup.row(
        types.InlineKeyboardButton("Ответить", callback_data=f"dialog_reply:{partner_user_id}:{page}:{list_page}"),
        types.InlineKeyboardButton("К диалогам", callback_data=f"dialogs_page:{list_page}"),
    )

    text = f"Диалог с <b>{partner['full_name']}</b> ({format_role(partner)})."
    send_screen(app, chat_id, text, markup, call)


def register_handlers(app: BotApp) -> None:
    bot = app.bot

    @bot.message_handler(commands=["dialogs"])
    def dialogs_command(message: types.Message) -> None:
        user = require_approved_user(app, message)
        if not user:
            return
        render_dialogs_page(app, message.chat.id, message.from_user.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("dialogs_page:"))
    def on_dialogs_page(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        page = int(call.data.split(":")[1])
        render_dialogs_page(app, call.message.chat.id, call.from_user.id, page=page, call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("dialog_open:"))
    def on_dialog_open(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        _, partner_id_text, page_text, list_page_text = call.data.split(":")
        render_dialog_page(
            app,
            call.message.chat.id,
            call.from_user.id,
            int(partner_id_text),
            page=int(page_text),
            list_page=int(list_page_text),
            call=call,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("dialog_reply:"))
    def on_dialog_reply(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        payload = call.data.split(":")
        partner_id_text = payload[1]
        if len(payload) == 4:
            page_text = payload[2]
            list_page_text = payload[3]
        else:
            page_text = "0"
            list_page_text = payload[2]
        prompt_for_message(
            app,
            call.message.chat.id,
            recipient_user_id=int(partner_id_text),
            call=call,
            back_callback=f"dialog_open:{partner_id_text}:{page_text}:{list_page_text}",
            delete_current_message=False,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("dialog_msg:"))
    def on_dialog_message(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        _, message_id_text, partner_id_text, page_text, list_page_text = call.data.split(":")
        remove_message(app, call.message.chat.id, call.message.message_id)
        ok, text = show_message(
            app,
            call.message.chat.id,
            call.from_user.id,
            int(message_id_text),
            back_callback=f"dialog_open:{partner_id_text}:{page_text}:{list_page_text}",
        )
        bot.answer_callback_query(call.id, text, show_alert=not ok)
