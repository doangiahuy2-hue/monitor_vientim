"""Client Telegram Bot API tối giản (httpx), có xử lý 429/5xx."""
from __future__ import annotations

import html
import json
import logging
import time
from pathlib import Path

import httpx

from . import config

log = logging.getLogger(__name__)

PHOTO_MAX_BYTES = 10 * 1024 * 1024


class TelegramError(RuntimeError):
    pass


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID
        if not self.token or not self.chat_id:
            raise TelegramError("Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.client = httpx.Client(timeout=httpx.Timeout(120, connect=20))

    def close(self) -> None:
        self.client.close()

    def _call(self, method: str, data: dict, files: dict | None = None, attempts: int = 5) -> dict:
        delay = 3
        for i in range(1, attempts + 1):
            try:
                r = self.client.post(f"{self.base}/{method}", data=data, files=files)
                body = r.json()
            except (httpx.HTTPError, ValueError) as e:
                body, r = {"ok": False, "description": str(e)}, None
            if body.get("ok"):
                return body["result"]
            retry_after = (body.get("parameters") or {}).get("retry_after")
            status = r.status_code if r is not None else 0
            if retry_after or status >= 500 or status == 0:
                wait = int(retry_after or delay)
                log.warning("Telegram %s lỗi (%s), thử lại sau %ss", method, body.get("description"), wait)
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue
            raise TelegramError(f"{method}: {body.get('description')}")
        raise TelegramError(f"{method}: thất bại sau {attempts} lần")

    def send_message(self, text: str, preview: bool = False) -> dict:
        return self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "false" if preview else "true",
            },
        )

    def send_photos(self, photos: list[tuple[str, bytes]], caption: str = "") -> None:
        """Gửi album ảnh (tự chia nhóm 10). photos: [(tên file, bytes)]."""
        photos = [p for p in photos if p[1]]
        for start in range(0, len(photos), 10):
            group = photos[start : start + 10]
            if len(group) == 1:
                name, data = group[0]
                self._call(
                    "sendPhoto",
                    {"chat_id": self.chat_id, "caption": caption if start == 0 else "", "parse_mode": "HTML"},
                    files={"photo": (name, data)},
                )
                continue
            media, files = [], {}
            for i, (name, data) in enumerate(group):
                key = f"f{i}"
                item = {"type": "photo", "media": f"attach://{key}"}
                if start == 0 and i == 0 and caption:
                    item.update(caption=caption, parse_mode="HTML")
                media.append(item)
                files[key] = (name, data)
            self._call("sendMediaGroup", {"chat_id": self.chat_id, "media": json.dumps(media)}, files=files)

    def send_document(self, name: str, data: bytes, caption: str = "") -> None:
        self._call(
            "sendDocument",
            {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"document": (name, data)},
        )


def load_photo(path: Path) -> tuple[str, bytes]:
    return path.name, path.read_bytes()


def shrink_for_photo(data: bytes, name: str) -> tuple[str, bytes]:
    """Đảm bảo ảnh hợp lệ cho sendPhoto (<10 MB, w+h <= 10000)."""
    from io import BytesIO

    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
    except Exception:  # noqa: BLE001
        return name, b""
    img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, 9000 / (w + h), 4096 / max(w, h))
    if scale < 1.0 or len(data) > PHOTO_MAX_BYTES or not name.lower().endswith((".jpg", ".jpeg")):
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=90, optimize=True)
        data = buf.getvalue()
        name = Path(name).stem + ".jpg"
    return name, data
