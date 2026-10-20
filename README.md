# Monitor Viện Tim TP.HCM (Thông báo & Tin tức sự kiện) → Telegram

Theo dõi 24/7 các bài viết và thông báo mới trên website Viện Tim TP.HCM:
- Chuyên mục **Thông báo**: https://vientimtphcm.vn/luu-tru/category/thong-bao
- Chuyên mục **Tin tức - Sự kiện**: https://vientimtphcm.vn/luu-tru/category/tin-tuc-su-kien
- Hoặc toàn bộ bài mới trên website (mục "Tin mới nhất" trên trang chủ: https://vientimtphcm.vn/blog)

Khi có bài mới, bot Telegram gửi:

1. Tin nhắn: tiêu đề, chuyên mục, ngày đăng, link (gửi **ngay**, không chờ chụp ảnh).
2. Ảnh chụp **trang chuyên mục** (tự động mở đúng chuyên mục chứa bài đó) + ảnh chụp **chi tiết bài viết** (bài dài tự cắt nhiều phần).
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

## 3. Các cách kích hoạt kiểm tra liền (On-Demand)

Bạn có thể yêu cầu bot kiểm tra website Viện Tim ngay lập tức qua các cách sau:

### Cách 1: Gửi lệnh qua Telegram (`/check`)
Khi chạy bot ở chế độ liên tục (`run_monitor.bat` hoặc `--loop`), bot sẽ tự động lắng nghe tin nhắn:
- `/check`: Quét website ngay lập tức và gửi bài mới (kèm ảnh), hoặc báo nếu chưa có bài mới.
- `/status`: Báo cáo tình trạng hoạt động, số bài đã theo dõi và bài mới nhất.
- `/help`: Xem danh sách lệnh.

### Cách 2: Nhấp đúp file `check_now.bat` (trên máy tính)
- Nhấp đúp vào file `check_now.bat` trong thư mục để bot chạy quét website ngay lập tức và gửi kết quả về Telegram.

### Cách 3: Nút "Run workflow" trên GitHub (Điện thoại hoặc Máy tính)
- Mở repo GitHub (trên web hoặc qua app **GitHub Mobile** trên iOS/Android) -> vào tab **Actions** -> chọn **Monitor Vien Tim** -> bấm **Run workflow** -> bot sẽ bật chạy ngay sau 5 giây.

### Cách 4: Tự động kích hoạt GitHub Actions khi gõ `/check` trên Telegram (Serverless Webhook)
- Xem hướng dẫn và mã nguồn có sẵn tại [`tools/telegram_webhook_worker.js`](tools/telegram_webhook_worker.js) để dùng Cloudflare Worker (0đ, không cần máy chủ) kết nối trực tiếp lệnh chat Telegram với GitHub Actions.

## Lệnh CLI
| Lệnh | Tác dụng |
|---|---|
| `--once` | Kiểm tra 1 lần |
| `--once --on-demand` | Kiểm tra 1 lần và luôn thông báo kết quả kể cả khi không có bài mới |
| `--loop` | Chạy liên tục mỗi `CHECK_INTERVAL_SEC` và lắng nghe lệnh Telegram (`/check`, `/status`) |
| `--test-latest` | Gửi bài mới nhất (kiểm tra cấu hình) |
| `--dry-run` | Không gửi Telegram, chỉ chụp ảnh vào `screenshots/` |
| `--detect` | Chỉ báo có bài mới hay không |

Lần chạy đầu chỉ ghi nhận các bài hiện có (không spam bài cũ). Cấu hình khác: xem `.env.example` và `monitor/config.py`.

## Kiểm thử
```powershell
.\.venv\Scripts\python -m pytest -q
```
