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

Trang quản trị lịch thi: <https://oantuti-demo.onrender.com/admin/>. Tên đăng nhập mặc định là `admin`. Mật khẩu nằm trong Render Dashboard → dịch vụ `oantuti-demo` → **Environment**: dùng `ADMIN_PASSWORD` nếu biến này đã được tạo; nếu chưa có, dùng `ADMIN_API_KEY`. Không chia sẻ secret hoặc đưa lên GitHub.

Render Free hiện chạy website/API và cơ sở dữ liệu, **chưa chạy worker**. Vì vậy, bản Render hiện tại nhận đăng ký và bài nộp nhưng chưa biên dịch bot, chưa ghép hai đội và chưa ghi kết quả trận. Trang admin có thể lưu lịch, nhưng mở lịch không tự làm trận chạy nếu thiếu worker. Không bật `MATCHES_ENABLED=true` trên Render để giả lập worker.

Muốn demo hai bot chạy đầy đủ ngay, hãy chạy toàn bộ stack bằng Docker trên máy có Docker Desktop như hướng dẫn bên dưới. Muốn các trận trên website Render chạy online, cần triển khai một worker có Docker và kết nối cùng cơ sở dữ liệu; Render Free không có gói Background Worker miễn phí.

## Chạy worker trên máy cá nhân, dùng website Render

Máy chạy worker có thể khác máy người chơi. Máy worker phải bật, có Internet và Docker Desktop đang chạy trong suốt thời gian thi. Worker cần quyền truy cập Docker socket để chạy bot trong sandbox; chỉ chạy trên máy của ban tổ chức.

1. Lấy **External Database URL** trong Render Dashboard → dịch vụ Postgres của cuộc thi → **Connect**. Không dùng Internal Database URL vì worker chạy ngoài Render.
2. Trong thư mục `oantuti_server`, tạo file cấu hình riêng:

   ```powershell
   Copy-Item .env.worker.example .env.worker
   notepad .env.worker
   ```

   Thay giá trị `DATABASE_URL` bằng External Database URL. Giữ `MATCHES_ENABLED=true`; đặt `AUTO_MATCH_START=00:00` để worker có thể ghép bot ngay trong ngày sau khi lịch thi được mở. Không gửi hoặc commit `.env.worker`.

3. Mở Docker Desktop, rồi chạy worker:

   ```powershell
   docker compose -f docker-compose.remote-worker.yml up --build -d
   docker compose -f docker-compose.remote-worker.yml logs -f worker
   ```

   Giữ Docker Desktop và cửa sổ worker hoạt động. Dùng `Ctrl+C` để thoát xem log; worker vẫn chạy nền. Dừng worker bằng `docker compose -f docker-compose.remote-worker.yml down`.

4. Sau khi log cho thấy worker đã kết nối và đang chờ việc, vào Render → Web Service `oantuti-demo` → **Environment**, đổi `MATCHES_ENABLED` thành `true` rồi lưu để trang web nhận trạng thái worker đã sẵn sàng. Chờ dịch vụ web khởi động lại.
5. Đăng nhập `/admin`, đặt giờ bắt đầu/kết thúc bao quanh thời điểm hiện tại và bật lịch. Hai đội cần có bài nộp ở trạng thái sẵn sàng. Worker sẽ biên dịch bài còn trong hàng đợi, rồi ghép các bot sẵn sàng theo lịch hằng ngày; với đúng hai đội, trận ghép sẽ là hai đội đó. Xem log worker và trang lịch sử/bảng xếp hạng để xác nhận kết quả.

Nếu người chơi đã đăng ký trên Render, worker phải dùng đúng External Database URL của cơ sở dữ liệu Render đó. Không chạy `docker compose up` cục bộ song song để chờ các bài nộp trên website; cấu hình Compose mặc định tạo một database local riêng.

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
5. Chờ cả hai bài có trạng thái sẵn sàng. Mở <http://localhost:8000/admin>, đăng nhập bằng `ADMIN_USERNAME` và `ADMIN_PASSWORD` trong `.env`, đặt thời điểm bắt đầu/kết thúc sao cho hiện tại nằm trong khung giờ đó, rồi lưu lịch. Worker ghép các bot sẵn sàng một lần mỗi ngày từ mốc `AUTO_MATCH_START`.

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
