from __future__ import annotations

from datetime import datetime
from pathlib import Path


class ActivityLogger:
    def __init__(self, log_path: str) -> None:
        self.path = Path(log_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, action: str, **fields: object) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        parts = [f"time={timestamp}", f"action={action}"]
        for key, value in fields.items():
            text = self._normalize(value)
            parts.append(f"{key}={text}")

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(" | ".join(parts) + "\n")

    def _normalize(self, value: object) -> str:
        if value is None:
            return "-"
        text = str(value).replace("\n", "\\n").strip()
        return text or "-"
