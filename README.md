# OTTv2 Tournament

Website tổ chức thi bot Python 3 / C++17 trên bàn cờ 9×9. **[GAMERULE.md](GAMERULE.md) là luật chính thức.** Bản triển khai này chỉ chạy Tournament; ghép Daily và tạo Challenge đã tắt. Practice chưa được triển khai.

## Thành phần

- `app/main.py`: FastAPI, tài khoản đội, đăng nhập BTC, replay từng ván và bảng Elo.
- `app/tournament.py`: đăng ký giải, chia bảng, khóa bot, lịch encounter, xếp hạng, knockout, Elo và khiếu nại.
- `app/tournament_api.py`: API Tournament công khai và API điều hành có xác thực.
- `app/models.py`, `app/schema.py`: PostgreSQL và migration cộng thêm, chạy lặp được.
- `app/worker.py`: hàng chờ `jobs` trong PostgreSQL; biên dịch, kiểm tra giao thức và judge Docker.
- `web/`: trang đội và trang `/admin`, giữ đăng nhập và replay hiện có.
- `examples/`: bot Python và C++ dạng hàm, dùng thư viện chuẩn.

`Match` luôn là **một ván**. `Encounter` gồm hai ván vòng bảng hoặc sáu ván chính knockout, tối đa hai cặp phụ. Điểm ván, điểm trận, suất đi tiếp và Elo được lưu riêng. Tài khoản, submission, match và URL replay cũ được giữ; dữ liệu ngoài Tournament không tự được tính Elo.

## Viết bot

Ô nộp bài điền sẵn mã mẫu có chú thích API. Đổi ngôn ngữ giữ riêng bản nháp Python/C++; nộp thành công vẫn giữ mã để sửa tiếp. Python nộp `def choose_move(state):` và trả về một dict trong `state["legal_moves"]`. C++ nộp `Move chooseMove(const BoardState& state)` và trả về một phần tử trong `state.legal_moves`. Có thể thêm hàm phụ và import/include thư viện chuẩn; không cần viết chương trình đọc JSON, `main()` hoặc in kết quả.

Khi nút nộp bị khóa, lý do hiển thị ngay bên dưới và trong tooltip khi rê chuột: chưa tham gia, chưa tới giờ mở, hết hạn, hết ba lượt, bot đã khóa hoặc không có suất vào vòng hiện tại. API trạng thái đội trả `can_submit` và `submission_block_reason`; giao diện khóa nút nếu không kiểm tra được điều kiện.

C++ được cung cấp sẵn `Piece`, `Move`, `BoardState`, `state.at(x, y)` và `state.board[y][x]`. Ô trống trả về `nullptr` / `std::nullopt`; tọa độ từ 0 đến 8. Python dùng dict `state` với `pieces` và `legal_moves`. Xem đầy đủ trường dữ liệu và mã mẫu trong [mục 9 của luật](GAMERULE.md#9-giao-thức-bot-và-sandbox) hoặc **Luật chơi** trên trang đội.

Sandbox ghép hàm với phần chạy do hệ thống cung cấp, giữ giao thức JSON v2 và giới hạn tài nguyên hiện có. Giải mới lưu `bot_entrypoint=functions-v1` cùng image bất biến. Khi nâng cấp, build sandbox mới và cập nhật `SANDBOX_IMAGE` cho API/worker trước khi tạo giải mới. Giữ image cũ để retry giải cũ: snapshot môi trường, bot và replay đã lưu không bị chuyển đổi.

## Chạy local

Yêu cầu Docker Engine / Docker Desktop Linux containers và Docker Compose. Dùng project Compose riêng khi máy đang chạy worker nối database giải thật.

```sh
cp .env.example .env
# Sửa POSTGRES_PASSWORD, ADMIN_API_KEY, ADMIN_PASSWORD trong .env.
docker compose build sandbox-runtime
docker image inspect oantuti-sandbox:dev --format '{{.Id}}'
docker run --rm --entrypoint python3 oantuti-sandbox:dev --version
docker run --rm --entrypoint g++ oantuti-sandbox:dev --version
```

Trước khi tạo giải, đặt `SANDBOX_IMAGE` trong **cả API và worker** thành image ID `sha256:...` vừa lấy, hoặc registry reference `repository@sha256:...`. Ghi phiên bản thực tế vào `SANDBOX_PYTHON_VERSION`, `SANDBOX_CPP_VERSION`. Các worker phải có cùng image; có thể dùng registry hoặc `docker save` / `docker load`. Không dùng tag thay đổi được cho giải đã mở.

```sh
docker compose up --build -d
docker compose ps
docker compose logs -f api worker
```

Mở [trang đội](http://localhost:8000/) và [trang BTC](http://localhost:8000/admin). Dừng bằng `docker compose down`; không dùng `-v` nếu cần giữ dữ liệu.

`ADMIN_API_KEY` ký phiên đăng nhập BTC và cũng dùng được qua `X-Admin-Key`. Đội dùng phiên đăng nhập hiện có qua `Authorization: Bearer ...`. `REGISTRATION_OPEN` điều khiển việc tạo tài khoản, độc lập với đăng ký tham gia Tournament. `AUTO_PAIR_ENABLED` không bật lại Daily trong bản này. Tournament dùng lịch tự động hoặc nút BTC, không phụ thuộc lịch Daily cũ hoặc `MATCHES_ENABLED`.

Phiên đăng nhập BTC có hạn 8 giờ. Trang quản trị hiện `alert()` khi hết hạn, kể cả đang nhập biểu mẫu, hoặc khi API trả HTTP 401 cho phiên hiện tại. Sau khi đóng thông báo, token được xóa khỏi sessionStorage và biểu mẫu đăng nhập hiện lại; đăng nhập sai vẫn hiển thị lỗi tài khoản/mật khẩu riêng.

## Vận hành giải

**Tự động điều hành khi đến hạn** được bật mặc định khi tạo giải mới. Trong `/admin`, đặt thời lượng **Cửa sổ nộp bot cho vòng tự tạo (phút)**, mặc định 1440 phút, và bấm **Lưu chế độ tự động** để đổi hoặc tắt ở bất kỳ giai đoạn hoạt động nào. Các giờ đã lưu của vòng không bị sửa; vòng mới chưa có lịch sẽ mở ngay và dùng thời lượng này. Chế độ tự động tiếp tục cả khi trình duyệt BTC đã đóng hoặc phiên đăng nhập hết hạn.

Worker kiểm tra mỗi 5 giây trong luồng riêng, kể cả khi đang judge ván dài. Đến hạn, worker khóa đăng ký, xác nhận bảng, đóng nhận bot/đợi kiểm tra/khóa bot, chạy các lượt, chốt encounter bảng sau 30 phút, chạy playoff nếu cần, công bố và chốt phân hạng, xác nhận nhánh, mở cửa sổ knockout rồi lặp tới kết quả cuối. Loại trực tiếp thi liên tục và chốt ngay khi đủ ván, không chờ 30 phút; chỉ mở cửa sổ nộp vòng sau khi mọi suất đi tiếp đã chốt. Hold, lỗi hạ tầng, cấu hình không hợp lệ và bài chưa kiểm tra xong được hiển thị trong trạng thái tự động để BTC xử lý; hệ thống không tự force, tự retry lỗi hạ tầng hoặc tự soạn thông báo.

Thao tác được ghi với `actor=scheduler`. Nhiều worker dùng chung khóa PostgreSQL để tránh chạy trùng; khởi động lại tiếp tục từ dữ liệu đã lưu. Tắt chế độ tự động chỉ dừng tự chuyển giai đoạn; giữ công việc đã đưa vào hàng chờ. Khi nâng cấp từ phiên bản cũ, các giải đã tồn tại giữ chế độ thủ công cho tới khi BTC bật lại. Có thể dùng các nút dưới đây bất kỳ lúc nào theo điều kiện giai đoạn.

1. **Tạo giải trong `/admin`.** Công bố cửa sổ đăng ký, kênh khiếu nại, hạn xử lý hạ tầng và cách công bố lịch hoãn. Mặc định M=4, Q=2. Chỉ một giải hoạt động; giải kết thúc vẫn có trong lịch sử.
2. **Đội đăng nhập và chọn “Tham gia giải”.** Tạo tài khoản không tự đăng ký giải. Server dùng thời gian nhận, hạn đóng được tính bao gồm thời điểm bằng hạn. Giao diện dùng UTC+7.
3. **Khóa đăng ký chủ động.** BTC được khóa trước hạn đóng khi có ít nhất hai đội và cấu hình hợp lệ. Hệ thống chụp danh sách và Elo, ngừng nhận đội mới ngay sau khóa. Với ba đội trở lên, bảng cân bằng, mỗi bảng có hơn Q đội, tổng suất đi tiếp ít nhất hai. Ngoại lệ hai đội: M ít nhất 2, Q=2, một bảng hai đội và cả hai đội đủ điều kiện đi tiếp vào chung kết. Không giới hạn 14 đội. Nếu cấu hình sai, sửa M/Q trước khi khóa.
4. **Xác nhận bảng.** Xem danh sách, dung lượng, hạt giống và kết quả chia. Mọi bốc thăm lưu seed, tập lựa chọn, phương pháp, kết quả, thời gian và người thực hiện. Không bốc lại để đổi kết quả.
5. **Mở cửa sổ bot vòng bảng.** Ô ngày chưa có lịch mặc định mở lúc 00:00 ngày mai, đóng lúc 23:59 ngày kế tiếp, theo UTC+7. Lịch đã lưu vẫn được hiển thị. Mỗi đội tối đa ba submission/vòng, kể cả bài kiểm tra thất bại. Retry cùng `Idempotency-Key` và nội dung không tạo bài hay tiêu tốn lượt mới. Không dùng lại key cho nội dung khác. Giao diện giữ key khi retry yêu cầu chưa nhận được phản hồi.
6. **Đợi bài đúng hạn được xử lý, rồi khóa bot.** `ready` yêu cầu biên dịch và kiểm tra ba lượt liên tiếp ở mỗi phe bằng tiến trình mới. Chọn bài ready mới nhất; bài mới lỗi không xóa bài ready trước. Không có bài ready thì kế thừa bot Tournament tương thích của vòng trước. Không lấy bot ngoài Tournament. Lỗi hạ tầng kiểm tra cần BTC retry, không coi là thiếu bot để đi tiếp lịch.
7. **Chạy từng lượt.** Bấm **Chạy lượt** để hệ thống tự chọn lượt chưa chạy có số nhỏ nhất trong vòng đang chọn, kể cả lượt phân hạng mới tạo. Không cần nhập số lượt; giao diện hiển thị lượt tiếp theo và tắt nút khi đã chạy hết. Lịch vòng tròn và phe được lưu trước khi chạy. Hai ván mỗi encounter đổi phe. Hệ thống chỉ đưa một ván của encounter vào hàng chờ tại một thời điểm. Đội không chạy hai encounter đồng thời. Encounter sau của đội chờ encounter trước được chốt, gồm cả hạn khiếu nại.
8. **Chốt encounter vòng bảng sau khi hết 30 phút.** Kết quả và replay được công bố tạm khi đủ ván. BTC ghi hold, bằng chứng, quyết định khi nhận khiếu nại qua kênh chính thức. Hold chặn kết quả/lịch liên quan. BTC có thể dùng Force để chủ động bỏ qua thời hạn và giải phóng hold liên quan, có ghi nhật ký.
9. **Phân hạng / công bố đi tiếp.** Chỉ thực hiện khi các trận bảng đã chốt. Hệ thống áp dụng năm tiêu chí với nhóm đối đầu ban đầu. Khối hòa chạm top-Q được tạo đúng một vòng tròn phụ dùng bot đã khóa. Chạy các lượt phụ vừa tạo, chốt kết quả, rồi bấm phân hạng lần nữa. Hòa cuối dùng bốc thăm đã ghi nhận. Điểm phụ không cộng vào bảng chính.
10. **Chốt vòng bảng sau hạn khiếu nại của phân hạng.** Sau đó xác nhận nhánh. Đội rút trước xác nhận được thay bằng đội đủ điều kiện kế tiếp cùng bảng theo thứ tự đã chốt. Không bù suất từ bảng khác. Một đội đi tiếp hợp lệ thì vô địch; không đội nào thì không có vô địch.
11. **Loại trực tiếp: tứ kết, bán kết, chung kết hoặc các vòng trước đó.** Nhánh theo hạt giống cố định, kích thước lũy thừa hai, hỗ trợ BYE. Mỗi vòng có cửa sổ nộp và bot khóa riêng. Chơi đủ sáu ván chính; chỉ tạo cặp phụ khi cần. Xét riêng từng cặp phụ. Hai cặp cùng hòa thì ghi kết quả thực đấu hòa và bốc thăm đi tiếp. Không tạo match giả cho BYE/ô trống.
12. **Chốt encounter loại trực tiếp ngay khi đủ ván và không có hold.** Khi bật tự động, các ván chạy liên tục, kể cả cặp phụ; không cần bấm chạy từng ván hoặc chờ khiếu nại 30 phút. Chỉ sau khi mọi suất đi tiếp đã chốt mới tạo vòng tiếp theo và mở cửa sổ nộp chung. Báo cáo qua kênh đã công bố phải được BTC ghi thành hold để dừng tiến trình; quyết định giải quyết hold cho phép tự tiếp tục. Ván đang chạy được giữ nguyên, ván chưa nhận job sẽ chờ. Nhánh trống được truyền tiếp; cuối nhánh có thể không có vô địch. Không có trận hạng ba.

Điểm ván: thắng 1, hòa 0,5, thua 0. Điểm encounter bảng: thắng 3, hòa 1, thua 0. Kết quả hành chính hiển thị riêng, không tạo replay giả hoặc Elo. Thiếu bot một bên cho đối thủ 3 điểm trận, 2 điểm ván quy ước; thiếu cả hai cho mỗi bên 0. Rút/truất quyền trước khi chốt bảng loại toàn bộ kết quả đội đó khỏi tiêu chí bảng nhưng giữ Elo thực đấu đã hợp lệ.

Elo bắt đầu 1000, K=32, một sự kiện/encounter thực đấu hoàn tất. Lottery knockout được tính hòa. BYE và kết quả hành chính không tạo Elo. Sửa kết quả encounter bảng trước khi chốt vòng mở lại hạn khiếu nại và tính lại chuỗi Elo theo thứ tự sự kiện bất biến. Vòng đã chốt không cho sửa trực tiếp suất đi tiếp.

## Sự cố và ngoại lệ

Trang BTC có các thao tác hold/decision, retry ván, retry kiểm tra, bỏ cuộc encounter, rút giải, truất quyền, sửa điểm ván có căn cứ và hủy toàn bộ vòng/giải. Tên giải, kênh liên hệ, chính sách sự cố, lý do và bằng chứng là tùy chọn; để trống không chặn thao tác, không tự điền nội dung. Truất quyền vẫn mở hạn khiếu nại riêng. ID hold, lịch sử quyết định, worker và các lần chạy có trong nhật ký BTC.

Trong **Đăng thông báo**, chọn giải, nhập nội dung rồi bấm **Đăng thông báo**. Để trống không tạo bản tin. Có thể chọn thông báo thường hoặc `rules_update`; hệ thống không tự soạn hoặc tự đăng nội dung. Thông báo được lưu trong `tournament_actions.evidence.reason`. **Gỡ** hoặc **Gỡ tất cả thông báo** đặt `notice_hidden_at` để ẩn khỏi trang công khai; nhật ký, hold và quyết định vẫn được giữ. Đăng thông báo cập nhật luật không sửa bản luật đã khóa.

Để kết thúc sớm giải đang chọn, bấm **Đóng giải** trong phần cấu hình; **Lý do đóng giải** là tùy chọn. Thao tác dùng trạng thái `cancelled`, dừng nhận bài, hủy công việc chưa chạy của giải và cho phép tạo giải mới. Giữ tài khoản, submission, ván đã hoàn tất, replay, vòng đã chốt và Elo thực đấu; không chọn vô địch cho giải bị đóng sớm. Nếu có ván, biên dịch hoặc lượt nhận job đang xử lý, đợi hoàn tất rồi đóng lại. Lý do do BTC nhập được công khai và lưu trong nhật ký. Gửi lại yêu cầu không tạo thêm bản ghi đóng; giải đã hoàn tất giữ nguyên vô địch.

Các nút **Force** chỉ hiện khi có thời hạn hoặc hold đang chặn **mở nộp**, **khóa bot**, **chốt encounter** hoặc **chốt vòng**. **Force mở nộp ngay** dùng giờ đóng đang hiển thị trong biểu mẫu và mở nhận bài ngay; nếu đã quá hạn đóng, chọn giờ đóng mới trong tương lai trước khi bấm. Nút này ẩn khi cửa sổ đã mở bình thường hoặc bot đã khóa. Khóa đăng ký, xác nhận bảng/nhánh, chạy lượt và phân hạng dùng nút thường. Có thể nhập lý do trong ô **Lý do force**, chỉ hiện khi cần override. Force khóa bot đóng cửa sổ ngay, kể cả khi hạn còn xa; vẫn phải xử lý xong mọi bài đã nhận. Force chốt kết quả bỏ qua thời hạn khiếu nại. Khi hết thời hạn và không có hold, các nút Force tự ẩn. Force vẫn giữ thứ tự giai đoạn, cấu hình hợp lệ, playoff bắt buộc, bot đã kiểm tra và kết quả ván thực tế; không bỏ qua lỗi hạ tầng hoặc tạo điểm giả.

API điều hành giai đoạn nhận thêm `{"force":true,"force_reason":"..."}` trong JSON. Mặc định `force=false`, giữ kiểm tra thông thường. Mỗi thao tác force và các hold được giải phóng được ghi riêng bằng `kind=force_stage` trong `tournament_actions`; giữ giá trị hạn cũ trong bằng chứng và không tự đăng thông báo công khai. Chỉ BTC đã xác thực được dùng Force.

- Lỗi JSON, khóa thừa/trùng, phản hồi thừa, dòng quá 256 byte, nước sai, timeout hoặc bot thoát trong ván là lỗi bot, thua **ván đó**. Tiếp tục các ván còn lại bằng bot khóa.
- Lỗi khởi tạo Docker, artifact hoặc worker là lỗi hạ tầng: encounter chuyển `paused`, không cộng thắng. Retry chỉ ván `failed`; giữ nguyên ván `done`, phe, submission và môi trường gốc. Không sửa bot giữa vòng.
- Worker mất heartbeat hoặc đổi instance: job được khôi phục, lần chạy cũ được ghi `abandoned`; instance cũ không được ghi đè kết quả.
- Quá hạn xử lý sự cố đã công bố: ghi hold/hoãn cùng lịch mới qua quyết định BTC. Hệ thống không tự bốc thăm hoặc cộng thắng để bỏ qua lỗi hạ tầng.
- Không cho hủy riêng encounter vì hạ tầng rồi bỏ qua phần còn thiếu. Hủy theo phạm vi cả vòng/giải và không công bố vô địch dựa trên kết quả chưa hoàn tất.

## API chính

| API | Mục đích |
| --- | --- |
| `GET /api/tournaments` | Giải đang chạy và lịch sử |
| `GET /api/leaderboard?tournament_id=...` | Chỉ đội và encounter của giải đã chọn; mặc định giải mới nhất |
| `GET /api/tournaments/{id}` | Roster, bảng, encounter, nhánh, kết quả, draw và phiên bản môi trường |
| `GET /api/tournaments/{id}/rules` | Toàn văn luật đã chụp |
| `POST /api/tournaments/{id}/join` | Đội tham gia |
| `GET /api/tournaments/{id}/team/status?round_id=...` | Quota, xử lý, bot chọn/khóa và lý do đủ điều kiện |
| `POST /api/tournaments/{id}/rounds/{round}/submissions` | Nộp bot, bắt buộc `Idempotency-Key` |
| `GET /api/tournaments/{id}/standings`, `/encounters`, `/bracket`, `/results` | Các phần công khai |
| `POST /api/admin/tournaments` | Tạo giải |
| `POST /api/admin/tournaments/{id}/close` | Đóng sớm giải; nhận `{}` hoặc `{"reason":"..."}` tùy chọn, giữ lịch sử |
| `PUT /api/admin/tournaments/{id}/configuration` | Sửa cấu hình trước khóa roster |
| `PUT /api/admin/tournaments/{id}/automation` | Bật/tắt tự động ở giai đoạn đang hoạt động; nhận `auto_run` và `auto_submission_minutes` |
| `GET`, `POST /api/admin/tournaments/{id}/notices` | Xem / đăng thông báo; POST nhận `message` và `kind` (`notice` hoặc `rules_update`) |
| `DELETE /api/admin/tournaments/{id}/notices`, `/notices/{notice}` | Gỡ tất cả / một thông báo; giữ nhật ký |
| `POST /api/admin/tournaments/{id}/lock-roster`, `/confirm-groups`, `/confirm-bracket` | Mốc điều hành giải |
| `PUT /api/admin/tournaments/{id}/rounds/{round}/window` | Mở cửa sổ bot |
| `POST /api/admin/tournaments/{id}/rounds/{round}/{action}` | `lock-bots`, `start-batch`, `finalize-encounters`, `qualify`, `finalize` |
| `POST /api/admin/tournaments/{id}/rounds/{round}/exceptions/{kind}` | Ngoại lệ và khiếu nại |
| `GET /api/admin/tournaments/{id}/audit` | Nhật ký BTC và lần chạy |
| `GET /api/matches/{id}` | Replay cũ và mới, vẫn cùng URL |

Lệnh vòng nhận JSON `{}`. `start-batch` tự chọn lượt chưa chạy kế tiếp khi không truyền `batch` và trả số lượt đã chọn; API vẫn hỗ trợ `{"batch":1}` khi cần chỉ định. Chốt riêng encounter nhận `{"encounter_id":123}`. HTTP 409/429 trả lý do bị chặn. API legacy `/api/submissions` chuyển tới vòng Tournament hiện tại và cũng bắt buộc request key; không có quota Daily.

**Bảng xếp hạng** có dropdown Tournament và giữ giải đang chọn khi tải lại dữ liệu. Số trận, số trận thắng và Elo trong bảng này chỉ tính các encounter có Elo còn hiệu lực của giải đó, từ 1000 với K=32. Không trộn đội chưa tham gia, giải khác, BYE hoặc kết quả hành chính. Chuỗi Elo lưu toàn hệ thống dùng cho hạt giống và lịch sử vẫn được giữ nguyên.

## Render và worker từ xa

Dùng Web Service/PostgreSQL hiện có qua `render.yaml`; API không thực thi mã bot. Đặt `DATABASE_URL`, `ADMIN_API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `REGISTRATION_OPEN`, image bất biến và phiên bản runtime trong Render Environment.

Trên mỗi máy BTC:

```sh
cp .env.worker.example .env.worker
# Đặt External Database URL của Render, image bất biến và WORKER_ID riêng.
docker compose -f docker-compose.remote-worker.yml up --build -d
docker compose -f docker-compose.remote-worker.yml logs -f worker
```

Giữ bí mật file môi trường; không gửi nội dung lên chat/Git. Mỗi máy có `WORKER_ID` khác nhau, cùng database và cùng image. Không cố định đội cho máy; worker rảnh nhận job từ hàng chờ chung. Không để máy Sleep trong lúc thi.

## Migration và rollout

Migration tự chạy khi API/worker khởi động, có khóa PostgreSQL chống chạy đồng thời. Chỉ thêm bảng/cột/index, giữ account, source, match và replay cũ. Chạy migration hai lần trên bản sao trước khi triển khai thật.

1. Dừng nhận thay đổi và đợi job đang chạy kết thúc. Dừng toàn bộ worker cũ để không chạy scheduler Daily cũ.
2. **Sao lưu PostgreSQL trước khi nâng cấp.** Dùng database URL qua môi trường, giữ file dump ở nơi riêng và kiểm tra restore trên database thử. Với `pg_dump`, dùng URL chuẩn bắt đầu bằng `postgresql://` hoặc `postgres://`; bỏ phần `+psycopg` nếu URL ứng dụng có phần đó.

   ```sh
   pg_dump "$DATABASE_URL" --format=custom --file=before-tournament.dump
   pg_restore --list before-tournament.dump
   ```

3. Cập nhật API và **tất cả** worker về cùng commit. Trên Render deploy Web Service; trên máy BTC dùng `git pull --ff-only` và Compose `up --build -d`.
4. Kiểm tra `/health`, đăng nhập BTC, worker trực tuyến, snapshot rules/image và một giải thử trên database riêng trước khi mở đăng ký thật.
5. Không chạy đồng thời worker cũ và mới. Rollback code chỉ khi không có giải mới đang chạy; không xóa bảng/cột migration hoặc restore đè database đang nhận dữ liệu.

Migration tự động điều hành chỉ thêm `auto_run`, `auto_submission_minutes`, `auto_status` vào `tournaments`. Giải cũ nhận `auto_run=false`, cửa sổ 1440 phút; không sửa giờ, snapshot, bot hoặc kết quả. Trước khi rollback phần tự động, tắt chế độ này và dừng worker mới; giữ các cột đã thêm để phiên bản cũ tiếp tục ghi dữ liệu bằng giá trị mặc định tương thích.

## Kiểm thử

Python 3.12 và dependency trong `requirements.txt`, gồm cả `httpx` và Playwright cho kiểm thử. `test_function_api.py` cần g++ trên máy chạy test để kiểm tra helper C++ và phần chạy nhiều lượt; kiểm thử worker dùng Docker. PostgreSQL integration **TRUNCATE database thử**, tuyệt đối không dùng database giải thật.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql+psycopg://USER:PASS@localhost/ott_test'
export TEST_DATABASE_URL="$DATABASE_URL"
python -m unittest discover -s tests -p test_tournament.py -v
docker compose build sandbox-runtime
export SANDBOX_IMAGE=$(docker image inspect oantuti-sandbox:dev --format '{{.Id}}')
python -m unittest discover -s tests -p test_function_api.py -v
python -m unittest discover -s tests -p 'test_worker*.py' -v
```

Orchestration dùng kết quả xác định: 14 đội (18 encounter bảng, 7 knockout, 78 ván chính), 10/17 đội có BYE, Q lớn hơn hai, playoff, 6/8/10 ván, lottery, quota đồng thời, deadline, inheritance, hành chính, withdrawal, appeal, Elo correction, worker restart và migration dữ liệu cũ. Judge integration chạy bot Python/C++ thật trong sandbox; các bài lỗi runtime cũ vẫn được kiểm tra như lỗi ván.

Để chạy thêm kiểm thử xuyên DB/queue/worker bằng sandbox thật, đặt `RUN_SANDBOX_TESTS=1`, `TEST_SANDBOX_IMAGE=sha256:...`, rồi chạy lại suite Tournament. Giao diện dùng cùng API và replay; không truyền video trực tiếp từ Docker.

Kiểm tra trình duyệt dùng dependency trong `requirements.txt`; cài Chromium bằng `python -m playwright install chromium`. Trên một deployment thử trống có worker đang chạy, đặt `TEST_BROWSER_URL`, `TEST_DATABASE_URL`, `TEST_ADMIN_PASSWORD`, rồi chạy `python tests/browser_tournament.py`. Script kiểm tra đăng ký, tham gia, điều hành vòng, nộp bot, batch thật, replay và chặn chốt sớm; script điều chỉnh đồng hồ deadline trong database thử để không phải chờ.

`python tests/browser_bot_templates.py` chỉ cần `TEST_BROWSER_URL` và Chromium. Script chặn toàn bộ API, kiểm tra chú thích API, mẫu hàm hai ngôn ngữ, bản nháp riêng, mã giữ sau khi nộp và bố cục desktop/mobile; không ghi database.
