# 📋 QUY TẮC DEV — F:\DEV

> File này ghi lại các quy tắc bắt buộc áp dụng cho **tất cả project** trong thư mục `F:\DEV`.
> AI assistant phải đọc và tuân thủ các quy tắc này trước khi thực hiện bất kỳ thay đổi nào.

---

## 🔢 Versioning (Phiên bản)

- **Luôn tự tăng phiên bản** sau mỗi lần sửa code (dù nhỏ).
  - Format: `MAJOR.MINOR.PATCH` (ví dụ: `1.0.0` → `1.0.1`)
  - Tăng `PATCH` khi sửa lỗi hoặc thay đổi nhỏ.
  - Tăng `MINOR` khi thêm tính năng mới nhưng vẫn tương thích ngược.
  - Tăng `MAJOR` khi có thay đổi lớn, breaking change.
- Với **WordPress plugin**: cập nhật version ở header file PHP chính (`Version: x.x.x`) và các file liên quan (`readme.txt`, `package.json` nếu có).

---

## 🔁 Git (Version Control)

- **Tự động commit và push** lên Git sau mỗi lần hoàn thành một thay đổi.
- Commit message phải rõ ràng, theo format:
  ```
  [type]: mô tả ngắn gọn bằng tiếng Việt hoặc tiếng Anh
  ```
  Các `type` phổ biến:
  - `feat` — thêm tính năng mới
  - `fix` — sửa lỗi
  - `ui` — thay đổi giao diện
  - `refactor` — cải thiện code không ảnh hưởng chức năng
  - `docs` — cập nhật tài liệu
  - `chore` — công việc lặt vặt (update dep, config...)

  Ví dụ: `feat: thêm nút File Manager lên đầu card`

- Push lên nhánh `main` (hoặc nhánh hiện tại) sau mỗi commit.

---

## 🗂️ Cấu trúc Project

- Mỗi project nằm trong thư mục riêng tại `F:\DEV\<tên-project>`.
- Mỗi project phải có file `README.md` mô tả mục đích và cách sử dụng.

---

## ✅ Checklist trước khi commit

- [ ] Đã tăng version (nếu có thay đổi code)?
- [ ] Code không có lỗi cú pháp?
- [ ] Giao diện hiển thị đúng trên các kích thước màn hình phổ biến?
- [ ] Đã rà soát sạch sẽ (View-Source Ready): không có comment đàm thoại, từ ngữ nhạy cảm (hack, chữa cháy, tạm bợ) hay log nội bộ?
- [ ] Commit message rõ ràng và đúng format?
- [ ] Đã push lên remote Git?

---

## 🗣️ Giao tiếp

- **Luôn giao tiếp bằng tiếng Việt** trong tất cả các câu trả lời, kế hoạch triển khai (implementation plan), task checklist và báo cáo kết quả (walkthrough) gửi cho người dùng.

---

## 🧩 Quy Tắc Xây Dựng Giao Diện WordPress Flatsome (UX Elements & VBC Architecture)

- **TUYỆT ĐỐI CẤM (HARD BAN) dồn toàn bộ nội dung HTML thô vào một thẻ Text / HTML block đơn lẻ**:
  - Khi lưu raw HTML `<div>...</div>` vào `post_content` của Page hoặc UX Block, trình dựng Flatsome UX Builder sẽ không hiểu cấu trúc phân cấp và tự động gói toàn bộ cục HTML đó thành **MỘT phần tử Text (`[text_box]`) duy nhất**.
  - Hậu quả: Cây thư mục bên trái của UX Builder bị rỗng (chỉ có 1 nút Text màu xanh), người dùng không thể kéo thả, không thể co giãn cột (Row/Col), không thể chỉnh sửa riêng từng thành phần bằng giao diện trực quan!
- **QUY TẮC BẮT BUỘC — 100% PHÂN RÃ THÀNH CÂY PHẦN TỬ UX BUILDER / VBC ELEMENTS**:
  1. Khung trang và từng phân khu phải là `[section]` hoặc `[vbc_section]`.
  2. Lưới chia cột phải là `[row]` và `[col span="..."]`.
  3. Khối bao, container con phải là `[vbc_div]`, `[vbc_box]`, `[vbc_block]`.
  4. Mọi tiêu đề dùng `[vbc_h1]` đến `[vbc_h6]` (hoặc `[title]`).
  5. Mọi đoạn văn dùng `[vbc_p]`.
  6. Mọi nút bấm, link dùng `[vbc_a]` hoặc `[button]`.
  7. Mọi danh sách bài viết / CPT dùng `[vbc_post]`.
  8. Mọi form thu thập dữ liệu dùng `[contact-form-7]`.
  - Khi mở UX Builder, bên trái **BẮT BUỘC PHẢI THẤY ĐẦY ĐỦ CÂY PHẦN TỬ** (Section > Row > Column > Elements). Nếu chỉ thấy 1 node `Text`, coi như task **THẤT BẠI HOÀN TOÀN**!

---

## 🚫 Tuyệt đối cấm in thông tin nội bộ vào Source Code / Comments (Production Cleanliness)

- **TUYỆT ĐỐI CẤM (HARD BAN) đưa thông tin trao đổi, giải trình hoặc từ ngữ nhạy cảm vào mã nguồn**:
  - **Không đưa ngữ cảnh đàm thoại vào comment**: Cấm tuyệt đối viết các câu giải trình như *"Thay vì dùng cách cũ/hack...", "Sửa theo yêu cầu của khách...", "Chữa cháy...", "Tạm thời fix...", "Bỏ phương án A chọn B..."* vào bất kỳ file code nào (HTML, CSS, JS, Python, PHP, C, Shell, SQL,...).
  - **Không dùng từ ngữ nghiệp dư**: Cấm các từ như `hack`, `workaround`, `chữa cháy`, `chắp vá`, `tạm bợ`, `fix gấp`, `thông tin trao đổi nội bộ`.
  - **Không để lộ vết tích lịch sử / thương hiệu cũ**: Tuyệt đối không để lại tên cũ (như `WPC`, `wpcloud`) hoặc các ghi chú nháp trong comment/code mới.
- **Tiêu chuẩn viết Comment chuẩn Production (Production-Grade Comments)**:
  - Comment **CHỈ** được phục vụ mục đích kỹ thuật khách quan: giải thích giải thuật phức tạp, định dạng dữ liệu đầu vào/ra, chuẩn kỹ thuật (RFC/W3C/ISO).
  - Viết ngắn gọn, chuyên nghiệp, ngôn từ trung lập và sạch sẽ.
  - Xóa bỏ toàn bộ các dòng `console.log()` debug tạm thời, test code và comment rác trước khi bàn giao.
- **Quy tắc "View-Source Ready"**:
  - Luôn tự hỏi: *"Nếu khách hàng, đối tác hoặc người dùng cuối bấm `Ctrl + U` (View Source) hoặc mở file kiểm tra, đoạn mã và comment này có hoàn toàn chuyên nghiệp, sạch đẹp và chuẩn doanh nghiệp không?"*
  - Bất kỳ dòng comment nào vi phạm làm lộ thông tin nội bộ đều bị coi là **lỗi nghiêm trọng (Critical Quality Defect)**.

---

## 📝 Ghi chú thêm

<!-- Thêm các quy tắc mới vào đây -->



