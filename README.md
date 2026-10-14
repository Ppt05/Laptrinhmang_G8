# Oẳn Tù Tì OTTv2

Website tổ chức thi lập trình bot cho game Oẳn Tù Tì chiến thuật trên bàn cờ 9×9. Mỗi đội nộp chương trình bằng Python 3 hoặc C++17. Bộ chấm chạy hai bot với nhau; người chơi không tự bấm nước đi.

## Cách một trận diễn ra

1. Hai đội đăng ký tài khoản trên website.
2. Mỗi đội dán mã bot vào **Thi đấu → Nộp bài dự thi**.
3. Worker biên dịch bài và chỉ dùng bài có trạng thái sẵn sàng.
4. Ban tổ chức mở lịch. Worker ghép bot, chạy trận và lưu kết quả.
5. Các đội xem kết quả ở **Lịch sử đấu** và **Bảng xếp hạng**.

## Trạng thái bản Render

Website demo: <https://oantuti-demo.onrender.com/>.

Trang quản trị lịch thi: <https://oantuti-demo.onrender.com/admin/>. Tài khoản mặc định có tên `admin`; mật khẩu nằm trong Render Dashboard → dịch vụ `oantuti-demo` → **Environment** → `ADMIN_PASSWORD`. Không chia sẻ mật khẩu hoặc đưa lên GitHub. Nếu Render chưa có biến này, cần đồng bộ Blueprint từ repo để Render tạo mật khẩu.

Render Free hiện chạy website/API và cơ sở dữ liệu, **chưa chạy worker**. Vì vậy, bản Render hiện tại nhận đăng ký và bài nộp nhưng chưa biên dịch bot, chưa ghép hai đội và chưa ghi kết quả trận. Trang admin có thể lưu lịch, nhưng mở lịch không tự làm trận chạy nếu thiếu worker. Không bật `MATCHES_ENABLED=true` trên Render để giả lập worker.

Muốn demo hai bot chạy đầy đủ ngay, hãy chạy toàn bộ stack bằng Docker trên máy có Docker Desktop như hướng dẫn bên dưới. Muốn các trận trên website Render chạy online, cần triển khai một worker có Docker và kết nối cùng cơ sở dữ liệu; Render Free không có gói Background Worker miễn phí.

## Chạy đầy đủ trên máy cá nhân

Yêu cầu: Docker Desktop đang mở.

1. Tạo file `.env` từ `.env.example`:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Trong `.env`, đặt `POSTGRES_PASSWORD`, `ADMIN_API_KEY` và `ADMIN_PASSWORD` thành giá trị riêng; giữ `ADMIN_USERNAME=admin`. Đổi `MATCHES_ENABLED=false` thành `MATCHES_ENABLED=true`. Để worker tự ghép khi lịch được mở, đặt `AUTO_MATCH_START` thành một giờ đã qua trong ngày, ví dụ `00:00`.
3. Chạy các dịch vụ:

   ```powershell
   docker compose up --build -d
   ```

4. Mở <http://localhost:8000>, đăng ký hai đội và nộp mã bot cho mỗi đội.
5. Mở <http://localhost:8000/admin>, đăng nhập bằng `ADMIN_USERNAME` và `ADMIN_PASSWORD` trong `.env`, đặt thời điểm bắt đầu/kết thúc sao cho hiện tại nằm trong khung giờ đó, rồi lưu lịch. Worker sẽ ghép các bot đã biên dịch thành công.

Xem log bằng `docker compose logs -f api worker`; dừng bằng `docker compose down`.

**An toàn:** Worker cần quyền điều khiển Docker để chạy mã dự thi trong sandbox. Chỉ chạy trên máy tin cậy; không chia sẻ quyền quản trị máy hoặc Docker socket cho thí sinh.

## Cách bot nhận lượt và trả nước đi

Ở mỗi lượt, bot đọc một dòng JSON từ `stdin`. Trong dữ liệu có trạng thái bàn cờ và danh sách `legal_moves`. Bot chọn một nước hợp lệ và in một dòng JSON ra `stdout`:

```json
{"piece_id":"S01","to":"a4"}
```

Chỉ chọn cặp `piece_id` và `to` có trong `legal_moves`. Không in lời giải thích vào `stdout`. Mỗi lượt có tối đa 1,5 giây; đầu ra sai hoặc quá giờ khiến bot thua trận. Website có nút **Chèn bot mẫu** để bắt đầu.

## Luật OTTv2

- Mỗi bên có 10 quân trên bàn 9×9; Xanh đi trước. Mỗi lượt di chuyển một ô theo một trong tám hướng.
- Đấm thắng Kéo, Kéo thắng Lá, Lá thắng Đấm. Quân chỉ ăn được quân đối thủ mà mình khắc chế.
- Thắng khi ăn hết một loại quân đối thủ hoặc đưa quân tới đích: Bắc tới `a1`, Nam tới `i9`.
- Hòa nếu lặp lại một thế cờ ba lần, không còn nước hợp lệ, hoặc đủ 200 nước đi.
- Điểm bảng xếp hạng: thắng 1, hòa 0,5, thua 0.

## Cấu trúc thư mục

- `app/`: API, dữ liệu đội/bài/trận và worker.
- `web/`: website, khu vực nộp bài và trang quản trị.
- `sandbox/`: Docker image để biên dịch/chạy bot.
- `docker-compose.yml`: chạy API, PostgreSQL, worker và sandbox trên máy cá nhân.
- `render.yaml`: cấu hình website demo trên Render; không tạo worker.

Không commit `.env`, mật khẩu, token đội hoặc khóa quản trị lên GitHub.
