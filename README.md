# Monitor "Thông báo" – Viện Tim TP.HCM → Telegram

Theo dõi 24/7 https://vientimtphcm.vn/luu-tru/category/thong-bao. Khi có bài mới, bot Telegram gửi:

1. Tin nhắn: tiêu đề, ngày đăng, link (gửi **ngay**, không chờ chụp ảnh).
2. Ảnh chụp **trang chuyên mục** + ảnh chụp **chi tiết bài viết** (bài dài tự cắt nhiều phần).
3. Ảnh **văn bản gốc** trong bài (đọc rõ hơn) – có thể tắt bằng `SEND_SOURCE_IMAGES=0`.

## Tối ưu cho website chậm
- Trang HTML mất ~60 s, nhưng REST API của WordPress chỉ ~0,7 s → **phát hiện bằng API**, fallback RSS.
- Chỉ mở Chromium khi thật sự có bài mới.
- Chặn font, video, tracker, iframe player; `wait_until=domcontentloaded`; ép ảnh lazy-load tải ngay; chỉ chờ ảnh trong vùng chụp.
- Timeout 150 s, retry 3 lần; nếu trang chuyên mục còn cache cũ thì chờ và tải lại.
- Chụp lỗi vẫn gửi ảnh gốc + cảnh báo → không bỏ sót bài.
- Cảnh báo khi website lỗi ≥ 3 lần liên tiếp, báo khi phục hồi; heartbeat hằng ngày (tuỳ chọn).

## 1. Tạo bot Telegram (miễn phí)
1. Chat với [@BotFather](https://t.me/BotFather) → `/newbot` → lấy **token**.
2. Nhắn 1 tin bất kỳ cho bot (hoặc thêm bot vào group / làm admin channel).
3. Mở `https://api.telegram.org/bot<TOKEN>/getUpdates` → lấy `chat.id` (group/channel có dạng `-100...`).

## 2A. Chạy 24/7 miễn phí bằng GitHub Actions (khuyên dùng)
1. Tạo repo **public** mới trên GitHub (repo public: phút chạy Actions không giới hạn), push thư mục này lên.
2. `Settings → Secrets and variables → Actions` → thêm `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. `Actions → Monitor Vien Tim → Run workflow` (tick *Gửi thử bài mới nhất* để kiểm tra).
4. Sau đó workflow tự chạy mỗi 5 phút. `state.json` được commit lại để nhớ bài đã gửi.

> Token nằm trong Secrets nên repo public vẫn an toàn. Heartbeat 08:00 hằng ngày tạo commit giúp GitHub không tự tắt lịch chạy sau 60 ngày.

## 2B. Chạy trên máy Windows / VPS
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
copy .env.example .env   # rồi điền token + chat id
.\.venv\Scripts\python -m monitor.main --test-latest   # gửi thử
.\run_monitor.bat                                      # chạy liên tục
```
Tự khởi động cùng Windows: Task Scheduler → *Create Task* → Trigger *At startup* → Action chạy `run_monitor.bat`, tick *Run whether user is logged on or not*.

Linux (VPS Oracle Always Free…): `python -m monitor.main --loop` qua systemd hoặc cron `*/5 * * * * python -m monitor.main --once`.

## Lệnh
| Lệnh | Tác dụng |
|---|---|
| `--once` | Kiểm tra 1 lần |
| `--loop` | Chạy liên tục mỗi `CHECK_INTERVAL_SEC` |
| `--test-latest` | Gửi bài mới nhất (kiểm tra cấu hình) |
| `--dry-run` | Không gửi Telegram, chỉ chụp ảnh vào `screenshots/` |
| `--detect` | Chỉ báo có bài mới hay không |

Lần chạy đầu chỉ ghi nhận các bài hiện có (không spam bài cũ). Cấu hình khác: xem `.env.example` và `monitor/config.py`.

## Kiểm thử
```powershell
.\.venv\Scripts\python -m pytest -q
```
