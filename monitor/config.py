"""Cấu hình trung tâm. Mọi giá trị đều có thể ghi đè bằng biến môi trường / file .env."""
from __future__ import annotations

import os
from pathlib import Path
from datetime import timedelta, timezone

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv là tuỳ chọn
    pass

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name, "1" if default else "0").lower()
    return v in ("1", "true", "yes", "on")


# --- Website ---
BASE_URL = _env("SITE_BASE_URL", "https://vientimtphcm.vn")
CATEGORY_ID = _env_int("CATEGORY_ID", 17)
CATEGORY_URL = _env("CATEGORY_URL", f"{BASE_URL}/luu-tru/category/thong-bao")
API_URL = f"{BASE_URL}/wp-json/wp/v2/posts"
RSS_URL = f"{CATEGORY_URL}/feed"
TZ = timezone(timedelta(hours=7), "ICT")  # Việt Nam không có giờ mùa hè

# --- Telegram ---
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")

# --- Hành vi ---
CHECK_INTERVAL_SEC = _env_int("CHECK_INTERVAL_SEC", 300)  # chế độ --loop
FETCH_TIMEOUT_SEC = _env_int("FETCH_TIMEOUT_SEC", 45)
PAGE_TIMEOUT_SEC = _env_int("PAGE_TIMEOUT_SEC", 240)  # site rất chậm (~60 s chỉ riêng HTML)
SCREENSHOT_RETRIES = _env_int("SCREENSHOT_RETRIES", 3)
CATEGORY_WAIT_ROUNDS = _env_int("CATEGORY_WAIT_ROUNDS", 3)  # chờ cache trang chuyên mục cập nhật
SEND_SOURCE_IMAGES = _env_bool("SEND_SOURCE_IMAGES", True)  # gửi kèm ảnh scan gốc trong bài
MAX_SOURCE_IMAGES = _env_int("MAX_SOURCE_IMAGES", 8)
HEARTBEAT_HOUR = _env_int("HEARTBEAT_HOUR", -1)  # -1 = tắt; vd 8 = gửi lúc 08:xx giờ VN
ALERT_AFTER_FAILURES = _env_int("ALERT_AFTER_FAILURES", 3)
MAX_POSTS_PER_RUN = _env_int("MAX_POSTS_PER_RUN", 5)

# --- File ---
STATE_FILE = Path(_env("STATE_FILE", str(ROOT / "state.json")))
OUTPUT_DIR = Path(_env("OUTPUT_DIR", str(ROOT / "screenshots")))
LOG_FILE = Path(_env("LOG_FILE", str(ROOT / "logs" / "monitor.log")))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
