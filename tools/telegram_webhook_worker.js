/**
 * Cloudflare Worker (Miễn phí 100%) - Cầu nối lệnh Telegram -> GitHub Actions
 *
 * Cách hoạt động:
 * 1. Bạn nhắn /check hoặc /status trong bot Telegram trên điện thoại.
 * 2. Telegram gửi webhook tới Worker này (chạy tức thì trong 0.1 giây).
 * 3. Worker gọi GitHub Actions API (workflow_dispatch) để kích hoạt bot chạy kiểm tra liền!
 *
 * CÁCH CÀI ĐẶT (chỉ mất 2 phút, không cần thẻ tín dụng):
 * 1. Đăng ký tài khoản miễn phí tại: https://dash.cloudflare.com
 * 2. Vào "Workers & Pages" -> "Create application" -> "Create Worker".
 * 3. Dán toàn bộ mã nguồn bên dưới vào và bấm "Deploy".
 * 4. Vào tab "Settings" -> "Variables" của Worker, thêm 3 biến:
 *    - GITHUB_TOKEN: Personal Access Token của GitHub (tạo tại github.com/settings/tokens, quyền 'repo' hoặc 'workflow')
 *    - GITHUB_REPO: "doangiahuy2-hue/monitor_vientim"
 *    - TELEGRAM_BOT_TOKEN: Token của bot Telegram
 * 5. Đăng ký Webhook cho Telegram bằng cách mở link trình duyệt:
 *    https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=<URL_WORKER_CUA_BAN>
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("OK", { status: 200 });
    }

    try {
      const update = await request.json();
      const message = update.message || update.edited_message;
      if (!message || !message.text) {
        return new Response("OK", { status: 200 });
      }

      const text = message.text.trim();
      const chatId = message.chat.id;

      if (text.startsWith("/check") || text.startsWith("/kiemtra")) {
        // 1. Phản hồi ngay cho người dùng trên Telegram
        await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            chat_id: chatId,
            text: "⏳ <b>Đã nhận lệnh /check!</b> Đang kích hoạt GitHub Actions quét website Viện Tim ngay lập tức...",
            parse_mode: "HTML",
          }),
        });

        // 2. Kích hoạt GitHub Actions workflow_dispatch
        const ghRes = await fetch(
          `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/monitor.yml/dispatches`,
          {
            method: "POST",
            headers: {
              "User-Agent": "Telegram-Webhook-Worker",
              "Accept": "application/vnd.github.v3+json",
              "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ ref: "main" }),
          }
        );

        if (!ghRes.ok) {
          const errText = await ghRes.text();
          await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              chat_id: chatId,
              text: `⚠️ Kích hoạt GitHub Actions thất bại: ${errText.substring(0, 200)}`,
            }),
          });
        }
      } else if (text.startsWith("/help") || text.startsWith("/start")) {
        await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            chat_id: chatId,
            text: "🤖 <b>Bot Monitor Viện Tim TP.HCM</b>\n\n• Gửi <code>/check</code> để kích hoạt bot quét website Viện Tim ngay lập tức!\n• Bot tự động kiểm tra định kỳ mỗi 5 phút 24/7.",
            parse_mode: "HTML",
          }),
        });
      }
    } catch (e) {
      console.error(e);
    }

    return new Response("OK", { status: 200 });
  },
};
