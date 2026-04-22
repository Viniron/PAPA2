from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from database import (
    LAUNDRY_DRY,
    LAUNDRY_MODE_COTTON,
    LAUNDRY_MODE_DRY,
    LAUNDRY_MODE_LABELS,
    LAUNDRY_MODE_MIXED,
    LAUNDRY_TYPE_LABELS,
    LAUNDRY_WASH,
)


MOSCOW_TZ = timezone(timedelta(hours=3))
SLOT_STEP_MINUTES = 30
SIGNUP_WINDOW_DAYS = 2
FIRST_SLOT_TIME = time(hour=7, minute=0)
LAST_SLOT_TIME = time(hour=22, minute=30)
WASHER_CAPACITY = 3
DRYER_CAPACITY = 2


def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)


def parse_dt(raw_value: str) -> datetime:
    return datetime.fromisoformat(raw_value)


def to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def round_up_to_slot(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    minutes = dt.minute % SLOT_STEP_MINUTES
    if minutes == 0:
        return dt
    return dt + timedelta(minutes=SLOT_STEP_MINUTES - minutes)


def duration_for_booking(booking_type: str, mode: str) -> timedelta:
    if booking_type == LAUNDRY_WASH and mode == LAUNDRY_MODE_MIXED:
        return timedelta(minutes=90)
    return timedelta(hours=3)


def build_slots(now: datetime | None = None) -> list[datetime]:
    current = now or now_moscow()
    current = current.astimezone(MOSCOW_TZ)
    rounded_now = round_up_to_slot(current)
    horizon = rounded_now + timedelta(days=SIGNUP_WINDOW_DAYS)

    slots: list[datetime] = []
    current_day = rounded_now.date()
    while datetime.combine(current_day, FIRST_SLOT_TIME, MOSCOW_TZ) <= horizon:
        day_start = datetime.combine(current_day, FIRST_SLOT_TIME, MOSCOW_TZ)
        day_end = datetime.combine(current_day, LAST_SLOT_TIME, MOSCOW_TZ)
        slot = max(day_start, rounded_now)
        while slot <= day_end and slot <= horizon:
            slots.append(slot)
            slot += timedelta(minutes=SLOT_STEP_MINUTES)
        current_day += timedelta(days=1)
    return slots


def calculate_slot_availability(bookings: list[dict], now: datetime | None = None) -> list[dict]:
    slots = build_slots(now=now)
    parsed_bookings = []
    for booking in bookings:
        parsed_bookings.append(
            {
                "booking_type": booking["booking_type"],
                "start_at": parse_dt(booking["start_at"]),
                "end_at": parse_dt(booking["end_at"]),
            }
        )

    result: list[dict] = []
    for slot in slots:
        busy_wash = 0
        busy_dry = 0
        for booking in parsed_bookings:
            if booking["start_at"] <= slot < booking["end_at"]:
                if booking["booking_type"] == LAUNDRY_WASH:
                    busy_wash += 1
                elif booking["booking_type"] == LAUNDRY_DRY:
                    busy_dry += 1

        free_wash = max(0, WASHER_CAPACITY - busy_wash)
        free_dry = max(0, DRYER_CAPACITY - busy_dry)
        if free_wash + free_dry == 0:
            continue

        result.append(
            {
                "slot": slot,
                "slot_iso": to_iso(slot),
                "free_wash": free_wash,
                "free_dry": free_dry,
            }
        )
    return result


def booking_window_available(
    bookings: list[dict],
    booking_type: str,
    start_at: datetime,
    end_at: datetime,
) -> bool:
    slot = start_at
    while slot < end_at:
        busy = 0
        for booking in bookings:
            booking_start = parse_dt(booking["start_at"])
            booking_end = parse_dt(booking["end_at"])
            if booking["booking_type"] != booking_type:
                continue
            if booking_start <= slot < booking_end:
                busy += 1

        capacity = WASHER_CAPACITY if booking_type == LAUNDRY_WASH else DRYER_CAPACITY
        if busy >= capacity:
            return False
        slot += timedelta(minutes=SLOT_STEP_MINUTES)
    return True


def format_slot(slot: datetime) -> str:
    return slot.astimezone(MOSCOW_TZ).strftime("%d.%m %H:%M")


def format_booking_label(booking_type: str, mode: str) -> str:
    return f"{LAUNDRY_TYPE_LABELS[booking_type]}: {LAUNDRY_MODE_LABELS[mode]}"
