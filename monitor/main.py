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
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler

import httpx

from . import config, fetcher, state as state_mod
from .fetcher import Post
from .telegram import Telegram, TelegramError, esc, load_photo, shrink_for_photo

log = logging.getLogger("monitor")
ALERT_REPEAT_SEC = 6 * 3600
_check_lock = threading.Lock()


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
    return "\n".join(lines)


def notify_post(tg: Telegram | None, post: Post) -> None:
    """Gửi 1 bài. Tin text được gửi trước để chắc chắn không bỏ sót; ảnh gửi sau."""
    from . import screenshot

    if tg:
        tg.send_message(header_text(post))  # lỗi ở đây → raise, bài sẽ được thử lại lần sau

    t = time.monotonic()
    shots = screenshot.capture(post.id, post.link, post.title, category_url=post.category_url)
    log.info("Chụp xong bài %s trong %.0f s: %s | lỗi: %s",
             post.id, time.monotonic() - t, [p.name for p in shots.all], shots.errors)

    if not tg:
        return

    try:
        if shots.all:
            caption = f"🖼 Ảnh chụp website\n<b>{esc(post.title)}</b>"
            if not shots.category_has_post:
                caption += "\n⚠️ Trang chuyên mục chưa kịp cập nhật bài này (cache của website)."
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


def maybe_heartbeat(tg: Telegram | None, st: state_mod.State, posts: list[Post]) -> None:
    if not tg or config.HEARTBEAT_HOUR < 0:
        return
    now = now_vn()
    today = now.strftime("%Y-%m-%d")
    if now.hour == config.HEARTBEAT_HOUR and st.last_heartbeat_date != today:
        latest = posts[0] if posts else None
        msg = "💓 Monitor Viện Tim vẫn hoạt động."
        if latest:
            msg += f"\nBài mới nhất: <b>{esc(latest.title)}</b> ({latest.date_str})"
        try:
            tg.send_message(msg)
            st.last_heartbeat_date = today
        except TelegramError as e:
            log.error("Heartbeat lỗi: %s", e)


def run_once(tg: Telegram | None, test_latest: bool = False, on_demand: bool = False) -> int:
    if not _check_lock.acquire(blocking=False):
        log.info('Quá trình kiểm tra khác đang chạy, bỏ qua lần gọi này.')
        if on_demand and tg:
            try:
                tg.send_message('⏳ Bot đang quét website, kết quả sẽ có sau ít phút...')
            except Exception:
                pass
        return 0
    try:
        return _do_run_once(tg, test_latest=test_latest, on_demand=on_demand)
    finally:
        _check_lock.release()


def _do_run_once(tg: Telegram | None, test_latest: bool = False, on_demand: bool = False) -> int:
    """Trả về số bài đã gửi."""
    st = state_mod.load(config.STATE_FILE)
    sent = 0
    try:
        posts = fetcher.fetch_latest_posts()
    except Exception as e:  # noqa: BLE001
        handle_failure(tg, st, e)
        state_mod.save(st, config.STATE_FILE)
        if on_demand and tg:
            try:
                tg.send_message(f"❌ <b>Kiểm tra thất bại:</b> Không kết nối được website.\nLỗi: {esc(str(e)[:200])}")
            except Exception:
                pass
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
        new_posts = [p for p in posts if not st.is_seen(p.id)]
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
            if on_demand and tg:
                latest = posts[0] if posts else None
                msg = "✅ <b>Kiểm tra hoàn tất:</b> Hiện chưa có bài viết mới."
                if latest:
                    msg += f"\n📌 Bài mới nhất: <b>{esc(latest.title)}</b> ({latest.date_str})"
                    if latest.category_name:
                        msg += f"\n📂 Chuyên mục: <b>{esc(latest.category_name)}</b>"
                    msg += f"\n🔗 {esc(latest.link)}"
                try:
                    tg.send_message(msg)
                except Exception as e:
                    log.error("Lỗi gửi tin on_demand: %s", e)

    maybe_heartbeat(tg, st, posts)
    state_mod.save(st, config.STATE_FILE)
    return sent


def detect() -> bool:
    st = state_mod.load(config.STATE_FILE)
    posts = fetcher.fetch_latest_posts()
    has_new = st.initialized and any(not st.is_seen(p.id) for p in posts)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"has_new={'true' if has_new else 'false'}\n")
    print(f"has_new={has_new}")
    return has_new


def poll_commands(tg: Telegram, stop: dict) -> None:
    """Lắng nghe các lệnh từ Telegram (/check, /status, /help) khi chạy chế độ --loop."""
    tg.set_commands()
    last_update_id = 0
    log.info("Bắt đầu lắng nghe lệnh Telegram (/check, /status, /help)...")

    while not stop["flag"]:
        try:
            updates = tg.get_updates(offset=last_update_id + 1, timeout=5)
            for u in updates:
                uid = u.get("update_id", 0)
                if uid > last_update_id:
                    last_update_id = uid
                msg = u.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id", ""))

                # Chỉ xử lý lệnh từ chat_id đã cấu hình
                configured_id = str(config.TELEGRAM_CHAT_ID or "")
                if configured_id and chat_id != configured_id and not configured_id.endswith(chat_id):
                    continue

                if not text:
                    continue

                cmd = text.split()[0].lower()
                if cmd in ("/check", "/check@vientim_bot", "/kiemtra"):
                    log.info("Nhận lệnh /check từ Telegram")
                    try:
                        tg.send_message("⏳ <b>Đang kiểm tra website Viện Tim ngay lập tức...</b>")
                    except Exception:
                        pass
                    run_once(tg, on_demand=True)
                elif cmd in ("/status", "/trangthai"):
                    log.info("Nhận lệnh /status từ Telegram")
                    st = state_mod.load(config.STATE_FILE)
                    posts = []
                    try:
                        posts = fetcher.fetch_latest_posts()
                    except Exception:
                        pass
                    latest_str = f"[{posts[0].id}] {esc(posts[0].title)}" if posts else "Chưa rõ"
                    cats_display = config.CATEGORIES or "Tất cả"
                    status_text = (
                        "🤖 <b>Trạng thái Monitor Viện Tim</b>\n"
                        "• <b>Tình trạng:</b> Đang chạy 24/7\n"
                        f"• <b>Chuyên mục theo dõi:</b> {esc(cats_display)}\n"
                        f"• <b>Số bài đã lưu:</b> {len(st.seen_ids)} bài\n"
                        f"• <b>Bài mới nhất hiện tại:</b> {latest_str}\n"
                        f"• <b>Thời gian hiện tại:</b> {now_vn().strftime('%d/%m/%Y %H:%M:%S')}\n"
                    )
                    try:
                        tg.send_message(status_text)
                    except Exception:
                        pass
                elif cmd in ("/help", "/start"):
                    help_text = (
                        "🤖 <b>Bot Monitor Viện Tim TP.HCM</b>\n\n"
                        "Các lệnh hỗ trợ:\n"
                        "• <code>/check</code> - Quét website Viện Tim và kiểm tra bài mới ngay lập tức\n"
                        "• <code>/status</code> - Xem tình trạng hoạt động của bot\n"
                        "• <code>/help</code> - Xem hướng dẫn các lệnh\n\n"
                        f"<i>Bot tự động kiểm tra định kỳ mỗi {config.CHECK_INTERVAL_SEC}s và gửi báo cáo khi có bài mới.</i>"
                    )
                    try:
                        tg.send_message(help_text)
                    except Exception:
                        pass
        except Exception as e:
            log.debug("Lỗi lắng nghe lệnh Telegram: %s", e)
            time.sleep(2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Monitor chuyên mục Thông báo – vientimtphcm.vn")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true", help="kiểm tra 1 lần (mặc định)")
    g.add_argument("--loop", action="store_true", help="chạy liên tục")
    g.add_argument("--detect", action="store_true", help="chỉ phát hiện bài mới")
    ap.add_argument("--test-latest", action="store_true", help="gửi thử bài mới nhất")
    ap.add_argument("--on-demand", action="store_true", help="báo kết quả ngay cả khi không có bài mới")
    ap.add_argument("--dry-run", action="store_true", help="không gửi Telegram")
    args = ap.parse_args(argv)
    setup_logging()

    if args.detect:
        try:
            detect()
        except Exception as e:  # noqa: BLE001
            log.error("Detect lỗi: %s", e)
            print("has_new=false")
        return 0

    tg = None if args.dry_run else Telegram()

    if not args.loop:
        run_once(tg, test_latest=args.test_latest, on_demand=args.on_demand)
        return 0

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    log.info("Chạy liên tục, chu kỳ %d s", config.CHECK_INTERVAL_SEC)
    if tg:
        threading.Thread(target=poll_commands, args=(tg, stop), daemon=True).start()
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
