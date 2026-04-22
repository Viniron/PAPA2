from __future__ import annotations

from datetime import datetime

from telebot import types

from app_context import BotApp
from database import (
    GRADE_PARALLELS,
    ROLE_LABELS,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    format_role,
)
from laundry import now_moscow, parse_dt


def set_commands(app: BotApp) -> None:
    commands = [
        types.BotCommand("start", "Главное меню"),
        types.BotCommand("register", "Подать заявку"),
        types.BotCommand("cancel_registration", "Отменить регистрацию"),
        types.BotCommand("send", "Отправить сообщение"),
        types.BotCommand("read", "Входящие уведомления"),
        types.BotCommand("dialogs", "Открыть диалоги"),
        types.BotCommand("signup", "Запись в прачечную"),
        types.BotCommand("admin", "Админ-раздел"),
        types.BotCommand("pending", "Заявки на одобрение"),
    ]
    app.bot.set_my_commands(commands)


def total_pages(app: BotApp, total_items: int) -> int:
    if total_items <= 0:
        return 1
    return (total_items + app.page_size - 1) // app.page_size


def clamp_page(app: BotApp, page: int, total_items: int) -> int:
    return max(0, min(page, total_pages(app, total_items) - 1))


def offset_for_page(app: BotApp, page: int) -> int:
    return page * app.page_size


def slot_token(slot_iso: str) -> str:
    return parse_dt(slot_iso).strftime("%Y%m%d%H%M")


def token_to_slot(token: str) -> datetime:
    return datetime.strptime(token, "%Y%m%d%H%M").replace(tzinfo=now_moscow().tzinfo)


def remove_message(app: BotApp, chat_id: int, message_id: int) -> None:
    try:
        app.bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def clear_chat_step_handler(app: BotApp, chat_id: int) -> None:
    try:
        app.bot.clear_step_handler_by_chat_id(chat_id)
    except Exception:
        pass


def send_screen(
    app: BotApp,
    chat_id: int,
    text: str,
    markup: types.InlineKeyboardMarkup | None = None,
    call: types.CallbackQuery | None = None,
) -> None:
    clear_chat_step_handler(app, chat_id)
    if call is not None:
        remove_message(app, call.message.chat.id, call.message.message_id)
    app.bot.send_message(chat_id, text, reply_markup=markup)


def add_pagination_buttons(
    app: BotApp,
    markup: types.InlineKeyboardMarkup,
    page: int,
    total_items: int,
    prev_callback: str,
    next_callback: str,
) -> None:
    pages = total_pages(app, total_items)
    if pages <= 1:
        return

    prev_button = types.InlineKeyboardButton("◀", callback_data=prev_callback)
    page_button = types.InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop")
    next_button = types.InlineKeyboardButton("▶", callback_data=next_callback)
    markup.row(prev_button, page_button, next_button)


def format_datetime(raw_value: str) -> str:
    dt = datetime.fromisoformat(raw_value)
    return dt.strftime("%d.%m.%Y %H:%M")


def content_type_label(content_type: str) -> str:
    labels = {
        "text": "Текст",
        "photo": "Фото",
        "video": "Видео",
        "document": "Файл",
    }
    return labels.get(content_type, "Сообщение")


def log_safe_content(content_type: str, preview_text: str | None) -> str:
    if content_type == "text":
        return preview_text or "-"
    if content_type == "photo":
        return "фото"
    if content_type == "video":
        return "видео"
    if content_type == "document":
        return "файл"
    return content_type


def short_preview(preview_text: str | None, content_type: str) -> str:
    if preview_text:
        compact = " ".join(preview_text.split())
        if len(compact) > 28:
            return compact[:25] + "..."
        return compact
    return content_type_label(content_type)


def message_preview(message: types.Message) -> str:
    if message.content_type == "text":
        return (message.text or "").strip()
    if message.caption:
        return message.caption.strip()
    return ""


def prompt_markup(back_callback: str | None = None) -> types.InlineKeyboardMarkup | None:
    if not back_callback:
        return None

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Назад", callback_data=back_callback))
    return markup


def main_menu_text(user: dict | None) -> str:
    if not user:
        return (
            "Это внутренний школьный мессенджер.\n\n"
            "Команды:\n"
            "/register - подать заявку\n"
            "/send - отправить сообщение\n"
            "/read - входящие уведомления\n"
            "/dialogs - список диалогов\n"
            "/signup - запись в прачечную"
        )

    role_text = format_role(user)
    if user["status"] == STATUS_APPROVED:
        admin_line = "\n/admin - админ-раздел" if user["is_admin"] else ""
        return (
            f"Вы зарегистрированы как <b>{role_text}</b>.\n\n"
            "Команды:\n"
            "/send - отправить сообщение\n"
            "/read - входящие уведомления\n"
            "/signup - запись в прачечную\n"
            "/dialogs - список диалогов\n"
            "/register - обновить заявку\n"
            f"/cancel_registration - отменить регистрацию{admin_line}"
        )

    if user["status"] == STATUS_CANCELLED:
        return (
            "Ваша предыдущая регистрация отменена.\n\n"
            "Команды:\n"
            "/register - создать новую заявку"
        )

    if user["status"] == STATUS_REJECTED:
        return (
            f"Последняя заявка отклонена для роли <b>{role_text}</b>.\n\n"
            "Команды:\n"
            "/register - подать новую заявку"
        )

    return (
        f"Ваша роль: <b>{role_text}</b>\n"
        "Статус заявки: <b>ожидает подтверждения</b>.\n\n"
        "После одобрения вам откроются команды /send, /read, /dialogs и /signup."
    )


def role_choice_markup() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Преподаватель", callback_data="reg_role:teacher"),
        types.InlineKeyboardButton("Ученик", callback_data="reg_role:student"),
        types.InlineKeyboardButton("Воспитатель", callback_data="reg_role:educator"),
        types.InlineKeyboardButton("Остальные", callback_data="reg_role:other"),
    )
    return markup


def grade_markup(prefix: str, back_callback: str | None = None) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(str(grade), callback_data=f"{prefix}:{grade}")
        for grade in GRADE_PARALLELS
    ]
    markup.add(*buttons)
    if back_callback:
        markup.row(types.InlineKeyboardButton("Назад", callback_data=back_callback))
    return markup


def parallel_markup(prefix: str, grade: int, back_callback: str | None = None) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=3)
    buttons = [
        types.InlineKeyboardButton(parallel, callback_data=f"{prefix}:{grade}:{parallel}")
        for parallel in GRADE_PARALLELS[grade]
    ]
    markup.add(*buttons)
    if back_callback:
        markup.row(types.InlineKeyboardButton("Назад", callback_data=back_callback))
    return markup


def send_root_markup() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Преподаватель", callback_data="send_role:teacher"),
        types.InlineKeyboardButton("Ученик", callback_data="send_role:student"),
        types.InlineKeyboardButton("Воспитатель", callback_data="send_role:educator"),
        types.InlineKeyboardButton("Остальные", callback_data="send_role:other"),
    )
    return markup


def recipient_list_title(role: str, class_number: int | None = None, parallel: str | None = None) -> str:
    if role == "student" and class_number is not None and parallel is not None:
        return f"Выберите ученика из {class_number}{parallel}."
    return f"Выберите получателя: {ROLE_LABELS[role]}."
