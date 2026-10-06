"""Chụp ảnh trang chuyên mục + chi tiết bài bằng Playwright, tối ưu cho site tải chậm."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

# Request bị chặn: không cần cho ảnh chụp nhưng làm trang tải chậm/treo.
BLOCK_RESOURCE_TYPES = {"font", "media", "websocket", "manifest", "eventsource"}
BLOCK_URL_RE = re.compile(
    r"google-analytics|googletagmanager|gtag/js|doubleclick|googlesyndication|"
    r"facebook\.(net|com)|fbcdn|connect\.facebook|youtube\.com|ytimg|maps\.google|"
    r"maps\.googleapis|gravatar|tawk\.to|zalo\.me|sp\.zalo|subiz|hotjar|clarity\.ms|"
    r"/player-embed/|fonts\.googleapis|fonts\.gstatic|wp-emoji|emoji-release|"
    r"\.(woff2?|ttf|otf|eot|mp4|webm|mp3)(\?|$)",
    re.I,
)

# Ẩn phần tử nổi/gây nhiễu, tắt animation để chụp ổn định.
INJECT_CSS = """
*, *::before, *::after { animation: none !important; transition: none !important; }
.fixto-fixed, [class*="cookie"], [id*="cookie"], [class*="popup"], .back-to-top,
#tawkchat-container, .zalo-chat-widget, .fb_dialog, .fb-customerchat, iframe[src*="player-embed"]
{ display: none !important; }
html { scroll-behavior: auto !important; }
"""

# Bắt ảnh lazy-load tải ngay, không cần cuộn.
EAGER_JS = """
() => {
  document.querySelectorAll('img').forEach(img => {
    img.loading = 'eager';
    for (const a of ['data-src', 'data-lazy-src', 'data-original']) {
      const v = img.getAttribute(a); if (v && !img.src.includes(v)) img.src = v;
    }
    const ss = img.getAttribute('data-srcset'); if (ss) img.srcset = ss;
  });
}
"""

# Chờ ảnh nằm trong vùng chụp tải xong (hoặc lỗi), không chờ toàn trang.
WAIT_IMAGES_JS = """
(sel) => {
  const root = document.querySelector(sel) || document;
  const imgs = [...root.querySelectorAll('img')];
  return imgs.every(i => i.complete);
}
"""

PHOTO_CHUNK_MAX_H = 2600  # px CSS — dễ đọc trên điện thoại, an toàn với giới hạn Telegram
DEVICE_SCALE = 1.5


@dataclass
class Shots:
    category: list[Path] = field(default_factory=list)
    post: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    category_has_post: bool = True

    @property
    def all(self) -> list[Path]:
        return self.category + self.post


def _route(route):
    req = route.request
    if req.resource_type in BLOCK_RESOURCE_TYPES or BLOCK_URL_RE.search(req.url):
        return route.abort()
    return route.continue_()


def _new_page(browser):
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 900},
        device_scale_factor=DEVICE_SCALE,
        user_agent=config.USER_AGENT,
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh",
        java_script_enabled=True,
    )
    ctx.route("**/*", _route)
    page = ctx.new_page()
    page.set_default_timeout(config.PAGE_TIMEOUT_SEC * 1000)
    return ctx, page


def _goto(page, url: str) -> None:
    t = time.monotonic()
    # domcontentloaded: không chờ tracker/ảnh ngoài vùng chụp. HTML của site đã mất ~60 s.
    page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_SEC * 1000)
    page.add_style_tag(content=INJECT_CSS)
    page.evaluate(EAGER_JS)
    log.info("Đã tải %s trong %.1f s", url, time.monotonic() - t)


def _wait_images(page, selector: str, timeout_s: float = 25) -> None:
    try:
        page.wait_for_function(WAIT_IMAGES_JS, arg=selector, timeout=timeout_s * 1000, polling=500)
    except Exception:  # noqa: BLE001 — ảnh chậm thì vẫn chụp
        log.warning("Ảnh trong %s chưa tải hết sau %ss, vẫn chụp", selector, timeout_s)
    page.wait_for_timeout(500)


def _box(page, selector: str):
    loc = page.locator(selector).first
    if loc.count() == 0:
        return None
    return loc.bounding_box()


def _clip_shot(page, top_sel: str, bottom_sels: list[str], out: Path, max_h: int) -> Path:
    """Chụp vùng từ đầu `top_sel` đến đáy lớn nhất trong `bottom_sels` (giới hạn max_h)."""
    top = _box(page, top_sel)
    if not top:
        raise RuntimeError(f"Không thấy phần tử {top_sel}")
    bottom = top["y"] + top["height"]
    found = [b for b in (_box(page, s) for s in bottom_sels) if b]
    if found:
        bottom = max(b["y"] + b["height"] for b in found)
    pad = 16
    x = max(top["x"] - pad, 0)
    y = max(top["y"] - pad, 0)
    w = top["width"] + 2 * pad
    h = min(bottom - y + pad, max_h)
    page.screenshot(path=str(out), clip={"x": x, "y": y, "width": w, "height": h}, full_page=True)
    return out


def split_tall_image(path: Path, max_h: int = PHOTO_CHUNK_MAX_H) -> list[Path]:
    """Cắt ảnh quá cao thành nhiều phần hợp lệ cho sendPhoto, chuyển sang JPEG."""
    from PIL import Image

    img = Image.open(path).convert("RGB")
    w, h = img.size
    # Giới hạn Telegram: width + height <= 10000, tỉ lệ <= 20.
    limit = int(min(max_h * DEVICE_SCALE, 10000 - w - 1, 19 * w))
    parts: list[Path] = []
    n = max(1, -(-h // limit))
    step = -(-h // n)  # chia đều cho đẹp
    for i in range(n):
        box = (0, i * step, w, min(h, (i + 1) * step))
        part = img.crop(box)
        out = path.with_name(f"{path.stem}_{i + 1}.jpg") if n > 1 else path.with_suffix(".jpg")
        part.save(out, "JPEG", quality=88, optimize=True)
        parts.append(out)
    return parts


def _with_browser(job_name: str, job) -> tuple[list[Path], str | None]:
    """Chạy `job(browser)` với retry. Mỗi luồng có Playwright + Chromium riêng (sync API không chia sẻ được giữa luồng)."""
    from playwright.sync_api import sync_playwright

    last_err = None
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"],
        )
        try:
            for attempt in range(1, config.SCREENSHOT_RETRIES + 1):
                ctx, page = _new_page(browser)
                try:
                    return job(page), None
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    log.warning("Chụp %s lỗi (lần %d/%d): %s", job_name, attempt, config.SCREENSHOT_RETRIES, e)
                    if attempt < config.SCREENSHOT_RETRIES:
                        time.sleep(10 * attempt)
                finally:
                    ctx.close()
        finally:
            browser.close()
    return [], f"Ảnh {job_name}: {str(last_err)[:300]}"


def capture(post_id: int, post_url: str, title: str) -> Shots:
    """Chụp 2 ảnh song song. Không raise: lỗi ghi vào Shots.errors để luồng chính gửi fallback."""
    from concurrent.futures import ThreadPoolExecutor

    out_dir = config.OUTPUT_DIR / str(post_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = Shots()

    def category_job(page) -> list[Path]:
        found = False
        for rnd in range(1, config.CATEGORY_WAIT_ROUNDS + 1):
            _goto(page, config.CATEGORY_URL)
            if page.locator(f"#post-{post_id}").count() > 0:
                found = True
                break
            log.info("Trang chuyên mục chưa có bài %s (cache?), vòng %d", post_id, rnd)
            if rnd < config.CATEGORY_WAIT_ROUNDS:
                page.wait_for_timeout(60_000)
        shots.category_has_post = found
        first_card = f"#post-{post_id}" if found else ".post-list .blog-post"
        _wait_images(page, "#page-content")
        path = _clip_shot(
            page,
            "#page-content .gridContainer > .row",
            [first_card, "#recent-posts-2"],
            out_dir / "category.png",
            max_h=1500,
        )
        return split_tall_image(path)

    def post_job(page) -> list[Path]:
        _goto(page, post_url)
        page.wait_for_selector(".post-content-single", state="attached")
        _wait_images(page, ".post-content-single", timeout_s=40)
        path = _clip_shot(
            page,
            ".content.post-page .gridContainer > .row",
            [".post-content-inner", ".post-content-single .meta"],
            out_dir / "post.png",
            max_h=30000,
        )
        return split_tall_image(path)

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_cat = ex.submit(_with_browser, "chuyên mục", category_job)
        f_post = ex.submit(_with_browser, "bài viết", post_job)
        shots.category, err1 = f_cat.result()
        shots.post, err2 = f_post.result()
    shots.errors = [e for e in (err1, err2) if e]
    return shots

