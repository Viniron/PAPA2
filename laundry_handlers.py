from __future__ import annotations

from datetime import timedelta

from telebot import types

from access import ensure_callback_approved_user, get_registered_user, require_approved_user
from app_context import BotApp
from bot_utils import add_pagination_buttons, clamp_page, offset_for_page, send_screen, slot_token
from database import (
    LAUNDRY_DRY,
    LAUNDRY_MODE_COTTON,
    LAUNDRY_MODE_DRY,
    LAUNDRY_MODE_LABELS,
    LAUNDRY_MODE_MIXED,
    LAUNDRY_TYPE_LABELS,
    LAUNDRY_WASH,
)
from laundry import (
    DRYER_CAPACITY,
    WASHER_CAPACITY,
    booking_window_available,
    calculate_slot_availability,
    duration_for_booking,
    format_booking_label,
    format_slot,
    now_moscow,
    parse_dt,
    to_iso,
)


def laundry_window_bounds() -> tuple[str, str]:
    now = now_moscow()
    return to_iso(now), to_iso(now + timedelta(days=3))


def load_laundry_entries(app: BotApp) -> tuple[list[dict], list[dict]]:
    range_start, range_end = laundry_window_bounds()
    bookings = app.db.list_laundry_bookings_in_range(range_start, range_end)
    slots = calculate_slot_availability(bookings, now=now_moscow())
    return bookings, slots


def find_slot_entry(app: BotApp, slot_value: str) -> tuple[list[dict], dict | None]:
    bookings, slots = load_laundry_entries(app)
    for entry in slots:
        if slot_token(entry["slot_iso"]) == slot_value:
            return bookings, entry
    return bookings, None


def cancel_future_laundry_bookings(app: BotApp, telegram_id: int) -> None:
    now_iso = to_iso(now_moscow())
    bookings = app.db.list_user_laundry_bookings(
        telegram_id,
        now_at=now_iso,
        limit=200,
        offset=0,
        include_active_only=True,
    )
    for booking in bookings:
        cancelled = app.db.cancel_laundry_booking(booking["id"])
        if not cancelled:
            continue
        app.activity_logger.log(
            "laundry_cancelled",
            user=booking["full_name"],
            booking=format_booking_label(booking["booking_type"], booking["mode"]),
            start_at=booking["start_at"],
            reason="registration_cancelled",
        )


def render_signup_home(
    app: BotApp,
    chat_id: int,
    telegram_id: int,
    page: int = 0,
    call: types.CallbackQuery | None = None,
) -> None:
    now_iso = to_iso(now_moscow())
    total = app.db.count_user_laundry_bookings(telegram_id, now_iso, include_active_only=True)
    page = clamp_page(app, page, total)
    bookings = app.db.list_user_laundry_bookings(
        telegram_id,
        now_at=now_iso,
        limit=app.page_size,
        offset=offset_for_page(app, page),
        include_active_only=True,
    )
    current_user = get_registered_user(app, telegram_id)
    if not current_user:
        send_screen(app, chat_id, "Пользователь не найден.", None, call)
        return

    wash_limit = app.db.count_user_future_laundry_bookings(current_user["id"], LAUNDRY_WASH, now_iso)
    dry_limit = app.db.count_user_future_laundry_bookings(current_user["id"], LAUNDRY_DRY, now_iso)
    text = (
        "Запись в прачечную.\n\n"
        f"Ваши будущие стирки: <b>{wash_limit}/1</b>\n"
        f"Ваши будущие сушки: <b>{dry_limit}/1</b>\n"
        f"Всего машин: стиралки <b>{WASHER_CAPACITY}</b>, сушки <b>{DRYER_CAPACITY}</b>."
    )

    markup = types.InlineKeyboardMarkup(row_width=1)
    for booking in bookings:
        label = (
            f"{format_slot(parse_dt(booking['start_at']))} | "
            f"{format_booking_label(booking['booking_type'], booking['mode'])}"
        )
        markup.add(
            types.InlineKeyboardButton(
                label,
                callback_data=f"signup_booking:{booking['id']}:{page}",
            )
        )

    add_pagination_buttons(
        app,
        markup,
        page,
        total,
        f"signup_home:{page - 1}",
        f"signup_home:{page + 1}",
    )
    markup.row(types.InlineKeyboardButton("Новая запись", callback_data="signup_slots:0"))
    send_screen(app, chat_id, text, markup, call)


def render_signup_slots_page(
    app: BotApp,
    chat_id: int,
    page: int = 0,
    call: types.CallbackQuery | None = None,
) -> None:
    _bookings, slots = load_laundry_entries(app)
    total = len(slots)
    page = clamp_page(app, page, total)
    start = offset_for_page(app, page)
    visible_slots = slots[start:start + app.page_size]

    if not visible_slots:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("К записям", callback_data="signup_home:0"))
        send_screen(app, chat_id, "Свободных окон для записи пока нет.", markup, call)
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for entry in visible_slots:
        label = (
            f"{format_slot(entry['slot'])} | "
            f"стирки: {entry['free_wash']} | сушки: {entry['free_dry']}"
        )
        markup.add(
            types.InlineKeyboardButton(
                label,
                callback_data=f"signup_slot:{slot_token(entry['slot_iso'])}:{page}",
            )
        )

    add_pagination_buttons(
        app,
        markup,
        page,
        total,
        f"signup_slots:{page - 1}",
        f"signup_slots:{page + 1}",
    )
    markup.row(types.InlineKeyboardButton("К записям", callback_data="signup_home:0"))
    send_screen(
        app,
        chat_id,
        "Выберите время. Показываются только окна, где доступна хотя бы одна машина.",
        markup,
        call,
    )


def render_signup_mode_page(
    app: BotApp,
    chat_id: int,
    telegram_id: int,
    slot_value: str,
    page: int = 0,
    call: types.CallbackQuery | None = None,
) -> None:
    current_user = get_registered_user(app, telegram_id)
    if not current_user:
        send_screen(app, chat_id, "Пользователь не найден.", None, call)
        return

    bookings, slot_entry = find_slot_entry(app, slot_value)
    if not slot_entry:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("К слотам", callback_data=f"signup_slots:{page}"))
        send_screen(app, chat_id, "Это окно уже недоступно. Выберите другое время.", markup, call)
        return

    now_iso = to_iso(now_moscow())
    wash_limit = app.db.count_user_future_laundry_bookings(current_user["id"], LAUNDRY_WASH, now_iso)
    dry_limit = app.db.count_user_future_laundry_bookings(current_user["id"], LAUNDRY_DRY, now_iso)
    slot_dt = slot_entry["slot"]

    text_lines = [
        f"Время записи: <b>{format_slot(slot_dt)}</b>",
        f"Свободные стиралки: <b>{slot_entry['free_wash']}</b>",
        f"Свободные сушки: <b>{slot_entry['free_dry']}</b>",
    ]
    markup = types.InlineKeyboardMarkup(row_width=1)

    if slot_entry["free_wash"] > 0 and wash_limit == 0:
        mixed_end = slot_dt + duration_for_booking(LAUNDRY_WASH, LAUNDRY_MODE_MIXED)
        if booking_window_available(bookings, LAUNDRY_WASH, slot_dt, mixed_end):
            markup.add(
                types.InlineKeyboardButton(
                    "Стирка: Смешанные вещи (1.5 часа)",
                    callback_data=f"signup_create:{LAUNDRY_WASH}:{LAUNDRY_MODE_MIXED}:{slot_value}:{page}",
                )
            )
        cotton_end = slot_dt + duration_for_booking(LAUNDRY_WASH, LAUNDRY_MODE_COTTON)
        if booking_window_available(bookings, LAUNDRY_WASH, slot_dt, cotton_end):
            markup.add(
                types.InlineKeyboardButton(
                    "Стирка: Хлопок (3 часа)",
                    callback_data=f"signup_create:{LAUNDRY_WASH}:{LAUNDRY_MODE_COTTON}:{slot_value}:{page}",
                )
            )
    elif wash_limit > 0:
        text_lines.append("Стирка недоступна: у вас уже есть будущая запись на стирку.")

    if slot_entry["free_dry"] > 0 and dry_limit == 0:
        dry_end = slot_dt + duration_for_booking(LAUNDRY_DRY, LAUNDRY_MODE_DRY)
        if booking_window_available(bookings, LAUNDRY_DRY, slot_dt, dry_end):
            markup.add(
                types.InlineKeyboardButton(
                    "Сушка (3 часа)",
                    callback_data=f"signup_create:{LAUNDRY_DRY}:{LAUNDRY_MODE_DRY}:{slot_value}:{page}",
                )
            )
    elif dry_limit > 0:
        text_lines.append("Сушка недоступна: у вас уже есть будущая запись на сушку.")

    markup.row(types.InlineKeyboardButton("Назад", callback_data=f"signup_slots:{page}"))
    send_screen(app, chat_id, "\n".join(text_lines), markup, call)


def render_signup_booking_page(
    app: BotApp,
    chat_id: int,
    telegram_id: int,
    booking_id: int,
    page: int,
    call: types.CallbackQuery | None = None,
) -> None:
    booking = app.db.get_laundry_booking(booking_id)
    if not booking or booking["telegram_id"] != telegram_id:
        send_screen(app, chat_id, "Запись не найдена.", None, call)
        return

    status_text = "Отменена" if booking["cancelled_at"] else "Активна"
    text = (
        "Ваша запись.\n\n"
        f"Время: <b>{format_slot(parse_dt(booking['start_at']))}</b>\n"
        f"Тип: <b>{format_booking_label(booking['booking_type'], booking['mode'])}</b>\n"
        f"Окончание: <b>{format_slot(parse_dt(booking['end_at']))}</b>\n"
        f"Статус: <b>{status_text}</b>"
    )
    markup = types.InlineKeyboardMarkup(row_width=1)
    if not booking["cancelled_at"] and parse_dt(booking["start_at"]) > now_moscow():
        markup.add(
            types.InlineKeyboardButton(
                "Отменить запись",
                callback_data=f"signup_cancel:{booking_id}:{page}",
            )
        )
    markup.row(types.InlineKeyboardButton("К записям", callback_data=f"signup_home:{page}"))
    send_screen(app, chat_id, text, markup, call)


def register_handlers(app: BotApp) -> None:
    bot = app.bot

    @bot.message_handler(commands=["signup"])
    def signup_command(message: types.Message) -> None:
        user = require_approved_user(app, message)
        if not user:
            return
        render_signup_home(app, message.chat.id, message.from_user.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("signup_home:"))
    def on_signup_home(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        page = int(call.data.split(":")[1])
        render_signup_home(app, call.message.chat.id, call.from_user.id, page=page, call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("signup_slots:"))
    def on_signup_slots(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        page = int(call.data.split(":")[1])
        render_signup_slots_page(app, call.message.chat.id, page=page, call=call)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("signup_slot:"))
    def on_signup_slot(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        _, slot_value, page_text = call.data.split(":")
        render_signup_mode_page(
            app,
            call.message.chat.id,
            call.from_user.id,
            slot_value=slot_value,
            page=int(page_text),
            call=call,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("signup_create:"))
    def on_signup_create(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        _, booking_type, mode, slot_value, page_text = call.data.split(":")
        bookings, slot_entry = find_slot_entry(app, slot_value)
        if not slot_entry:
            bot.answer_callback_query(call.id, "Это окно уже занято или устарело.", show_alert=True)
            return

        now_iso = to_iso(now_moscow())
        if app.db.count_user_future_laundry_bookings(user["id"], booking_type, now_iso) > 0:
            bot.answer_callback_query(call.id, "Лимит для этого типа записи уже исчерпан.", show_alert=True)
            return

        start_at = slot_entry["slot"]
        end_at = start_at + duration_for_booking(booking_type, mode)
        if not booking_window_available(bookings, booking_type, start_at, end_at):
            bot.answer_callback_query(call.id, "Для выбранной длительности окно уже недоступно.", show_alert=True)
            return

        booking_id = app.db.create_laundry_booking(
            user_id=user["id"],
            booking_type=booking_type,
            mode=mode,
            start_at=to_iso(start_at),
            end_at=to_iso(end_at),
        )
        app.activity_logger.log(
            "laundry_created",
            user=user["full_name"],
            booking_id=booking_id,
            booking=format_booking_label(booking_type, mode),
            start_at=to_iso(start_at),
            end_at=to_iso(end_at),
        )
        render_signup_home(app, call.message.chat.id, call.from_user.id, call=call)
        bot.answer_callback_query(call.id, "Запись создана.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("signup_booking:"))
    def on_signup_booking(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        _, booking_id_text, page_text = call.data.split(":")
        render_signup_booking_page(
            app,
            call.message.chat.id,
            call.from_user.id,
            booking_id=int(booking_id_text),
            page=int(page_text),
            call=call,
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("signup_cancel:"))
    def on_signup_cancel(call: types.CallbackQuery) -> None:
        user = ensure_callback_approved_user(app, call)
        if not user:
            return
        _, booking_id_text, page_text = call.data.split(":")
        booking = app.db.get_laundry_booking(int(booking_id_text))
        if not booking or booking["telegram_id"] != call.from_user.id:
            bot.answer_callback_query(call.id, "Запись не найдена.", show_alert=True)
            return
        if booking["cancelled_at"] or parse_dt(booking["start_at"]) <= now_moscow():
            bot.answer_callback_query(call.id, "Эту запись уже нельзя отменить.", show_alert=True)
            return

        cancelled = app.db.cancel_laundry_booking(int(booking_id_text))
        if cancelled:
            app.activity_logger.log(
                "laundry_cancelled",
                user=booking["full_name"],
                booking_id=booking["id"],
                booking=format_booking_label(booking["booking_type"], booking["mode"]),
                start_at=booking["start_at"],
                reason="user_cancelled",
            )
        render_signup_home(app, call.message.chat.id, call.from_user.id, page=int(page_text), call=call)
        bot.answer_callback_query(call.id, "Запись отменена.")
