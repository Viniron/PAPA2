from __future__ import annotations

from telebot import types

from access import ensure_callback_approved_user, require_approved_user
from app_context import BotApp
from bot_utils import (
    add_pagination_buttons,
    clamp_page,
    clear_chat_step_handler,
    format_datetime,
    log_safe_content,
    message_preview,
    offset_for_page,
    prompt_markup,
    recipient_list_title,
    remove_message,
    send_root_markup,
    send_screen,
    short_preview,
)
from bot_utils import grade_markup, parallel_markup
from database import ROLE_STUDENT, STATUS_APPROVED, format_role


def render_send_root(app: BotApp, chat_id: int, call: types.CallbackQuery | None = None) -> None:
    send_screen(app, chat_id, "Кому вы хотите написать?", send_root_markup(), call)


def render_recipients_page(
    app: BotApp,
    chat_id: int,
    sender_telegram_id: int,
    role: str,
    page: int = 0,
    class_number: int | None = None,
    parallel: str | None = None,
    call: types.CallbackQuery | None = None,
) -> None:
    total = app.db.count_recipients_by_role(
        role,
        class_number=class_number,
        parallel=parallel,
        exclude_telegram_id=sender_telegram_id,
    )
    page = clamp_page(app, page, total)
    recipients = app.db.list_recipients_by_role(
        role,
        class_number=class_number,
        parallel=parallel,
        exclude_telegram_id=sender_telegram_id,
        limit=app.page_size,
        offset=offset_for_page(app, page),
    )

    if not recipients:
        send_screen(app, chat_id, "Пока нет доступных получателей в этой категории.", None, call)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for user in recipients:
        label = f"{user['full_name']} | {format_role(user)}"
        callback_data = (
            f"pick_user:{user['id']}:{role}:{class_number if class_number is not None else '-'}:"
            f"{parallel if parallel is not None else '-'}:{page}"
        )
        markup.add(types.InlineKeyboardButton(label, callback_data=callback_data))

    if role == ROLE_STUDENT and class_number is not None and parallel is not None:
        back_callback = f"send_grade:{class_number}"
        prev_callback = f"send_page:{role}:{class_number}:{parallel}:{page - 1}"
        next_callback = f"send_page:{role}:{class_number}:{parallel}:{page + 1}"
    else:
        back_callback = "send_root"
        prev_callback = f"send_page:{role}:-:-:{page - 1}"
        next_callback = f"send_page:{role}:-:-:{page + 1}"

    add_pagination_buttons(app, markup, page, total, prev_callback, next_callback)
    markup.row(types.InlineKeyboardButton("Назад", callback_data=back_callback))
    text = recipient_list_title(role, class_number=class_number, parallel=parallel)
    send_screen(app, chat_id, text, markup, call)


def render_notifications_page(
    app: BotApp,
    chat_id: int,
    telegram_id: int,
    page: int = 0,
    call: types.CallbackQuery | None = None,
) -> None:
    total = app.db.count_messages_for_user(telegram_id)
    page = clamp_page(app, page, total)
    messages = app.db.list_messages_for_user(
        telegram_id,
        limit=app.page_size,
        offset=offset_for_page(app, page),
    )

    if not messages:
        send_screen(app, chat_id, "У вас пока нет уведомлений.", None, call)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in messages:
        unread_prefix = "● " if not item["is_read"] else ""
        preview = short_preview(item["preview_text"], item["content_type"])
        label = f"{unread_prefix}{item['sender_name']} | {preview}"
        markup.add(
            types.InlineKeyboardButton(
                label,
                callback_data=f"read_msg:{item['id']}:{page}",
            )
        )

    add_pagination_buttons(
        app,
        markup,
        page,
        total,
        f"read_page:{page - 1}",
        f"read_page:{page + 1}",
    )
    send_screen(app, chat_id, "Входящие уведомления. Непрочитанные отмечены точкой.", markup, call)


def send_delivery_notification(
    app: BotApp,
    message_id: int,
    sender: dict,
    recipient: dict,
    reply_to_message_id: int | None,
) -> bool:
    entry = app.db.get_message(message_id)
    if not entry:
        return False

    title = "Новый ответ" if reply_to_message_id else "Новое сообщение"
    header = f"{title} от <b>{sender['full_name']}</b>"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("Открыть все уведомления", callback_data="read_page:0"))
    markup.row(
        types.InlineKeyboardButton("Диалог", callback_data=f"dialog_open:{sender['id']}:0:0"),
        types.InlineKeyboardButton("Ответить", callback_data=f"reply_msg:{message_id}"),
    )

    if entry["content_type"] == "text":
        body = header
        if entry["preview_text"]:
            body += f"\n\n{entry['preview_text']}"
        try:
            app.bot.send_message(recipient["telegram_id"], body, reply_markup=markup)
            return True
        except Exception:
            return False

    caption = header
    if entry["reply_to_message_id"]:
        reply_preview = short_preview(entry["reply_preview_text"], "text")
        reply_author = entry["reply_sender_name"] or "неизвестно"
        caption += f"\nОтвет на сообщение от <b>{reply_author}</b>: {reply_preview}"
    if entry["preview_text"]:
        caption += f"\n\n{entry['preview_text']}"

    try:
        app.bot.copy_message(
            chat_id=recipient["telegram_id"],
            from_chat_id=entry["source_chat_id"],
            message_id=entry["source_message_id"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=markup,
        )
        return True
    except Exception:
        return False


def handle_compose_message(
    app: BotApp,
    message: types.Message,
    recipient_user_id: int,
    reply_to_message_id: int | None = None,
) -> None:
    sender = require_approved_user(app, message)
    if not sender:
        return

    recipient = app.db.get_user_by_id(recipient_user_id)
    if not recipient or recipient["status"] != STATUS_APPROVED:
        app.bot.reply_to(message, "Этот пользователь сейчас недоступен для переписки.")
        return

    if message.content_type not in {"text", "photo", "video", "document"}:
        app.bot.reply_to(
            message,
            "Поддерживаются только текст, фото, видео и файлы. Запустите действие еще раз.",
        )
        return

    preview_text = message_preview(message)
    message_id = app.db.create_message(
        sender_user_id=sender["id"],
        recipient_user_id=recipient["id"],
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
        content_type=message.content_type,
        preview_text=preview_text,
        reply_to_message_id=reply_to_message_id,
    )
    delivered = send_delivery_notification(
        app=app,
        message_id=message_id,
        sender=sender,
        recipient=recipient,
        reply_to_message_id=reply_to_message_id,
    )
    app.activity_logger.log(
        "message_sent",
        from_user=sender["full_name"],
        from_role=format_role(sender),
        to_user=recipient["full_name"],
        to_role=format_role(recipient),
        content=log_safe_content(message.content_type, preview_text),
        reply_to=reply_to_message_id,
    )

    if delivered:
        app.bot.reply_to(message, f"Сообщение отправлено пользователю <b>{recipient['full_name']}</b>.")
        return

    app.bot.reply_to(
        message,
        f"Сообщение сохранено для <b>{recipient['full_name']}</b>, но уведомление доставить не удалось.",
    )


def prompt_for_message(
    app: BotApp,
    chat_id: int,
    recipient_user_id: int,
    reply_to_message_id: int | None = None,
    prompt_text: str | None = None,
    call: types.CallbackQuery | None = None,
    back_callback: str | None = None,
    delete_current_message: bool = True,
) -> None:
    recipient = app.db.get_user_by_id(recipient_user_id)
    if not recipient or recipient["status"] != STATUS_APPROVED:
        if call:
            app.bot.answer_callback_query(call.id, "Пользователь недоступен.", show_alert=True)
        return

    text = prompt_text or (
        f"Напишите сообщение для <b>{recipient['full_name']}</b>.\n"
        "Можно отправить текст, фото, видео или файл."
    )
    clear_chat_step_handler(app, chat_id)
    if call is not None and delete_current_message:
        remove_message(app, call.message.chat.id, call.message.message_id)
    prompt = app.bot.send_message(chat_id, text, reply_markup=prompt_markup(back_callback))
    app.bot.register_next_step_handler(
        prompt,
        lambda message: handle_compose_message(app, message, recipient_user_id, reply_to_message_id),
    )


def show_message(
    app: BotApp,
    chat_id: int,
    viewer_telegram_id: int,
    message_id: int,
    back_callback: str | None = None,
) -> tuple[bool, str]:
    entry = app.db.get_message_for_user(message_id, viewer_telegram_id)
    if not entry:
        return False, "Сообщение не найдено."

    incoming = entry["recipient_telegram_id"] == viewer_telegram_id
    counterpart_name = entry["sender_name"] if incoming else entry["recipient_name"]
    counterpart_user_id = entry["sender_user_id"] if incoming else entry["recipient_user_id"]
    direction = "От" if incoming else "Кому"

    lines = [
        f"{direction}: <b>{counterpart_name}</b>",
        f"Дата: <b>{format_datetime(entry['created_at'])}</b>",
    ]
    if entry["reply_to_message_id"]:
        reply_preview = short_preview(entry["reply_preview_text"], "text")
        reply_author = entry["reply_sender_name"] or "неизвестно"
        lines.append(f"Ответ на сообщение от <b>{reply_author}</b>: {reply_preview}")

    markup = types.InlineKeyboardMarkup(row_width=2)
    partner = app.db.get_user_by_id(counterpart_user_id)
    if partner and partner["status"] == STATUS_APPROVED:
        markup.row(
            types.InlineKeyboardButton("Ответить", callback_data=f"reply_msg:{message_id}"),
            types.InlineKeyboardButton("Диалог", callback_data=f"dialog_open:{counterpart_user_id}:0:0"),
        )
    if back_callback:
        markup.row(types.InlineKeyboardButton("Назад", callback_data=back_callback))

    try:
        app.bot.send_message(chat_id, "\n".join(lines), reply_markup=markup)
        app.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=entry["source_chat_id"],
            message_id=entry["source_message_id"],
        )
    except Exception:
        return False, "Не удалось открыть сообщение."

    if incoming and not entry["is_read"]:
        app.db.mark_message_as_read(message_id)
    return True, "Сообщение открыто."


def prepare_reply_to_message(app: BotApp, call: types.CallbackQuery, message_id: int) -> None:
    user = ensure_callback_approved_user(app, call)
    if not user:
        return

    entry = app.db.get_message_for_user(message_id, call.from_user.id)
    if not entry:
        app.bot.answer_callback_query(call.id, "Сообщение не найдено.", show_alert=True)
        return

    recipient_user_id = (
        entry["sender_user_id"]
        if entry["recipient_telegram_id"] == call.from_user.id
        else entry["recipient_user_id"]
    )
    back_callback = f"dialog_open:{recipient_user_id}:0:0"
    preview = short_preview(entry["preview_text"], entry["content_type"])
    prompt_for_message(
        app,
        call.message.chat.id,
        recipient_user_id=recipient_user_id,
        reply_to_message_id=message_id,
        prompt_text=(
            "Напишите ответ.\n"
            f"Основа ответа: <b>{preview}</b>\n"
            "Можно отправить текст, фото, видео или файл."
        ),
        call=call,
        back_callback=back_callback,
        delete_current_message=False,
    )
    app.bot.answer_callback_query(call.id)


def register_handlers(app: BotApp) -> None:
    bot = app.bot

    @bot.message_handler(commands=["send"])
    def send_command(message: types.Message) -> None:
        user = require_approved_user(app, message)
        if not user:
            return
        render_send_root(app, message.chat.id)

    @bot.message_handler(commands=["read"])
    def read_command(message: types.Message) -> None:
        user = require_approved_user(app, message)
        if not user:
            return
        render_notifications_page(app, message.chat.id, message.from_user.id)

    @bot.callback_query_handler(func=lambda call: call.data == "send_root")
    def send_root_callback(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        render_send_root(app, call.message.chat.id, call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("send_nav:"))
    def send_navigation_callback(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        target = call.data.split(":")[1]
        if target == "grade":
            send_screen(
                app,
                call.message.chat.id,
                "Выберите номер класса.",
                grade_markup("send_grade", back_callback="send_root"),
                call,
            )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("send_role:"))
    def on_send_role(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return

        role = call.data.split(":")[1]
        if role == ROLE_STUDENT:
            send_screen(
                app,
                call.message.chat.id,
                "Выберите номер класса.",
                grade_markup("send_grade", back_callback="send_root"),
                call,
            )
            bot.answer_callback_query(call.id)
            return

        render_recipients_page(app, call.message.chat.id, call.from_user.id, role, call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("send_grade:"))
    def on_send_grade(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        grade = int(call.data.split(":")[1])
        send_screen(
            app,
            call.message.chat.id,
            "Выберите параллель.",
            parallel_markup("send_parallel", grade, back_callback="send_nav:grade"),
            call,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("send_parallel:"))
    def on_send_parallel(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        _, grade_text, parallel = call.data.split(":")
        render_recipients_page(
            app,
            call.message.chat.id,
            call.from_user.id,
            ROLE_STUDENT,
            class_number=int(grade_text),
            parallel=parallel,
            call=call,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("send_page:"))
    def on_send_page(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        _, role, grade_text, parallel_text, page_text = call.data.split(":")
        grade = None if grade_text == "-" else int(grade_text)
        parallel = None if parallel_text == "-" else parallel_text
        render_recipients_page(
            app,
            call.message.chat.id,
            call.from_user.id,
            role,
            page=int(page_text),
            class_number=grade,
            parallel=parallel,
            call=call,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pick_user:"))
    def on_pick_user(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        payload = call.data.split(":")
        recipient_user_id = int(payload[1])
        back_callback = None
        if len(payload) == 6:
            _, _, role, grade_text, parallel_text, page_text = payload
            if role == ROLE_STUDENT and grade_text != "-" and parallel_text != "-":
                back_callback = f"send_page:{role}:{grade_text}:{parallel_text}:{page_text}"
            else:
                back_callback = f"send_page:{role}:-:-:{page_text}"
        prompt_for_message(
            app,
            call.message.chat.id,
            recipient_user_id=recipient_user_id,
            call=call,
            back_callback=back_callback,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("read_page:"))
    def on_read_page(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        page = int(call.data.split(":")[1])
        render_notifications_page(app, call.message.chat.id, call.from_user.id, page=page, call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("read_msg:"))
    def on_read_message(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        payload = call.data.split(":")
        message_id_text = payload[1]
        page_text = payload[2] if len(payload) > 2 else "0"
        remove_message(app, call.message.chat.id, call.message.message_id)
        ok, text = show_message(
            app,
            call.message.chat.id,
            call.from_user.id,
            int(message_id_text),
            back_callback=f"read_page:{page_text}",
        )
        bot.answer_callback_query(call.id, text, show_alert=not ok)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("open_msg:"))
    def on_open_notification_message(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        message_id = int(call.data.split(":")[1])
        ok, text = show_message(app, call.message.chat.id, call.from_user.id, message_id)
        bot.answer_callback_query(call.id, text, show_alert=not ok)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("reply_msg:"))
    def on_reply_to_message(call: types.CallbackQuery) -> None:
        message_id = int(call.data.split(":")[1])
        prepare_reply_to_message(app, call, message_id)
