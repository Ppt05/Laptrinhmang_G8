# OanTuTi Bot League — OTTv2

> **Giao thức thi đấu phiên bản 2.** Đây là game chiến thuật trên bàn 9×9 theo luật của dự án [LapTrinhMangNhom8](https://github.com/phongdepptrai/LapTrinhMangNhom8), không phải trò chọn BUA/BAO/KEO độc lập theo từng lượt. Ghép trận đang tắt mặc định; chỉ bật sau khi bot đã được cập nhật và ban tổ chức xác nhận thể lệ.

## Luật bàn cờ

- Mỗi bên có 10 quân: 4 paper (lá), 3 rock (đấm), 3 scissors (kéo). Xanh/south đi trước.
- Mỗi lượt di chuyển một quân một ô theo một trong 8 hướng. Không đi ra ngoài bàn, không đi vào quân cùng phe, quân địch cùng loại hoặc quân địch khắc chế quân mình. Có thể đi vào ô trống hoặc ăn quân địch mà quân mình khắc chế.
- Rock thắng scissors, scissors thắng paper, paper thắng rock.
- Thắng khi ăn hết quân của một loại bên đối thủ hoặc đưa quân north tới a1 / quân south tới i9.
- Bản chạy giải xử thua nếu bot timeout hoặc xuất sai JSON/nước không hợp lệ. Nếu một bên đến lượt mà không có nước hợp lệ, bàn cờ lặp lại 3 lần, hoặc đã đi tổng cộng 200 lượt (plies), trận kết thúc hòa ngay, không đi tiếp. Đây là quy tắc vận hành do ban tổ chức bổ sung vì README game chưa định nghĩa các trường hợp hòa này.

## Giao thức bot

Mỗi tiến trình bot được khởi động một lần cho cả trận. Mỗi lần tới lượt, hệ thống gửi một dòng JSON vào `stdin`, ví dụ:

```json
{"protocol_version":2,"move_number":1,"side":"south","board_size":9,"pieces":[{"id":"S01","side":"south","type":"paper","x":1,"y":4}],"legal_moves":[{"piece_id":"S01","from":"b5","to":"a4","capture":null}]}
```

`pieces` chứa toàn bộ quân còn trên bàn; `x=0..8` ứng với cột `a..i`, `y=0..8` ứng với hàng `1..9`. `legal_moves` liệt kê các lựa chọn hợp lệ của bot ở lượt hiện tại. Bot phải trả đúng một dòng JSON qua `stdout`, ví dụ:

```json
{"piece_id":"S01","to":"a4"}
```

Chỉ cần chọn một cặp `piece_id` + `to` có trong `legal_moves`. Mọi log gỡ lỗi ghi ra `stderr`, không ghi thêm nội dung vào `stdout`.

Mẫu Python tối thiểu (chọn nước hợp lệ đầu tiên):

```python
import json, sys

for line in sys.stdin:
    state = json.loads(line)
    moves = state["legal_moves"]
    if not moves:
        break
    move = moves[0]
    print(json.dumps({"piece_id": move["piece_id"], "to": move["to"]}), flush=True)
```

Mẫu này chỉ để kiểm tra giao thức; nó không phải chiến thuật tốt.

## Chạy server cục bộ

1. Mở Docker Desktop, vào thư mục `E:\Bai3_Oantuti_N8\oantuti_server`.
2. Nếu chưa có `.env`, sao chép `.env.example` thành `.env`, rồi đặt mật khẩu DB, khóa admin và mã đăng ký riêng. Không chia sẻ `.env`.
3. Chạy `docker compose up --build -d`; mở `http://localhost:8000/docs`.
4. Đăng ký đội, lưu token; nộp mã nguồn dùng `language` là `python` hoặc `cpp` và giao thức ở trên.
5. Xem trạng thái tại `GET /api/submissions`. Khi đã có bot OTTv2 hợp lệ, mới bật `MATCHES_ENABLED=true` trong `.env` và khởi động lại dịch vụ.

## Đưa API lên Internet bằng Linux VPS

Đăng ký một VPS Ubuntu có IPv4 công khai và một tên miền/subdomain. Trỏ bản ghi DNS `A` của tên miền về IPv4 VPS; mở cổng `22`, `80`, `443` ở firewall nhà cung cấp. Cài Docker Engine + Compose plugin trên VPS, chép thư mục server lên đó, đặt `.env` với `DOMAIN=api.ten-mien-cua-ban.vn` và các secret riêng, rồi chạy:

```bash
docker compose --profile public up --build -d
docker compose ps
```

Caddy tự xin và gia hạn chứng chỉ HTTPS. Người chơi truy cập `https://api.ten-mien-cua-ban.vn/docs`. Không bật ghép trận trước khi kiểm tra bot OTTv2, backup, giới hạn tài nguyên và năng lực máy chủ. Cấu hình cloud chưa được triển khai hoặc thử tải; VPS/domain phải do ban tổ chức cung cấp và quản lý.

## Hạ tầng và vận hành

API đăng ký/nộp bài, PostgreSQL, hàng đợi và worker chạy bot cô lập được đóng gói bằng Docker Compose. Sandbox tắt mạng, giới hạn 256 MB RAM, 1 CPU và 32 tiến trình. Worker có quyền truy cập Docker socket, tương đương quyền quản trị Docker host; chỉ chạy trên máy tin cậy. Bản này chưa được kiểm chứng tải 4.000 bot, chưa có giao diện thi hoàn chỉnh, sao lưu ngoài máy, HTTPS hay khôi phục token. Không mở thi công khai trước khi triển khai Linux cloud, cấu hình bảo mật và thử tải trên đúng máy chủ dự kiến.
