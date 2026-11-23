"""Chụp ảnh trang chuyên mục + chi tiết bài bằng Playwright, tối ưu cho site tải chậm."""
from __future__ import annotations

import html
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
    used_fallback: bool = False

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


def _first_box(page, selectors: str | list[str]):
    if isinstance(selectors, str):
        selectors = [selectors]
    for sel in selectors:
        box = _box(page, sel)
        if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
            return box
    return None


def _clip_shot(page, top_sels: str | list[str], bottom_sels: list[str], out: Path, max_h: int) -> Path:
    """Chụp vùng từ đầu `top_sels` đến đáy lớn nhất trong `bottom_sels` (giới hạn max_h)."""
    top = _first_box(page, top_sels)
    if not top:
        raise RuntimeError(f"Không thấy phần tử {top_sels}")
    bottom = top["y"] + top["height"]
    found = [b for b in (_box(page, s) for s in bottom_sels) if b]
    if found:
        candidate = max(b["y"] + b["height"] for b in found)
        if candidate > top["y"] + 40:
            bottom = candidate
    pad = 16
    x = max(top["x"] - pad, 0)
    y = max(top["y"] - pad, 0)
    w = top["width"] + 2 * pad
    h = max(100, min(bottom - y + pad, max_h))
    page.screenshot(path=str(out), clip={"x": x, "y": y, "width": w, "height": h}, full_page=True)
    return out


def render_fallback_html(
    title: str,
    content_html: str,
    date_str: str = "",
    category_name: str = "",
    post_url: str = "",
) -> str:
    """Dựng HTML gọn nhẹ từ dữ liệu REST API khi trang gốc timeout/lỗi."""
    cat_html = (
        f'<div class="cat">{html.escape(category_name)}</div>'
        if category_name
        else ""
    )
    meta_parts = []
    if date_str:
        meta_parts.append(f"<span>🗓 {html.escape(date_str)}</span>")
    if post_url:
        meta_parts.append(f"<span>🔗 {html.escape(post_url)}</span>")
    meta_html = f'<div class="meta">{" &nbsp;•&nbsp; ".join(meta_parts)}</div>' if meta_parts else ""
    body_html = content_html.strip() or "<p><i>(Bài viết không có nội dung văn bản)</i></p>"

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<base href="{html.escape(config.BASE_URL)}/">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px;
    background: #f3f5f8; color: #1b2430;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    line-height: 1.6; font-size: 17px;
  }}
  #fallback-article {{
    max-width: 900px; margin: 0 auto; padding: 28px 32px;
    background: #ffffff; border-radius: 12px;
    border: 1px solid #e1e6ef;
  }}
  .cat {{
    display: inline-block; padding: 4px 10px; margin-bottom: 10px;
    background: #eaf2ff; color: #0b57d0; border-radius: 6px;
    font-size: 13px; font-weight: 600; text-transform: uppercase;
  }}
  h1 {{ margin: 0 0 10px; font-size: 25px; line-height: 1.35; color: #0f172a; }}
  .meta {{
    font-size: 13.5px; color: #526071; margin-bottom: 18px;
    padding-bottom: 12px; border-bottom: 1px solid #e7ebf2;
    word-break: break-word;
  }}
  .content img {{
    max-width: 100%; height: auto; display: block;
    margin: 12px auto; border-radius: 6px;
  }}
  .content figure {{ margin: 14px 0; text-align: center; }}
  .content figcaption {{ font-size: 14px; color: #526071; margin-top: 6px; }}
  .content table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  .content th, .content td {{ border: 1px solid #d5dce6; padding: 8px; text-align: left; }}
  .content a {{ color: #0b57d0; word-break: break-word; }}
</style>
</head>
<body>
  <article id="fallback-article">
    {cat_html}
    <h1>{html.escape(title)}</h1>
    {meta_html}
    <div class="content">{body_html}</div>
  </article>
</body>
</html>"""


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


def _run_with_retry(browser, job_name: str, job) -> tuple[list[Path], str | None]:
    """Chạy `job(page)` với retry trên cùng một Chromium browser."""
    last_err = None
    for attempt in range(1, config.SCREENSHOT_RETRIES + 1):
        ctx, page = _new_page(browser)
        try:
            return job(page), None
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("Chụp %s lỗi (lần %d/%d): %s", job_name, attempt, config.SCREENSHOT_RETRIES, e)
            if attempt < config.SCREENSHOT_RETRIES:
                time.sleep(min(5 * attempt, 15))
        finally:
            ctx.close()
    return [], f"Ảnh {job_name}: {str(last_err)[:300]}"


def _capture_fallback_post(
    browser,
    out_dir: Path,
    title: str,
    content_html: str,
    date_str: str,
    category_name: str,
    post_url: str,
) -> list[Path]:
    ctx, page = _new_page(browser)
    try:
        html_doc = render_fallback_html(
            title=title,
            content_html=content_html,
            date_str=date_str,
            category_name=category_name,
            post_url=post_url,
        )
        page.set_content(html_doc, wait_until="domcontentloaded")
        page.evaluate(EAGER_JS)
        _wait_images(page, "#fallback-article", timeout_s=30)
        path = _clip_shot(
            page,
            ["#fallback-article"],
            ["#fallback-article"],
            out_dir / "post.png",
            max_h=30000,
        )
        return split_tall_image(path)
    finally:
        ctx.close()


def capture(
    post_id: int,
    post_url: str,
    title: str,
    category_url: str | None = None,
    content_html: str = "",
    date_str: str = "",
    category_name: str = "",
) -> Shots:
    """Chụp 2 ảnh bằng 1 Chromium duy nhất (fallback render từ API nếu trang bài lỗi)."""
    from playwright.sync_api import sync_playwright

    out_dir = config.OUTPUT_DIR / str(post_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = Shots()
    default_cat_url = config.CATEGORY_URL if config.get_categories_param() else config.BLOG_URL
    target_cat_url = category_url.strip() if (category_url and category_url.strip()) else default_cat_url

    def category_job(page) -> list[Path]:
        found = False
        for rnd in range(1, config.CATEGORY_WAIT_ROUNDS + 1):
            url = target_cat_url
            if rnd > 1:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}_cb={int(time.time())}"
            _goto(page, url)
            if page.locator(f"#post-{post_id}").count() > 0:
                found = True
                break
            log.info("Trang chuyên mục chưa có bài %s (cache?), vòng %d", post_id, rnd)
            if rnd < config.CATEGORY_WAIT_ROUNDS:
                page.wait_for_timeout(config.CATEGORY_WAIT_SEC * 1000)
        shots.category_has_post = found
        first_card = f"#post-{post_id}" if found else ".post-list .blog-post"
        _wait_images(page, "#page-content")
        path = _clip_shot(
            page,
            ["#page-content .gridContainer > .row", "main", "#page-content", "body"],
            [first_card, "#recent-posts-2"],
            out_dir / "category.png",
            max_h=1500,
        )
        return split_tall_image(path)

    def post_job(page) -> list[Path]:
        _goto(page, post_url)
        try:
            page.wait_for_selector(".post-content-single, article, main", state="attached", timeout=15_000)
        except Exception:  # noqa: BLE001 — fallback xuống selector rộng hơn
            pass
        _wait_images(page, ".post-content-single", timeout_s=40)
        path = _clip_shot(
            page,
            [".content.post-page .gridContainer > .row", ".post-content-single", "article", "body"],
            [".post-content-inner", ".post-content-single .meta"],
            out_dir / "post.png",
            max_h=30000,
        )
        return split_tall_image(path)

    err1: str | None = None
    err2: str | None = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"],
            )
            try:
                shots.category, err1 = _run_with_retry(browser, "chuyên mục", category_job)
                shots.post, err2 = _run_with_retry(browser, "bài viết", post_job)
                if err2 and content_html.strip():
                    try:
                        shots.post = _capture_fallback_post(
                            browser,
                            out_dir,
                            title=title,
                            content_html=content_html,
                            date_str=date_str,
                            category_name=category_name,
                            post_url=post_url,
                        )
                        shots.used_fallback = True
                        log.info("Đã chụp ảnh dự phòng từ HTML API cho bài %s", post_id)
                        err2 = None
                    except Exception as fb_err:  # noqa: BLE001
                        log.warning("Chụp dự phòng bài %s lỗi: %s", post_id, fb_err)
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 — không làm chết tiến trình nếu Chromium lỗi khởi động
        log.error("Không khởi động được trình duyệt chụp bài %s: %s", post_id, e)
        shots.errors.append(f"Trình duyệt: {str(e)[:300]}")
        return shots

    shots.errors = [e for e in (err1, err2) if e]
    return shots

