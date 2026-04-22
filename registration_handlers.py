from __future__ import annotations

from telebot import types

from access import get_registered_user
from admin_handlers import render_admin_pending_page
from app_context import BotApp
from bot_utils import (
    clear_chat_step_handler,
    grade_markup,
    main_menu_text,
    parallel_markup,
    prompt_markup,
    remove_message,
    role_choice_markup,
    send_screen,
)
from database import ROLE_STUDENT, STATUS_APPROVED, STATUS_CANCELLED, STATUS_PENDING, format_role
from laundry_handlers import cancel_future_laundry_bookings


def send_registration_notification(app: BotApp, admin_telegram_id: int, user: dict) -> bool:
    username_text = f"@{user['username']}" if user["username"] else "не указан"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("Открыть заявку", callback_data=f"admin_req:{user['id']}:0"))
    markup.row(
        types.InlineKeyboardButton("Одобрить", callback_data=f"admin_action:approve:{user['id']}:0"),
        types.InlineKeyboardButton("Отклонить", callback_data=f"admin_action:reject:{user['id']}:0"),
    )
    text = (
        "Новая заявка на регистрацию.\n"
        f"ФИО: <b>{user['full_name']}</b>\n"
        f"Роль: <b>{format_role(user)}</b>\n"
        f"Username: <b>{username_text}</b>"
    )
    try:
        app.bot.send_message(admin_telegram_id, text, reply_markup=markup)
        return True
    except Exception:
        return False


def notify_admins_about_registration(app: BotApp, user: dict) -> int:
    approved_admins = app.db.list_admins()
    if approved_admins:
        delivered = 0
        for admin in approved_admins:
            delivered += int(send_registration_notification(app, admin["telegram_id"], user))
        return delivered

    delivered = 0
    for admin_id in app.settings.admin_ids:
        delivered += int(send_registration_notification(app, admin_id, user))
    return delivered


def prompt_for_registration_name(
    app: BotApp,
    chat_id: int,
    role: str,
    class_number: int | None = None,
    parallel: str | None = None,
    prompt_text: str | None = None,
    back_callback: str | None = None,
    call: types.CallbackQuery | None = None,
) -> None:
    clear_chat_step_handler(app, chat_id)
    if call is not None:
        remove_message(app, call.message.chat.id, call.message.message_id)
    text = prompt_text or "Напишите ваше ФИО одним сообщением."
    prompt = app.bot.send_message(chat_id, text, reply_markup=prompt_markup(back_callback))
    app.bot.register_next_step_handler(
        prompt,
        lambda message: save_registration(app, message, role, class_number, parallel),
    )


def save_registration(
    app: BotApp,
    message: types.Message,
    role: str,
    class_number: int | None = None,
    parallel: str | None = None,
) -> None:
    if message.content_type != "text" or not (message.text or "").strip():
        app.bot.reply_to(message, "Нужно отправить ФИО текстом. Попробуйте снова через /register.")
        return

    full_name = message.text.strip()
    user = app.db.upsert_registration(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=full_name,
        role=role,
        class_number=class_number,
        parallel=parallel,
    )
    app.activity_logger.log(
        "registration_submitted",
        user=full_name,
        telegram_id=message.from_user.id,
        role=format_role(user),
    )

    if message.from_user.id in app.settings.admin_ids:
        app.db.set_user_status(user["id"], STATUS_APPROVED)
        app.db.ensure_admins(app.settings.admin_ids)
        app.activity_logger.log(
            "registration_approved",
            user=full_name,
            telegram_id=message.from_user.id,
            role=format_role(user),
            approved_by="system_admin_bootstrap",
        )
        app.bot.reply_to(message, "Вы зарегистрированы и сразу получили права администратора.")
        return

    delivered = notify_admins_about_registration(app, user)
    if delivered:
        app.bot.reply_to(
            message,
            "Заявка отправлена администраторам. После подтверждения вы сможете пользоваться мессенджером.",
        )
        return

    app.bot.reply_to(
        message,
        "Заявка сохранена, но уведомление администраторам не доставилось. "
        "Проверьте BOT_ADMIN_IDS и убедитесь, что администраторы уже начали диалог с ботом.",
    )


def render_cancel_registration_confirm(app: BotApp, chat_id: int, call: types.CallbackQuery | None = None) -> None:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("Подтвердить", callback_data="registration_cancel:confirm"),
        types.InlineKeyboardButton("Назад", callback_data="registration_cancel:back"),
    )
    send_screen(
        app,
        chat_id,
        "Отменить текущую регистрацию? После этого вы сможете создать новую через /register.",
        markup,
        call,
    )


def register_handlers(app: BotApp) -> None:
    bot = app.bot

    @bot.message_handler(commands=["register"])
    def register_command(message: types.Message) -> None:
        bot.send_message(message.chat.id, "Выберите роль для регистрации.", reply_markup=role_choice_markup())

    @bot.message_handler(commands=["cancel_registration"])
    def cancel_registration_command(message: types.Message) -> None:
        user = get_registered_user(app, message.from_user.id)
        if not user or user["status"] == STATUS_CANCELLED:
            bot.reply_to(message, "У вас нет активной регистрации, которую можно отменить.")
            return
        render_cancel_registration_confirm(app, message.chat.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("registration_cancel:"))
    def registration_cancel_callback(call: types.CallbackQuery) -> None:
        user = get_registered_user(app, call.from_user.id)
        if not user or user["status"] == STATUS_CANCELLED:
            bot.answer_callback_query(call.id, "Активной регистрации нет.", show_alert=True)
            return

        action = call.data.split(":")[1]
        if action == "back":
            remove_message(app, call.message.chat.id, call.message.message_id)
            bot.send_message(call.message.chat.id, main_menu_text(user))
            bot.answer_callback_query(call.id)
            return

        cancel_future_laundry_bookings(app, call.from_user.id)
        updated = app.db.cancel_registration(call.from_user.id)
        app.activity_logger.log(
            "registration_cancelled",
            user=updated["full_name"] if updated else user["full_name"],
            telegram_id=call.from_user.id,
            previous_status=user["status"],
        )
        send_screen(
            app,
            call.message.chat.id,
            "Регистрация отменена. Вы можете создать новую через /register.",
            None,
            call,
        )
        bot.answer_callback_query(call.id, "Регистрация отменена.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("reg_role:"))
    def on_register_role(call: types.CallbackQuery) -> None:
        role = call.data.split(":")[1]
        if role == ROLE_STUDENT:
            send_screen(
                app,
                call.message.chat.id,
                "Выберите номер класса.",
                grade_markup("reg_grade", back_callback="reg_nav:role"),
                call,
            )
        else:
            prompt_for_registration_name(
                app,
                call.message.chat.id,
                role=role,
                back_callback="reg_nav:role",
                call=call,
            )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("reg_grade:"))
    def on_register_grade(call: types.CallbackQuery) -> None:
        grade = int(call.data.split(":")[1])
        send_screen(
            app,
            call.message.chat.id,
            "Выберите параллель.",
            parallel_markup("reg_parallel", grade, back_callback="reg_nav:grade"),
            call,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("reg_nav:"))
    def on_register_navigation(call: types.CallbackQuery) -> None:
        payload = call.data.split(":")
        target = payload[1]
        if target == "role":
            send_screen(app, call.message.chat.id, "Выберите роль для регистрации.", role_choice_markup(), call)
        elif target == "grade":
            send_screen(
                app,
                call.message.chat.id,
                "Выберите номер класса.",
                grade_markup("reg_grade", back_callback="reg_nav:role"),
                call,
            )
        elif target == "parallel":
            grade = int(payload[2])
            send_screen(
                app,
                call.message.chat.id,
                "Выберите параллель.",
                parallel_markup("reg_parallel", grade, back_callback="reg_nav:grade"),
                call,
            )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("reg_parallel:"))
    def on_register_parallel(call: types.CallbackQuery) -> None:
        _, grade_text, parallel = call.data.split(":")
        grade = int(grade_text)
        prompt_for_registration_name(
            app,
            call.message.chat.id,
            role=ROLE_STUDENT,
            class_number=grade,
            parallel=parallel,
            prompt_text=f"Вы выбрали {grade}{parallel}. Напишите ваше ФИО одним сообщением.",
            back_callback=f"reg_nav:parallel:{grade}",
            call=call,
        )
        bot.answer_callback_query(call.id)
