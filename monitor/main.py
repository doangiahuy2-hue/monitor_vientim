"""Điều phối monitor.

Cách dùng:
    python -m monitor.main --once          # kiểm tra 1 lần (GitHub Actions / Task Scheduler)
    python -m monitor.main --loop          # chạy liên tục, mỗi CHECK_INTERVAL_SEC giây
    python -m monitor.main --test-latest   # gửi thử bài mới nhất lên Telegram
    python -m monitor.main --detect        # chỉ kiểm tra có bài mới không (không gửi, không đổi state)
    python -m monitor.main --dry-run --test-latest   # chụp ảnh nhưng không gửi Telegram
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from urllib.parse import unquote, urlsplit

import httpx

from . import config, fetcher, state as state_mod
from .fetcher import Post
from .telegram import Telegram, TelegramError, esc, load_photo, shrink_for_photo

log = logging.getLogger("monitor")
ALERT_REPEAT_SEC = 6 * 3600
DOC_MAX_BYTES = 45 * 1024 * 1024
HISTORICAL_POST_AGE_SEC = 30 * 86400


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    try:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(config.LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def now_vn() -> datetime:
    return datetime.now(config.TZ)


# ---------------------------------------------------------------- gửi thông báo
def download_images(urls: list[str]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    with httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": config.USER_AGENT}) as c:
        for url in urls[: config.MAX_SOURCE_IMAGES]:
            for attempt in range(3):
                try:
                    r = c.get(url)
                    r.raise_for_status()
                    name, data = shrink_for_photo(r.content, url.rsplit("/", 1)[-1] or "image.jpg")
                    if data:
                        out.append((name, data))
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("Tải ảnh gốc %s lỗi (lần %d): %s", url, attempt + 1, e)
                    time.sleep(5 * (attempt + 1))
    return out


def download_documents(urls: list[str]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    with httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": config.USER_AGENT}) as c:
        for url in urls[: config.MAX_DOCUMENTS]:
            raw_name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "document.pdf"
            for attempt in range(3):
                try:
                    r = c.get(url)
                    r.raise_for_status()
                    data = r.content
                    if not data:
                        break
                    if len(data) > DOC_MAX_BYTES:
                        log.warning("Tài liệu %s vượt quá giới hạn 45MB (%d bytes), bỏ qua gửi file.", raw_name, len(data))
                        break
                    out.append((raw_name, data))
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("Tải tài liệu %s lỗi (lần %d): %s", url, attempt + 1, e)
                    time.sleep(5 * (attempt + 1))
    return out


def header_text(post: Post) -> str:
    lines = [
        "📢 <b>BÀI VIẾT MỚI – VIỆN TIM TP.HCM</b>",
        "",
    ]
    if post.category_name:
        lines.append(f"📂 Chuyên mục: <b>{esc(post.category_name)}</b>")
    lines.append(f"📝 <b>{esc(post.title)}</b>")
    if post.date_str:
        lines.append(f"🗓 {post.date_str}")
    lines.append(f"🔗 {esc(post.link)}")
    for doc_url in post.document_urls[: config.MAX_DOCUMENTS]:
        lines.append(f"📎 Tài liệu: {esc(doc_url)}")
    return "\n".join(lines)


def notify_post(tg: Telegram | None, post: Post) -> None:
    """Gửi 1 bài. Tin text được gửi trước để chắc chắn không bỏ sót; ảnh và tài liệu gửi sau."""
    from . import screenshot

    if tg:
        tg.send_message(header_text(post))  # lỗi ở đây → raise, bài sẽ được thử lại lần sau

    t = time.monotonic()
    shots = screenshot.capture(
        post.id,
        post.link,
        post.title,
        category_url=post.category_url,
        content_html=post.content_html,
        date_str=post.date_str,
        category_name=post.category_name,
    )
    log.info("Chụp xong bài %s trong %.0f s: %s | lỗi: %s",
             post.id, time.monotonic() - t, [p.name for p in shots.all], shots.errors)

    if not tg:
        return

    try:
        if shots.all:
            caption = f"🖼 Ảnh chụp website\n<b>{esc(post.title)}</b>"
            if not shots.category_has_post:
                caption += "\n⚠️ Trang chuyên mục chưa kịp cập nhật bài này (cache của website)."
            if shots.used_fallback:
                caption += "\nℹ️ Ảnh chi tiết bài viết được dựng từ API do website phản hồi chậm/lỗi."
            tg.send_photos([load_photo(p) for p in shots.all], caption=caption)
    except TelegramError as e:
        log.error("Gửi ảnh chụp lỗi: %s", e)
        shots.errors.append(f"Gửi ảnh: {e}")

    need_source = config.SEND_SOURCE_IMAGES or not shots.post
    if need_source and post.image_urls:
        imgs = download_images(post.image_urls)
        if imgs:
            try:
                tg.send_photos(imgs, caption=f"📄 Ảnh gốc trong bài ({len(imgs)})")
            except TelegramError as e:
                log.error("Gửi ảnh gốc lỗi: %s", e)

    if config.SEND_DOCUMENTS and post.document_urls:
        docs = download_documents(post.document_urls)
        for name, data in docs:
            try:
                tg.send_document(name, data, caption=f"📎 {esc(name)}")
            except TelegramError as e:
                log.error("Gửi tài liệu %s lỗi: %s", name, e)

    if shots.errors:
        tg.send_message("⚠️ Không chụp được đầy đủ ảnh màn hình (website phản hồi chậm/lỗi):\n"
                        + esc("\n".join(f"• {e[:200]}" for e in shots.errors)))


# ---------------------------------------------------------------- vòng kiểm tra
def handle_failure(tg: Telegram | None, st: state_mod.State, err: Exception) -> None:
    st.consecutive_failures += 1
    log.error("Kiểm tra thất bại (%d lần liên tiếp): %s", st.consecutive_failures, err)
    if not tg or st.consecutive_failures < config.ALERT_AFTER_FAILURES:
        return
    if st.alert_active and time.time() - st.last_alert_ts < ALERT_REPEAT_SEC:
        return
    try:
        tg.send_message(f"🚨 Monitor không truy cập được website ({st.consecutive_failures} lần liên tiếp).\n"
                        f"Lỗi: {esc(str(err)[:300])}")
        st.alert_active, st.last_alert_ts = True, time.time()
    except TelegramError as e:
        log.error("Không gửi được cảnh báo: %s", e)


def handle_recovery(tg: Telegram | None, st: state_mod.State) -> None:
    if st.alert_active and tg:
        try:
            tg.send_message("✅ Monitor đã truy cập lại website bình thường.")
        except TelegramError:
            return
    st.consecutive_failures, st.alert_active = 0, False


def _heartbeat_due(st: state_mod.State, now: datetime | None = None) -> bool:
    if config.HEARTBEAT_HOUR < 0:
        return False
    now = now or now_vn()
    return now.hour == config.HEARTBEAT_HOUR and st.last_heartbeat_date != now.strftime("%Y-%m-%d")


def _is_new_post(st: state_mod.State, post: Post, now: datetime | None = None) -> bool:
    if st.is_seen(post.id):
        return False
    if st.seen_ids and post.id < max(st.seen_ids) and post.date is not None:
        ref = now or now_vn()
        post_dt = post.date if post.date.tzinfo else post.date.replace(tzinfo=config.TZ)
        if (ref - post_dt).total_seconds() > HISTORICAL_POST_AGE_SEC:
            return False
    return True


def maybe_heartbeat(tg: Telegram | None, st: state_mod.State, posts: list[Post]) -> None:
    if not tg:
        return
    now = now_vn()
    if _heartbeat_due(st, now):
        latest = posts[0] if posts else None
        msg = "💓 Monitor Viện Tim vẫn hoạt động."
        if latest:
            msg += f"\nBài mới nhất: <b>{esc(latest.title)}</b> ({latest.date_str})"
        try:
            tg.send_message(msg)
            st.last_heartbeat_date = now.strftime("%Y-%m-%d")
        except TelegramError as e:
            log.error("Heartbeat lỗi: %s", e)


def run_once(tg: Telegram | None, test_latest: bool = False) -> int:
    """Trả về số bài đã gửi."""
    st = state_mod.load(config.STATE_FILE)
    sent = 0
    try:
        posts = fetcher.fetch_latest_posts()
    except Exception as e:  # noqa: BLE001
        handle_failure(tg, st, e)
        state_mod.save(st, config.STATE_FILE)
        return 0
    handle_recovery(tg, st)

    if test_latest and posts:
        notify_post(tg, posts[0])
        st.mark_seen(posts[0].id)
        sent = 1
    elif not st.initialized:
        for p in posts:
            st.mark_seen(p.id)
        st.initialized = True
        log.info("Lần chạy đầu: ghi nhận %d bài hiện có, không gửi.", len(posts))
        if tg:
            try:
                tg.send_message(
                    "🤖 Monitor <b>Thông báo – Viện Tim TP.HCM</b> đã khởi động.\n"
                    f"Bài mới nhất hiện tại: <b>{esc(posts[0].title)}</b>" if posts else "🤖 Monitor đã khởi động."
                )
            except TelegramError as e:
                log.error("Không gửi được tin khởi động: %s", e)
    else:
        now = now_vn()
        new_posts = [p for p in posts if _is_new_post(st, p, now)]
        # Gửi theo thứ tự cũ → mới, giới hạn số bài mỗi lượt để tránh spam.
        for p in sorted(new_posts, key=lambda x: x.id)[: config.MAX_POSTS_PER_RUN]:
            log.info("Bài mới: %s – %s", p.id, p.title)
            try:
                notify_post(tg, p)
            except TelegramError as e:
                log.error("Gửi Telegram lỗi, sẽ thử lại lượt sau: %s", e)
                break
            st.mark_seen(p.id)
            state_mod.save(st, config.STATE_FILE)  # lưu ngay sau mỗi bài
            sent += 1
        if not new_posts:
            log.info("Không có bài mới.")

    maybe_heartbeat(tg, st, posts)
    state_mod.save(st, config.STATE_FILE)
    return sent


def _write_gh_outputs(has_new: bool, should_run: bool) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"has_new={'true' if has_new else 'false'}\n")
            f.write(f"should_run={'true' if should_run else 'false'}\n")
    print(f"has_new={has_new}")
    print(f"should_run={should_run}")


def detect() -> bool:
    st = state_mod.load(config.STATE_FILE)
    posts = fetcher.fetch_latest_posts()
    now = now_vn()
    has_new = st.initialized and any(_is_new_post(st, p, now) for p in posts)
    should_run = (
        has_new
        or not st.initialized
        or st.alert_active
        or st.consecutive_failures > 0
        or _heartbeat_due(st, now)
    )
    _write_gh_outputs(has_new, should_run)
    return has_new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Monitor website – vientimtphcm.vn")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true", help="kiểm tra 1 lần (mặc định)")
    g.add_argument("--loop", action="store_true", help="chạy liên tục")
    g.add_argument("--detect", action="store_true", help="chỉ phát hiện bài mới")
    ap.add_argument("--test-latest", action="store_true", help="gửi thử bài mới nhất")
    ap.add_argument("--dry-run", action="store_true", help="không gửi Telegram")
    args = ap.parse_args(argv)
    setup_logging()

    if args.detect:
        try:
            detect()
        except Exception as e:  # noqa: BLE001
            log.error("Detect lỗi: %s", e)
            _write_gh_outputs(has_new=False, should_run=True)
        return 0

    tg = None if args.dry_run else Telegram()

    if not args.loop:
        run_once(tg, test_latest=args.test_latest)
        return 0

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    log.info("Chạy liên tục, chu kỳ %d s", config.CHECK_INTERVAL_SEC)
    first = True
    while not stop["flag"]:
        started = time.monotonic()
        try:
            run_once(tg, test_latest=args.test_latest and first)
        except Exception:  # noqa: BLE001 — không để daemon chết
            log.exception("Lỗi không mong muốn")
        first = False
        remaining = config.CHECK_INTERVAL_SEC - (time.monotonic() - started)
        while remaining > 0 and not stop["flag"]:
            time.sleep(min(1, remaining))
            remaining -= 1
    log.info("Đã dừng.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
