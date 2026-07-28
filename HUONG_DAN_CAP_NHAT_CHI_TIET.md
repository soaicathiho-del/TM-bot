# HƯỚNG DẪN CẬP NHẬT TM-BOT REWRITE (DÀNH CHO USER)

Chào bạn, đây là bản hướng dẫn chi tiết để bạn tự tin nâng cấp TM-Bot lên cấu trúc mới mà không lo bị lỗi. Mọi thứ đã được tối ưu để giữ nguyên "linh hồn" cũ nhưng mạnh mẽ hơn ở bên trong.

---

## 1. LIÊT KÊ THAY ĐỔI TRONG FILE CODE (GHI ĐÈ)

Tôi không bỏ bớt công đoạn nào, mà thực tế là đã **hợp nhất và làm sạch** code để chạy mượt hơn trên Railway:

*   **config.py**: 
    *   *Thay đổi*: Thêm biến `RULES_POINT_DATABASE_ID` và đồng bộ hóa cách gọi `Config.VARIABLE` thay vì dùng biến rời rạc.
    *   *Mục đích*: Giúp code dễ đọc và tránh lỗi thiếu biến môi trường.
*   **notion_service.py**:
    *   *Thay đổi*: Thêm hàm `update_status_note` và tối ưu lại hàm `get_today_tasks` để lấy đúng múi giờ Việt Nam (GMT+7).
    *   *Mục đích*: Để bot có thể ghi chú tâm trạng/sức khỏe của bạn vào Notion.
*   **gemini_service.py**:
    *   *Thay đổi*: Chuyển sang sử dụng `google-generativeai` bản mới nhất, hỗ trợ xử lý bất đồng bộ (async).
    *   *Mục đích*: Tăng tốc độ phản hồi của bot.
*   **telegram_handlers.py**:
    *   *Thay đổi*: Đây là file thay đổi nhiều nhất. Tích hợp bộ lọc **Intent Detection** (nhận diện ý định) và **Context Buffer** (bộ nhớ ngắn hạn).
    *   *Mục đích*: Giúp TM hiểu bạn đang nói gì mà không cần bạn phải gõ đúng câu lệnh `/done`.
*   **app.py**:
    *   *Thay đổi*: Làm sạch các đoạn code thừa, tập trung vào việc nhận tin nhắn từ Telegram và chuyển cho bộ xử lý.
*   **Procfile**:
    *   *Thay đổi*: Đảm bảo chạy đúng file `telegram_handlers.py` dưới dạng worker ổn định trên Railway.

---

## 2. GIẢI THÍCH TÍNH NĂNG MỚI

### A. Context Buffer (Bộ nhớ thông minh theo ngày)
*   **Trong ngày**: TM sẽ nhớ **toàn bộ nội dung hội thoại** từ sáng đến tối của ngày hôm đó. Bạn có thể nói chuyện thoải mái mà không lo TM bị "mất trí nhớ" giữa chừng.
*   **Qua ngày mới**: TM sẽ lấy dữ liệu từ **Bản tóm tắt ngày hôm trước** (đã được bạn duyệt) để làm ngữ cảnh khởi đầu.
*   **Cách duyệt bộ nhớ**: Từ sau 21:00, TM sẽ tự động đưa ra bản tóm tắt ngày. Để tránh làm phiền, TM sẽ **chỉ gửi report 1 lần**. Nếu sau đó bạn có thêm ít nhất 3 tin nhắn mới hoặc có dữ liệu quan trọng, TM mới cập nhật bản tóm tắt mới.
*   **Giãn cách nhắc việc**: TM sẽ không nhắc việc liên tục. Sau mỗi lần nhắc task, TM sẽ "im lặng" về vấn đề công việc trong ít nhất **1 tiếng** (trừ khi bạn chủ động hỏi) để bạn tập trung làm việc mà không bị áp lực.

### B. Adaptive Rules (Chiến thuật Push-Probe-Pivot)
*   **Cách hoạt động**: TM tuân theo quy tắc:
    1.  **Push**: Thúc đẩy bạn làm việc.
    2.  **Probe**: Nếu bạn vẫn chưa làm, TM sẽ hỏi sâu xem vấn đề nằm ở đâu (kỹ năng, tâm trạng hay thời gian?).
    3.  **Pivot**: Nếu thấy bạn thực sự không ổn, TM sẽ chủ động khuyên bạn nghỉ ngơi hoặc đổi task.
*   **Lợi ích**: TM trở nên "người" hơn, không còn là một cái máy nhắc việc khô khan.

---

## 3. CÁC BƯỚC CẬP NHẬT TRÊN GITHUB (CẦM TAY CHỈ VIỆC)

### Bước 1: Chuẩn bị thư mục (Quan trọng)
1.  Vào Repository của bạn trên GitHub.
2.  Nhấn **Add file** -> **Create new file**.
3.  Gõ tên: `prompts/system/.gitkeep` rồi nhấn **Commit changes**.
4.  Lặp lại tương tự để tạo:
    *   `prompts/tasks/.gitkeep`
    *   `data/.gitkeep`
    *   `evals/.gitkeep`

### Bước 2: Xóa các file cũ (Dọn dẹp)
Nhấn vào từng file sau trên GitHub, chọn biểu tượng thùng rác để xóa:
*   `system_prompt.md`
*   `user_profile.md`
*   `history.json` (nếu có ở ngoài)
*   `memories.json` (nếu có ở ngoài)

### Bước 3: Cập nhật nội dung (Copy & Paste)
Mở file ZIP tôi gửi, mở từng file tương ứng và thực hiện:

1.  **Tạo mới file trong thư mục**:
    *   Vào `prompts/system/`, tạo file `tm-core.md` -> Paste nội dung từ ZIP.
    *   Vào `prompts/system/`, tạo file `tm-adaptive-rules.md` -> Paste nội dung từ ZIP.
    *   Vào `prompts/tasks/`, tạo file `morning.md`, `focus.md`, `sleep.md` -> Paste nội dung tương ứng.
    *   Vào `data/`, tạo file `user-profile.md` -> Paste nội dung từ ZIP.
    *   Vào `evals/`, tạo file `test-cases.md` -> Paste nội dung từ ZIP.

2.  **Ghi đè file code cũ**:
    *   Mở `config.py` trên GitHub -> Nhấn biểu tượng Edit (cây bút) -> Ctrl+A xóa hết -> Paste code mới từ ZIP -> Commit.
    *   Làm tương tự cho: `notion_service.py`, `gemini_service.py`, `telegram_handlers.py`, `app.py`, `telegram_service.py`, `Procfile`, `.github/workflows/daily.yml`.

### Bước 4: Kiểm tra trên Railway
1.  Vào Railway, đợi bot Deploy xong (hiển thị màu xanh).
2.  Mở Telegram, gõ `/start` để xem TM "tái sinh".

---
**Lưu ý**: Đừng quên thêm cột **Status Note** trên Notion trước khi test nhé! Chúc bạn thành công!
