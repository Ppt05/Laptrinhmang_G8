# Đưa website Oẳn Tù Tì lên Render (bản demo miễn phí)

Blueprint `render.yaml` tạo một Web Service chạy website + API và một Render Postgres miễn phí. Repo cần có thư mục này làm thư mục gốc (chứa `render.yaml` và `Dockerfile`).

## Các bước

1. Tạo một repository GitHub mới, chép nội dung trong `oantuti_server` vào thư mục gốc repo rồi push. Không đưa `.env` lên GitHub; `.gitignore` đã loại trừ file này.
2. Đăng nhập Render, chọn **New → Blueprint**, kết nối repository vừa push và chọn `render.yaml`.
3. Khi Render yêu cầu `REGISTRATION_CODE`, tự đặt một mã rồi chỉ gửi mã đó cho thí sinh. `ADMIN_API_KEY` được Render tự tạo.
4. Chọn deploy. Chờ Web Service và Postgres hiện trạng thái **Live**; mở URL `onrender.com` của Web Service, không dùng `/docs` làm trang chính.

## Giới hạn của bản miễn phí hiện tại

- Web Service có thể ngủ sau 15 phút không có truy cập; lần mở tiếp theo có thể mất khoảng một phút để thức dậy.
- Postgres miễn phí có hạn mức và hết hạn sau 30 ngày. Không dùng để giữ kết quả quan trọng nếu chưa xuất bản sao lưu.
- `MATCHES_ENABLED=false`. Blueprint này chưa triển khai worker chấm bot. Vì vậy, API có thể nhận tài khoản và ghi bài nộp vào hàng đợi, nhưng bài nộp sẽ chưa được biên dịch/xử lý và chưa có trận tự động. Không công bố cuộc thi thật cho tới khi worker được triển khai và kiểm tra.
- Worker hiện tại chạy bot bằng Docker socket; Render Free không cung cấp Background Worker miễn phí. Không bật ghép trận chỉ bằng cách đổi `MATCHES_ENABLED`.

## Cấu hình

Render lấy kết nối PostgreSQL từ Blueprint; code chuyển URL của Render sang driver Psycopg 3. `Dockerfile` dùng biến `PORT` do nền tảng cung cấp, còn Docker Compose cục bộ đặt `PORT=8000`.

Các secret chỉ nhập trong Render Dashboard. Không commit `.env`, registration token, `ADMIN_API_KEY` hoặc mật khẩu cơ sở dữ liệu vào GitHub.
