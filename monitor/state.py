"""Lưu trạng thái (ID bài đã gửi, bộ đếm lỗi...) vào JSON, ghi atomic."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_IDS = 500


@dataclass
class State:
    seen_ids: list[int] = field(default_factory=list)
    initialized: bool = False
    consecutive_failures: int = 0
    last_alert_ts: float = 0.0
    alert_active: bool = False
    last_heartbeat_date: str = ""

    def is_seen(self, post_id: int) -> bool:
        return post_id in self.seen_ids

    def mark_seen(self, post_id: int) -> None:
        if post_id not in self.seen_ids:
            self.seen_ids.append(post_id)
        self.seen_ids = sorted(set(self.seen_ids))[-MAX_IDS:]


def load(path: Path) -> State:
    if not path.exists():
        return State()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return State()
    known = {k: v for k, v in data.items() if k in State.__dataclass_fields__}
    return State(**known)


def save(state: State, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
