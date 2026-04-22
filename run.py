from __future__ import annotations

from app_context import create_app
from bot_utils import set_commands
import admin_handlers
import core_handlers
import dialogs_handlers
import laundry_handlers
import messaging_handlers
import registration_handlers


def main() -> None:
    app = create_app()
    core_handlers.register_handlers(app)
    registration_handlers.register_handlers(app)
    messaging_handlers.register_handlers(app)
    dialogs_handlers.register_handlers(app)
    laundry_handlers.register_handlers(app)
    admin_handlers.register_handlers(app)

    set_commands(app)
    print("School messenger bot started.")
    app.bot.infinity_polling(skip_pending=True)


main()
