# Chạy website trên Render và worker trên máy ban tổ chức

Xem [README.md](README.md) để cài dự án, đặt `WORKER_ID` cho từng máy và hiểu cách hệ thống tự ghép trận theo lịch admin.

`render.yaml` tạo website/API và PostgreSQL. Worker chạy bằng `docker-compose.remote-worker.yml` trên máy của ban tổ chức có Docker Desktop. Các worker cùng kết nối tới **External Database URL** của PostgreSQL trên Render; mỗi máy có một `WORKER_ID` khác nhau trong `.env.worker`.

Không đưa `.env`, `.env.worker`, `ADMIN_PASSWORD`, `ADMIN_API_KEY` hoặc URL database lên GitHub. Trang admin chỉ đặt giờ mở/đóng và xem kết quả; các worker tự lấy trận từ hàng chờ chung. Nếu website chưa hiện bản mới, kiểm tra bản deploy của Web Service trong Render Dashboard.
