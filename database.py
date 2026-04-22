from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator


ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLE_EDUCATOR = "educator"
ROLE_OTHER = "other"

ROLE_LABELS = {
    ROLE_STUDENT: "Ученик",
    ROLE_TEACHER: "Преподаватель",
    ROLE_EDUCATOR: "Воспитатель",
    ROLE_OTHER: "Остальные",
}

GRADE_PARALLELS = {
    8: ("М", "К", "Г"),
    9: ("М", "К", "Г", "Е"),
    10: ("И", "М", "К", "Д", "Г", "Б"),
    11: ("И", "М", "К", "Д", "Г", "Б"),
}

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"

LAUNDRY_WASH = "wash"
LAUNDRY_DRY = "dry"

LAUNDRY_MODE_MIXED = "mixed"
LAUNDRY_MODE_COTTON = "cotton"
LAUNDRY_MODE_DRY = "dry"

LAUNDRY_TYPE_LABELS = {
    LAUNDRY_WASH: "Стирка",
    LAUNDRY_DRY: "Сушка",
}

LAUNDRY_MODE_LABELS = {
    LAUNDRY_MODE_MIXED: "Смешанные вещи",
    LAUNDRY_MODE_COTTON: "Хлопок",
    LAUNDRY_MODE_DRY: "Сушка",
}


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    class_number INTEGER,
                    parallel TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_user_id INTEGER NOT NULL,
                    recipient_user_id INTEGER NOT NULL,
                    source_chat_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    preview_text TEXT,
                    reply_to_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    read_at TEXT,
                    FOREIGN KEY (sender_user_id) REFERENCES users(id),
                    FOREIGN KEY (recipient_user_id) REFERENCES users(id),
                    FOREIGN KEY (reply_to_message_id) REFERENCES messages(id)
                );

                CREATE TABLE IF NOT EXISTS laundry_bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    booking_type TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    cancelled_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_users_status_role
                ON users(status, role, full_name);

                CREATE INDEX IF NOT EXISTS idx_messages_recipient_created
                ON messages(recipient_user_id, is_read, created_at, id);

                CREATE INDEX IF NOT EXISTS idx_messages_dialog_created
                ON messages(sender_user_id, recipient_user_id, created_at, id);

                CREATE INDEX IF NOT EXISTS idx_laundry_user_type_start
                ON laundry_bookings(user_id, booking_type, start_at);

                CREATE INDEX IF NOT EXISTS idx_laundry_active_range
                ON laundry_bookings(booking_type, start_at, end_at, cancelled_at);
                """
            )
            self._ensure_column(
                connection,
                table_name="messages",
                column_name="reply_to_message_id",
                column_sql="INTEGER REFERENCES messages(id)",
            )

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_sql: str,
    ) -> None:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_names = {row["name"] for row in rows}
        if column_name in existing_names:
            return
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"
        )

    def ensure_admins(self, admin_ids: tuple[int, ...]) -> None:
        if not admin_ids:
            return

        placeholders = ", ".join("?" for _ in admin_ids)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE users SET is_admin = 1 WHERE telegram_id IN ({placeholders})",
                admin_ids,
            )

    def upsert_registration(
        self,
        telegram_id: int,
        username: str | None,
        full_name: str,
        role: str,
        class_number: int | None = None,
        parallel: str | None = None,
    ) -> dict:
        now = _now_iso()

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()

            if existing:
                connection.execute(
                    """
                    UPDATE users
                    SET username = ?, full_name = ?, role = ?, class_number = ?, parallel = ?,
                        status = ?, updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (
                        username,
                        full_name,
                        role,
                        class_number,
                        parallel,
                        STATUS_PENDING,
                        now,
                        telegram_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO users (
                        telegram_id, username, full_name, role, class_number, parallel,
                        status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        telegram_id,
                        username,
                        full_name,
                        role,
                        class_number,
                        parallel,
                        STATUS_PENDING,
                        now,
                        now,
                    ),
                )

        return self.get_user_by_telegram_id(telegram_id)

    def cancel_registration(self, telegram_id: int) -> dict | None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET status = ?, updated_at = ?
                WHERE telegram_id = ?
                """,
                (STATUS_CANCELLED, now, telegram_id),
            )
        return self.get_user_by_telegram_id(telegram_id)

    def get_user_by_telegram_id(self, telegram_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        return _row_to_dict(row)

    def get_user_by_id(self, user_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return _row_to_dict(row)

    def count_users(self, status: str | None = None) -> int:
        query = "SELECT COUNT(*) AS total FROM users"
        params: list[object] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)

        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return int(row["total"])

    def list_users(
        self,
        status: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict]:
        query = "SELECT * FROM users"
        params: list[object] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY full_name COLLATE NOCASE ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def count_pending_users(self) -> int:
        return self.count_users(STATUS_PENDING)

    def get_pending_users(self, limit: int = 10, offset: int = 0) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM users
                WHERE status = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ? OFFSET ?
                """,
                (STATUS_PENDING, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_admins(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM users
                WHERE is_admin = 1 AND status = ?
                ORDER BY full_name COLLATE NOCASE ASC
                """,
                (STATUS_APPROVED,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_user_status(self, user_id: int, status: str) -> dict | None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, user_id),
            )
        return self.get_user_by_id(user_id)

    def count_recipients_by_role(
        self,
        role: str,
        class_number: int | None = None,
        parallel: str | None = None,
        exclude_telegram_id: int | None = None,
    ) -> int:
        query = ["SELECT COUNT(*) AS total FROM users WHERE status = ? AND role = ?"]
        params: list[object] = [STATUS_APPROVED, role]

        if class_number is not None:
            query.append("AND class_number = ?")
            params.append(class_number)

        if parallel is not None:
            query.append("AND parallel = ?")
            params.append(parallel)

        if exclude_telegram_id is not None:
            query.append("AND telegram_id != ?")
            params.append(exclude_telegram_id)

        with self._connect() as connection:
            row = connection.execute(" ".join(query), params).fetchone()
        return int(row["total"])

    def list_recipients_by_role(
        self,
        role: str,
        class_number: int | None = None,
        parallel: str | None = None,
        exclude_telegram_id: int | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict]:
        query = ["SELECT * FROM users WHERE status = ? AND role = ?"]
        params: list[object] = [STATUS_APPROVED, role]

        if class_number is not None:
            query.append("AND class_number = ?")
            params.append(class_number)

        if parallel is not None:
            query.append("AND parallel = ?")
            params.append(parallel)

        if exclude_telegram_id is not None:
            query.append("AND telegram_id != ?")
            params.append(exclude_telegram_id)

        query.append("ORDER BY full_name COLLATE NOCASE ASC LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        with self._connect() as connection:
            rows = connection.execute(" ".join(query), params).fetchall()
        return [dict(row) for row in rows]

    def create_message(
        self,
        sender_user_id: int,
        recipient_user_id: int,
        source_chat_id: int,
        source_message_id: int,
        content_type: str,
        preview_text: str | None,
        reply_to_message_id: int | None = None,
    ) -> int:
        now = _now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (
                    sender_user_id, recipient_user_id, source_chat_id, source_message_id,
                    content_type, preview_text, reply_to_message_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sender_user_id,
                    recipient_user_id,
                    source_chat_id,
                    source_message_id,
                    content_type,
                    preview_text,
                    reply_to_message_id,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def count_messages_for_user(self, telegram_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM messages AS m
                JOIN users AS recipient ON recipient.id = m.recipient_user_id
                WHERE recipient.telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
        return int(row["total"])

    def list_messages_for_user(
        self,
        telegram_id: int,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    m.*,
                    sender.full_name AS sender_name,
                    sender.role AS sender_role,
                    sender.class_number AS sender_class_number,
                    sender.parallel AS sender_parallel,
                    reply_source.preview_text AS reply_preview_text,
                    reply_sender.full_name AS reply_sender_name
                FROM messages AS m
                JOIN users AS recipient ON recipient.id = m.recipient_user_id
                JOIN users AS sender ON sender.id = m.sender_user_id
                LEFT JOIN messages AS reply_source ON reply_source.id = m.reply_to_message_id
                LEFT JOIN users AS reply_sender ON reply_sender.id = reply_source.sender_user_id
                WHERE recipient.telegram_id = ?
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT ? OFFSET ?
                """,
                (telegram_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_message(self, message_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    m.*,
                    sender.telegram_id AS sender_telegram_id,
                    sender.full_name AS sender_name,
                    sender.role AS sender_role,
                    sender.class_number AS sender_class_number,
                    sender.parallel AS sender_parallel,
                    recipient.telegram_id AS recipient_telegram_id,
                    recipient.full_name AS recipient_name,
                    recipient.role AS recipient_role,
                    recipient.class_number AS recipient_class_number,
                    recipient.parallel AS recipient_parallel,
                    reply_source.preview_text AS reply_preview_text,
                    reply_sender.full_name AS reply_sender_name
                FROM messages AS m
                JOIN users AS sender ON sender.id = m.sender_user_id
                JOIN users AS recipient ON recipient.id = m.recipient_user_id
                LEFT JOIN messages AS reply_source ON reply_source.id = m.reply_to_message_id
                LEFT JOIN users AS reply_sender ON reply_sender.id = reply_source.sender_user_id
                WHERE m.id = ?
                """,
                (message_id,),
            ).fetchone()
        return _row_to_dict(row)

    def get_message_for_user(self, message_id: int, telegram_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    m.*,
                    sender.telegram_id AS sender_telegram_id,
                    sender.full_name AS sender_name,
                    sender.role AS sender_role,
                    sender.class_number AS sender_class_number,
                    sender.parallel AS sender_parallel,
                    recipient.telegram_id AS recipient_telegram_id,
                    recipient.full_name AS recipient_name,
                    recipient.role AS recipient_role,
                    recipient.class_number AS recipient_class_number,
                    recipient.parallel AS recipient_parallel,
                    reply_source.preview_text AS reply_preview_text,
                    reply_sender.full_name AS reply_sender_name
                FROM messages AS m
                JOIN users AS recipient ON recipient.id = m.recipient_user_id
                JOIN users AS sender ON sender.id = m.sender_user_id
                LEFT JOIN messages AS reply_source ON reply_source.id = m.reply_to_message_id
                LEFT JOIN users AS reply_sender ON reply_sender.id = reply_source.sender_user_id
                WHERE m.id = ?
                AND (recipient.telegram_id = ? OR sender.telegram_id = ?)
                """,
                (message_id, telegram_id, telegram_id),
            ).fetchone()
        return _row_to_dict(row)

    def mark_message_as_read(self, message_id: int) -> None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE messages
                SET is_read = 1, read_at = ?
                WHERE id = ? AND is_read = 0
                """,
                (now, message_id),
            )

    def count_dialogs_for_user(self, telegram_id: int) -> int:
        current_user = self.get_user_by_telegram_id(telegram_id)
        if not current_user:
            return 0

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM (
                    SELECT
                        CASE
                            WHEN sender_user_id = ? THEN recipient_user_id
                            ELSE sender_user_id
                        END AS partner_user_id
                    FROM messages
                    WHERE sender_user_id = ? OR recipient_user_id = ?
                    GROUP BY partner_user_id
                )
                """,
                (current_user["id"], current_user["id"], current_user["id"]),
            ).fetchone()
        return int(row["total"])

    def list_dialogs_for_user(
        self,
        telegram_id: int,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict]:
        current_user = self.get_user_by_telegram_id(telegram_id)
        if not current_user:
            return []

        current_user_id = current_user["id"]
        with self._connect() as connection:
            rows = connection.execute(
                """
                WITH latest_messages AS (
                    SELECT
                        MAX(m.id) AS last_message_id,
                        CASE
                            WHEN m.sender_user_id = ? THEN m.recipient_user_id
                            ELSE m.sender_user_id
                        END AS partner_user_id
                    FROM messages AS m
                    WHERE m.sender_user_id = ? OR m.recipient_user_id = ?
                    GROUP BY partner_user_id
                )
                SELECT
                    partner.id AS partner_user_id,
                    partner.telegram_id AS partner_telegram_id,
                    partner.full_name AS partner_name,
                    partner.role AS partner_role,
                    partner.class_number AS partner_class_number,
                    partner.parallel AS partner_parallel,
                    latest.last_message_id,
                    last_message.sender_user_id AS last_sender_user_id,
                    last_message.preview_text AS last_preview_text,
                    last_message.content_type AS last_content_type,
                    last_message.created_at AS last_created_at,
                    (
                        SELECT COUNT(*)
                        FROM messages AS unread
                        WHERE unread.sender_user_id = partner.id
                        AND unread.recipient_user_id = ?
                        AND unread.is_read = 0
                    ) AS unread_count
                FROM latest_messages AS latest
                JOIN users AS partner ON partner.id = latest.partner_user_id
                JOIN messages AS last_message ON last_message.id = latest.last_message_id
                ORDER BY last_message.created_at DESC, last_message.id DESC
                LIMIT ? OFFSET ?
                """,
                (
                    current_user_id,
                    current_user_id,
                    current_user_id,
                    current_user_id,
                    limit,
                    offset,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_dialog_messages(self, telegram_id: int, partner_user_id: int) -> int:
        current_user = self.get_user_by_telegram_id(telegram_id)
        if not current_user:
            return 0

        current_user_id = current_user["id"]
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM messages
                WHERE
                    (sender_user_id = ? AND recipient_user_id = ?)
                    OR
                    (sender_user_id = ? AND recipient_user_id = ?)
                """,
                (current_user_id, partner_user_id, partner_user_id, current_user_id),
            ).fetchone()
        return int(row["total"])

    def list_dialog_messages(
        self,
        telegram_id: int,
        partner_user_id: int,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict]:
        current_user = self.get_user_by_telegram_id(telegram_id)
        if not current_user:
            return []

        current_user_id = current_user["id"]
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    m.*,
                    sender.telegram_id AS sender_telegram_id,
                    sender.full_name AS sender_name,
                    sender.role AS sender_role,
                    sender.class_number AS sender_class_number,
                    sender.parallel AS sender_parallel,
                    recipient.telegram_id AS recipient_telegram_id,
                    recipient.full_name AS recipient_name,
                    recipient.role AS recipient_role,
                    recipient.class_number AS recipient_class_number,
                    recipient.parallel AS recipient_parallel,
                    reply_source.preview_text AS reply_preview_text,
                    reply_sender.full_name AS reply_sender_name
                FROM messages AS m
                JOIN users AS sender ON sender.id = m.sender_user_id
                JOIN users AS recipient ON recipient.id = m.recipient_user_id
                LEFT JOIN messages AS reply_source ON reply_source.id = m.reply_to_message_id
                LEFT JOIN users AS reply_sender ON reply_sender.id = reply_source.sender_user_id
                WHERE
                    (m.sender_user_id = ? AND m.recipient_user_id = ?)
                    OR
                    (m.sender_user_id = ? AND m.recipient_user_id = ?)
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT ? OFFSET ?
                """,
                (
                    current_user_id,
                    partner_user_id,
                    partner_user_id,
                    current_user_id,
                    limit,
                    offset,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_dialog_partner(self, telegram_id: int, partner_user_id: int) -> dict | None:
        current_user = self.get_user_by_telegram_id(telegram_id)
        if not current_user:
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM users
                WHERE id = ?
                AND status = ?
                """,
                (partner_user_id, STATUS_APPROVED),
            ).fetchone()
        return _row_to_dict(row)

    def create_laundry_booking(
        self,
        user_id: int,
        booking_type: str,
        mode: str,
        start_at: str,
        end_at: str,
    ) -> int:
        now = _now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO laundry_bookings (
                    user_id, booking_type, mode, start_at, end_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, booking_type, mode, start_at, end_at, now),
            )
            return int(cursor.lastrowid)

    def get_laundry_booking(self, booking_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    booking.*,
                    user.telegram_id,
                    user.full_name,
                    user.role,
                    user.class_number,
                    user.parallel
                FROM laundry_bookings AS booking
                JOIN users AS user ON user.id = booking.user_id
                WHERE booking.id = ?
                """,
                (booking_id,),
            ).fetchone()
        return _row_to_dict(row)

    def cancel_laundry_booking(self, booking_id: int) -> dict | None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE laundry_bookings
                SET cancelled_at = ?
                WHERE id = ? AND cancelled_at IS NULL
                """,
                (now, booking_id),
            )
        return self.get_laundry_booking(booking_id)

    def count_user_future_laundry_bookings(
        self,
        user_id: int,
        booking_type: str,
        now_at: str,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM laundry_bookings
                WHERE user_id = ?
                AND booking_type = ?
                AND cancelled_at IS NULL
                AND start_at > ?
                """,
                (user_id, booking_type, now_at),
            ).fetchone()
        return int(row["total"])

    def list_laundry_bookings_in_range(
        self,
        range_start: str,
        range_end: str,
        include_cancelled: bool = False,
    ) -> list[dict]:
        query = [
            """
            SELECT
                booking.*,
                user.telegram_id,
                user.full_name,
                user.role,
                user.class_number,
                user.parallel
            FROM laundry_bookings AS booking
            JOIN users AS user ON user.id = booking.user_id
            WHERE booking.start_at < ?
            AND booking.end_at > ?
            """
        ]
        params: list[object] = [range_end, range_start]
        if not include_cancelled:
            query.append("AND booking.cancelled_at IS NULL")
        query.append("ORDER BY booking.start_at ASC, booking.id ASC")

        with self._connect() as connection:
            rows = connection.execute(" ".join(query), params).fetchall()
        return [dict(row) for row in rows]

    def count_user_laundry_bookings(
        self,
        telegram_id: int,
        now_at: str,
        include_active_only: bool = True,
    ) -> int:
        current_user = self.get_user_by_telegram_id(telegram_id)
        if not current_user:
            return 0

        query = [
            """
            SELECT COUNT(*) AS total
            FROM laundry_bookings
            WHERE user_id = ?
            AND cancelled_at IS NULL
            """
        ]
        params: list[object] = [current_user["id"]]
        if include_active_only:
            query.append("AND start_at > ?")
            params.append(now_at)

        with self._connect() as connection:
            row = connection.execute(" ".join(query), params).fetchone()
        return int(row["total"])

    def list_user_laundry_bookings(
        self,
        telegram_id: int,
        now_at: str,
        limit: int = 10,
        offset: int = 0,
        include_active_only: bool = True,
    ) -> list[dict]:
        current_user = self.get_user_by_telegram_id(telegram_id)
        if not current_user:
            return []

        query = [
            """
            SELECT
                booking.*,
                user.telegram_id,
                user.full_name,
                user.role,
                user.class_number,
                user.parallel
            FROM laundry_bookings AS booking
            JOIN users AS user ON user.id = booking.user_id
            WHERE booking.user_id = ?
            AND booking.cancelled_at IS NULL
            """
        ]
        params: list[object] = [current_user["id"]]
        if include_active_only:
            query.append("AND booking.start_at > ?")
            params.append(now_at)
        query.append("ORDER BY booking.start_at ASC, booking.id ASC LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        with self._connect() as connection:
            rows = connection.execute(" ".join(query), params).fetchall()
        return [dict(row) for row in rows]

    def count_laundry_history(self, since_at: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM laundry_bookings
                WHERE start_at >= ?
                """,
                (since_at,),
            ).fetchone()
        return int(row["total"])

    def list_laundry_history(
        self,
        since_at: str,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    booking.*,
                    user.telegram_id,
                    user.full_name,
                    user.role,
                    user.class_number,
                    user.parallel
                FROM laundry_bookings AS booking
                JOIN users AS user ON user.id = booking.user_id
                WHERE booking.start_at >= ?
                ORDER BY booking.start_at DESC, booking.id DESC
                LIMIT ? OFFSET ?
                """,
                (since_at, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_admin_statistics(self) -> dict:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, role, COUNT(*) AS total
                FROM users
                GROUP BY status, role
                """
            ).fetchall()

        stats = {
            "total_users": 0,
            "pending_users": 0,
            "approved_users": 0,
            "rejected_users": 0,
            "cancelled_users": 0,
            "roles": {
                ROLE_TEACHER: 0,
                ROLE_STUDENT: 0,
                ROLE_EDUCATOR: 0,
                ROLE_OTHER: 0,
            },
        }
        for row in rows:
            total = int(row["total"])
            stats["total_users"] += total
            if row["status"] == STATUS_PENDING:
                stats["pending_users"] += total
            elif row["status"] == STATUS_APPROVED:
                stats["approved_users"] += total
                if row["role"] in stats["roles"]:
                    stats["roles"][row["role"]] += total
            elif row["status"] == STATUS_REJECTED:
                stats["rejected_users"] += total
            elif row["status"] == STATUS_CANCELLED:
                stats["cancelled_users"] += total
        return stats


def format_role(user: dict) -> str:
    role_label = ROLE_LABELS.get(user["role"], user["role"])
    if user["role"] == ROLE_STUDENT and user["class_number"] and user["parallel"]:
        return f"{role_label} {user['class_number']}{user['parallel']}"
    return role_label
