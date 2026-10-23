# Daily — hướng dẫn sử dụng

Daily là chế độ riêng, mặc định tắt. Mỗi đội có **5 lượt nộp bot và tối đa 5 trận/ngày**,
ngày bắt đầu lúc 00:00 UTC+7. Hai quota độc lập; bài lỗi code vẫn tính lượt nộp.

## Bật và tắt

Chạy `docker compose up --build -d`, đăng nhập `/admin`, gạt công tắc Daily.
Công tắc lưu ngay, hiển thị Đang mở / Tạm đóng; nếu lỗi lưu sẽ trở về trạng thái trước.
API và worker phải dùng cùng code, database và SANDBOX_IMAGE cố định bằng ID/digest.
Không cần tạo Tournament hoặc lịch cũ trong event_control để dùng Daily.
Tắt Daily dừng nhận bài và ghép mới; không hủy các trận đã ghép. Retry hạ tầng vẫn dùng được.

## Đội thi

1. Đăng nhập, chọn Daily và nộp hàm Python/C++ như Practice (tối đa 100 KB).
2. Chờ bài chuyển sang Sẵn sàng. Có thể nộp phiên bản mới trong 5 lượt/ngày.
3. Chọn bot Daily sẵn sàng nộp hôm nay rồi bấm **Tìm trận Daily**.
4. Worker ghép khi có đối thủ tương thích, khoảng mỗi 5 giây. Có thể Hủy tìm trận trước khi ghép.
5. Mỗi lần tìm chỉ tạo một trận. Đấu xong cần bấm tìm lại; không tự chơi hết 5 lượt.
6. Trận lưu đúng bot đã chọn và môi trường tương ứng. Nộp mới không đổi bot của trận đã ghép.

Một đội chỉ có một yêu cầu tìm hoặc một trận Daily chưa xử lý xong.
Hàng chờ hết hiệu lực khi qua ngày mới; bot hôm trước không dùng cho trận mới.
Trận đã ghép giữ một lượt, kể cả chờ/chạy/lỗi hạ tầng. Ngày tính quota là ngày ghép.

Ưu tiên đội chờ lâu, đối thủ ít gặp và giảm lệch số lần cầm Nam/Bắc trong ngày.
Nếu chỉ có hai đội vẫn cho tái đấu. Cân bằng phe là ưu tiên khi có thể, không phải bảo đảm tuyệt đối.
Seed, dữ liệu lịch sử và kết quả được lưu để tái lập cách ghép.

Tournament được ưu tiên: đội đang bận giải chưa thể tìm Daily; vẫn có thể nộp bot.
Nếu Daily đã chạy thì để hoàn tất và chốt Elo trước khi Tournament tiếp tục.
Practice không chiếm lịch tính Elo.

## Điểm và giao diện quản trị

Một trận Daily gồm một ván: thắng 1, hòa 0,5, thua 0 điểm.
**Xếp hạng theo Elo chung với Tournament**, K=32, ban đầu 1000. Tổng điểm Daily để tham khảo.
Lọc ngày chỉ lọc đội và điểm của các trận trong ngày; Elo vẫn là giá trị chung hiện tại.

Admin có bảng trận gần nhất: đội Nam/Bắc, tỷ số, trạng thái và nút Xem lại/Chi tiết.
Hiện trước 10 trận, bấm Xem thêm để tải tiếp; điện thoại hiển thị từng trận thành thẻ.
Hủy và trả lượt nằm trong menu dấu ba chấm, cần nhập/xác nhận lý do qua hộp thoại.
Nhật ký ghép hiển thị tên đội, phe, thời gian và đội còn chờ; JSON/seed nằm trong Chi tiết kỹ thuật.

## Sự cố và dữ liệu cũ

- Lỗi code không được hoàn lượt nộp. Bài mới dùng một lượt khác trong giới hạn 5.
- Lỗi hạ tầng biên dịch: kiểm tra lại đúng bài đã nộp, không tính thêm lượt.
- Lỗi hạ tầng trận: retry cùng ID trận/bot/môi trường, không tạo lượt mới.
- Hủy vì hạ tầng: giữ lịch sử, loại điểm/Elo và trả một lượt trận; không hoàn lượt nộp bot.
  Sau khi hủy, đội phải chủ động tìm lại.
- Các bài/trận trước nâng cấp giữ nguyên. Trận Daily cũ hợp lệ tính vào quota của ngày ghép.
  Trạng thái opt-in cũ không tự chuyển thành yêu cầu tìm trận. Không sửa snapshot giải cũ.
- Bot khác snapshot luật/môi trường không ghép chung, kể cả nếu cùng ngày; có thể cần nộp bot mới.

## API

- GET /api/daily/state: trạng thái, hai quota, danh sách bài, hàng chờ và trận đang xử lý.
- POST /api/daily/submissions: nộp bài; yêu cầu Idempotency-Key.
- POST /api/daily/queue: chọn submission_id và tìm một trận; yêu cầu Idempotency-Key.
- DELETE /api/daily/queue: hủy tìm trận trước khi ghép.
- POST /api/daily/matches/{id}/retry: retry cùng trận lỗi hạ tầng.
- POST /api/daily/submissions/{id}/retry: retry bài lỗi hạ tầng trong ngày.
- GET /api/daily/leaderboard?day=YYYY-MM-DD: xếp theo Elo, điểm Daily tham khảo.
- GET/PUT /api/admin/daily: xem/bật/tắt (BTC).
- GET /api/admin/daily/matches?offset=0&limit=10: phân trang trận (BTC).
- GET /api/admin/daily/draws: nhật ký ghép có tên đội và dữ liệu tái lập (BTC).
- POST /api/admin/matches/{id}/void: hủy, loại điểm/Elo và trả lượt (BTC).
- PUT /api/daily/opt-in: endpoint cũ chỉ hỗ trợ hủy hàng chờ; không dùng để tìm trận mới.

## Kiểm thử

Chỉ dùng TEST_DATABASE_URL trỏ tới PostgreSQL tạm riêng: kiểm thử xóa dữ liệu trong database đó.
Không dùng database local đang chơi hoặc database Render.

`python -m unittest discover -s tests -p test_daily.py -v`

Kiểm tra quota, gửi trùng/đồng thời, tìm/hủy, hết ngày UTC+7, snapshot, Elo, retry,
ưu tiên Tournament, tái đấu/cân bằng phe, nâng cấp giữ dữ liệu và giao diện admin/đội.
