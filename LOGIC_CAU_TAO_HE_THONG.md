# LOGIC CẤU TẠO HỆ THỐNG TM-BOT (AI OPERATING SYSTEM)

Để đạt được tầm nhìn bạn mong muốn, các tính năng tiếp theo sẽ được xây dựng dựa trên 4 trụ cột logic chính:

---

## 1. LOGIC KẾT NỐI ĐA TẦNG (Goal & Priority Engine)
Thay vì coi mỗi task là một thực thể độc lập, TM sẽ kết nối chúng theo hình tháp:
*   **Tầm nhìn (Vision)** -> **Mục tiêu (Goal)** -> **Dự án (Project)** -> **Task**.
*   **Cơ chế**: Khi bạn tạo một task mới, TM sẽ hỏi: "Task này phục vụ mục tiêu [X] đúng không?". Nếu bạn làm quá nhiều task không liên quan đến mục tiêu lớn, TM sẽ cảnh báo: "Bạn đang dành 80% thời gian cho những việc không giúp bạn đạt được Vision".
*   **Priority Engine**: Ưu tiên không chỉ dựa trên Deadline, mà dựa trên **ROI (tác động)**. Việc nào giúp đạt Goal nhanh hơn sẽ được TM đẩy lên đầu.

---

## 2. LOGIC ĐIỀU PHỐI NĂNG LƯỢNG (Energy & Calendar Management)
TM sẽ không chỉ nhìn vào "thời gian trống" mà nhìn vào "năng lượng trống":
*   **Dữ liệu đầu vào**: Giấc ngủ (từ Health app), tâm trạng buổi sáng (từ chat), và lịch làm việc (Google Calendar).
*   **Cơ chế**: 
    *   Nếu sáng bạn báo "hơi mệt", TM sẽ tự động dời các task cần tư duy sâu (Deep Work) sang buổi chiều và xếp các việc nhẹ (Admin task) lên trước.
    *   **Time Blocking**: TM tự động chèn các block thời gian vào Calendar. Nếu bạn lỡ tay xóa một block, TM sẽ đề xuất lịch bù ngay lập tức.

---

## 3. LOGIC PHÂN TÍCH & DỰ BÁO (Analytics & Prediction)
TM đóng vai trò là một nhà phân tích dữ liệu cá nhân:
*   **Pattern Recognition**: TM theo dõi lịch sử trong 4 tuần. Nếu thấy chiều thứ 6 nào bạn cũng trì hoãn, TM sẽ kết luận: "Năng lượng của bạn cạn kiệt vào chiều thứ 6".
*   **Prediction**: Dựa trên tốc độ hoàn thành task hiện tại, TM sẽ dự báo: "Với tốc độ này, bạn sẽ trễ deadline dự án [X] khoảng 3 ngày. Bạn có muốn cắt bớt task phụ không?".

---

## 4. LOGIC HỆ THỐNG ĐA ĐẠI LÝ (Multi-Agent & Knowledge Graph)
Đây là "bộ não" cấp cao nhất của TM:
*   **Knowledge Graph**: TM không lưu văn bản rời rạc mà lưu mối quan hệ. Ví dụ: "Anh A là đối tác của dự án B, anh này thích nói chuyện thẳng thắn". Khi bạn chuẩn bị họp với anh A, TM sẽ nhắc nhở phong cách giao tiếp phù hợp.
*   **Multi-Agent**: 
    *   **Agent Planner**: Chuyên sắp xếp lịch.
    *   **Agent Coach**: Chuyên thúc đẩy và phản biện.
    *   **Agent Analyst**: Chuyên báo cáo dữ liệu.
    *   Các Agent này sẽ "họp" với nhau để đưa ra một câu trả lời thống nhất và tối ưu nhất cho bạn.

---

## 5. LOGIC CHỦ ĐỘNG (Proactive AI)
TM chuyển từ trạng thái "chờ lệnh" sang "khởi tạo":
*   **Cơ chế**: TM liên tục quét (scan) các dữ liệu từ Notion, Calendar và Mail. 
*   **Ví dụ**: TM thấy có một email mời họp vào lúc bạn đang có block Deep Work. TM sẽ chủ động nhắn: "Có lịch họp trùng vào giờ tập trung của bạn, tôi có nên đề xuất dời lịch họp không?".

---

**Kết luận**: Logic của TM-Bot là **biến dữ liệu tĩnh thành hành động động**. Mọi thông tin bạn đưa vào đều được kết nối để phục vụ một mục tiêu duy nhất: Tối ưu hóa con người bạn.
