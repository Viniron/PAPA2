from __future__ import annotations

from datetime import timedelta

from telebot import types

from access import ensure_callback_admin, require_admin
from app_context import BotApp
from bot_utils import add_pagination_buttons, clamp_page, offset_for_page, send_screen
from database import (
    LAUNDRY_TYPE_LABELS,
    ROLE_EDUCATOR,
    ROLE_OTHER,
    ROLE_STUDENT,
    ROLE_TEACHER,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    format_role,
)
from laundry import format_booking_label, format_slot, now_moscow, parse_dt, to_iso


def admin_home_text(app: BotApp) -> str:
    stats = app.db.get_admin_statistics()
    roles = stats["roles"]
    return (
        "Админ-раздел.\n\n"
        f"Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"Одобрено: <b>{stats['approved_users']}</b>\n"
        f"На проверке: <b>{stats['pending_users']}</b>\n"
        f"Отклонено: <b>{stats['rejected_users']}</b>\n"
        f"Отменено: <b>{stats['cancelled_users']}</b>\n\n"
        f"Преподаватели: <b>{roles[ROLE_TEACHER]}</b>\n"
        f"Ученики: <b>{roles[ROLE_STUDENT]}</b>\n"
        f"Воспитатели: <b>{roles[ROLE_EDUCATOR]}</b>\n"
        f"Остальные: <b>{roles[ROLE_OTHER]}</b>"
    )


def admin_home_markup() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("Заявки на одобрение", callback_data="admin_pending:0"),
        types.InlineKeyboardButton("Все пользователи", callback_data="admin_users:0"),
        types.InlineKeyboardButton("История прачечной", callback_data="admin_laundry:0"),
    )
    return markup


def render_admin_home(app: BotApp, chat_id: int, call: types.CallbackQuery | None = None) -> None:
    send_screen(app, chat_id, admin_home_text(app), admin_home_markup(), call)


def render_admin_pending_page(
    app: BotApp,
    chat_id: int,
    page: int = 0,
    call: types.CallbackQuery | None = None,
) -> None:
    total = app.db.count_pending_users()
    page = clamp_page(app, page, total)
    users = app.db.get_pending_users(limit=app.page_size, offset=offset_for_page(app, page))

    if not users:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("В админ-раздел", callback_data="admin_home"))
        send_screen(app, chat_id, "Сейчас нет заявок на подтверждение.", markup, call)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for user in users:
        label = f"{user['full_name']} | {format_role(user)}"
        markup.add(types.InlineKeyboardButton(label, callback_data=f"admin_req:{user['id']}:{page}"))

    add_pagination_buttons(
        app,
        markup,
        page,
        total,
        f"admin_pending:{page - 1}",
        f"admin_pending:{page + 1}",
    )
    markup.row(types.InlineKeyboardButton("В админ-раздел", callback_data="admin_home"))
    send_screen(app, chat_id, "Заявки на одобрение.", markup, call)


def render_admin_request(
    app: BotApp,
    chat_id: int,
    user_id: int,
    page: int,
    call: types.CallbackQuery | None = None,
) -> None:
    user = app.db.get_user_by_id(user_id)
    if not user:
        send_screen(app, chat_id, "Заявка не найдена.", None, call)
        return

    username = f"@{user['username']}" if user["username"] else "не указан"
    text = (
        "Карточка заявки.\n\n"
        f"ФИО: <b>{user['full_name']}</b>\n"
        f"Роль: <b>{format_role(user)}</b>\n"
        f"Username: <b>{username}</b>\n"
        f"Статус: <b>{user['status']}</b>"
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("Одобрить", callback_data=f"admin_action:approve:{user_id}:{page}"),
        types.InlineKeyboardButton("Отклонить", callback_data=f"admin_action:reject:{user_id}:{page}"),
    )
    markup.row(types.InlineKeyboardButton("К заявкам", callback_data=f"admin_pending:{page}"))
    send_screen(app, chat_id, text, markup, call)


def render_admin_users_page(
    app: BotApp,
    chat_id: int,
    page: int = 0,
    call: types.CallbackQuery | None = None,
) -> None:
    total = app.db.count_users()
    page = clamp_page(app, page, total)
    users = app.db.list_users(limit=app.page_size, offset=offset_for_page(app, page))

    if not users:
        send_screen(app, chat_id, "Пользователей пока нет.", None, call)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for user in users:
        label = f"{user['full_name']} | {format_role(user)} | {user['status']}"
        markup.add(types.InlineKeyboardButton(label, callback_data=f"admin_user:{user['id']}:{page}"))

    add_pagination_buttons(
        app,
        markup,
        page,
        total,
        f"admin_users:{page - 1}",
        f"admin_users:{page + 1}",
    )
    markup.row(types.InlineKeyboardButton("В админ-раздел", callback_data="admin_home"))
    send_screen(app, chat_id, "Список пользователей.", markup, call)


def render_admin_user(
    app: BotApp,
    chat_id: int,
    user_id: int,
    page: int,
    call: types.CallbackQuery | None = None,
) -> None:
    user = app.db.get_user_by_id(user_id)
    if not user:
        send_screen(app, chat_id, "Пользователь не найден.", None, call)
        return

    username = f"@{user['username']}" if user["username"] else "не указан"
    admin_text = "Да" if user["is_admin"] else "Нет"
    text = (
        "Карточка пользователя.\n\n"
        f"ФИО: <b>{user['full_name']}</b>\n"
        f"Роль: <b>{format_role(user)}</b>\n"
        f"Статус: <b>{user['status']}</b>\n"
        f"Администратор: <b>{admin_text}</b>\n"
        f"Username: <b>{username}</b>"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("К пользователям", callback_data=f"admin_users:{page}"))
    send_screen(app, chat_id, text, markup, call)


def render_admin_laundry_history_page(
    app: BotApp,
    chat_id: int,
    page: int = 0,
    call: types.CallbackQuery | None = None,
) -> None:
    since_at = to_iso(now_moscow() - timedelta(days=2))
    total = app.db.count_laundry_history(since_at)
    page = clamp_page(app, page, total)
    history = app.db.list_laundry_history(
        since_at,
        limit=app.page_size,
        offset=offset_for_page(app, page),
    )

    if not history:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("В админ-раздел", callback_data="admin_home"))
        send_screen(app, chat_id, "За последние два дня записей в прачечную не было.", markup, call)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for booking in history:
        state = "отмена" if booking["cancelled_at"] else "активна"
        label = (
            f"{format_slot(parse_dt(booking['start_at']))} | "
            f"{booking['full_name']} | "
            f"{LAUNDRY_TYPE_LABELS[booking['booking_type']]} | {state}"
        )
        markup.add(types.InlineKeyboardButton(label, callback_data="noop"))

    add_pagination_buttons(
        app,
        markup,
        page,
        total,
        f"admin_laundry:{page - 1}",
        f"admin_laundry:{page + 1}",
    )
    markup.row(types.InlineKeyboardButton("В админ-раздел", callback_data="admin_home"))
    send_screen(app, chat_id, "История прачечной за последние два дня.", markup, call)


def apply_admin_decision(app: BotApp, call: types.CallbackQuery, action: str, user_id: int, page: int) -> None:
    target = app.db.get_user_by_id(user_id)
    if not target or target["status"] != STATUS_PENDING:
        app.bot.answer_callback_query(call.id, "Заявка уже обработана.", show_alert=True)
        return

    new_status = STATUS_APPROVED if action == "approve" else STATUS_REJECTED
    updated = app.db.set_user_status(user_id, new_status)
    app.db.ensure_admins(app.settings.admin_ids)
    app.activity_logger.log(
        "registration_approved" if new_status == STATUS_APPROVED else "registration_rejected",
        user=updated["full_name"],
        telegram_id=updated["telegram_id"],
        role=format_role(updated),
        admin=call.from_user.id,
    )

    try:
        if new_status == STATUS_APPROVED:
            app.bot.send_message(
                updated["telegram_id"],
                "Ваша заявка одобрена. Теперь вам доступны команды /send, /read, /dialogs и /signup.",
            )
        else:
            app.bot.send_message(
                updated["telegram_id"],
                "Ваша заявка отклонена. При необходимости вы можете отправить новую через /register.",
            )
    except Exception:
        pass

    render_admin_pending_page(app, call.message.chat.id, page=page, call=call)
    app.bot.answer_callback_query(call.id, "Заявка обработана.")


def register_handlers(app: BotApp) -> None:
    bot = app.bot

    @bot.message_handler(commands=["admin"])
    def admin_command(message: types.Message) -> None:
        admin = require_admin(app, message)
        if not admin:
            return
        render_admin_home(app, message.chat.id)

    @bot.message_handler(commands=["pending"])
    def pending_command(message: types.Message) -> None:
        admin = require_admin(app, message)
        if not admin:
            return
        render_admin_pending_page(app, message.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data == "admin_home")
    def admin_home_callback(call: types.CallbackQuery) -> None:
        admin = ensure_callback_admin(app, call)
        if not admin:
            return
        render_admin_home(app, call.message.chat.id, call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_pending:"))
    def on_admin_pending(call: types.CallbackQuery) -> None:
        admin = ensure_callback_admin(app, call)
        if not admin:
            return
        page = int(call.data.split(":")[1])
        render_admin_pending_page(app, call.message.chat.id, page=page, call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_req:"))
    def on_admin_request(call: types.CallbackQuery) -> None:
        admin = ensure_callback_admin(app, call)
        if not admin:
            return
        _, user_id_text, page_text = call.data.split(":")
        render_admin_request(app, call.message.chat.id, int(user_id_text), int(page_text), call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_users:"))
    def on_admin_users(call: types.CallbackQuery) -> None:
        admin = ensure_callback_admin(app, call)
        if not admin:
            return
        page = int(call.data.split(":")[1])
        render_admin_users_page(app, call.message.chat.id, page=page, call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_user:"))
    def on_admin_user(call: types.CallbackQuery) -> None:
        admin = ensure_callback_admin(app, call)
        if not admin:
            return
        _, user_id_text, page_text = call.data.split(":")
        render_admin_user(app, call.message.chat.id, int(user_id_text), int(page_text), call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_laundry:"))
    def on_admin_laundry(call: types.CallbackQuery) -> None:
        admin = ensure_callback_admin(app, call)
        if not admin:
            return
        page = int(call.data.split(":")[1])
        render_admin_laundry_history_page(app, call.message.chat.id, page=page, call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_action:"))
    def on_admin_action(call: types.CallbackQuery) -> None:
        admin = ensure_callback_admin(app, call)
        if not admin:
            return
        _, action, user_id_text, page_text = call.data.split(":")
        apply_admin_decision(app, call, action, int(user_id_text), int(page_text))

    @bot.callback_query_handler(func=lambda call: call.data.startswith("admin_approve:") or call.data.startswith("admin_reject:"))
    def on_legacy_admin_action(call: types.CallbackQuery) -> None:
        admin = ensure_callback_admin(app, call)
        if not admin:
            return
        prefix, user_id_text = call.data.split(":")
        action = "approve" if prefix == "admin_approve" else "reject"
        apply_admin_decision(app, call, action, int(user_id_text), 0)
