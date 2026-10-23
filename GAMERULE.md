# OTTv2 — Luật chơi và thể thức giải đấu

## 1. Hiệu lực và phạm vi áp dụng

Cập nhật Daily 2026-10-23: tối đa 5 lượt nộp bot và 5 trận/ngày/đội; đội chủ động tìm trận bằng một bài Daily sẵn sàng trong ngày. Mỗi trận khóa phiên bản bot riêng. Bảng Daily xếp theo Elo chung, điểm Daily để tham khảo. Chỉ áp dụng cho yêu cầu mới sau nâng cấp; giữ nguyên bản chụp, kết quả và replay đã lưu.

Phiên bản 2026-10-18.4: điều hành loại trực tiếp liên tục, chốt encounter ngay khi đủ ván và không có hold/khiếu nại đang xử lý. Chỉ mở cửa sổ nộp vòng kế tiếp sau khi đủ các suất đi tiếp. Chính sách điều hành này áp dụng cho knockout đang và sắp diễn ra; không sửa kết quả, replay, bản chụp hoặc luật bàn cờ đã lưu. Vòng bảng vẫn giữ hạn khiếu nại 30 phút.

Thay đổi 2026-10-18.3: BTC có thể ủy quyền worker tự điều hành theo các mốc thời gian tại mục 11.1. Giữ nguyên luật bàn cờ, thể thức, thời hạn khiếu nại và bản chụp đã lưu của giải cũ.

Thay đổi 2026-10-18.2: các trường văn bản trong phần điều hành là tùy chọn, gồm tên giải, kênh liên hệ, chính sách sự cố, lý do và bằng chứng. Để trống không chặn thao tác; hệ thống vẫn lưu loại thao tác, đối tượng, người thực hiện và thời điểm, không tự điền nội dung. Thông báo để trống không tạo bản tin.

Thay đổi 2026-10-18.1: bổ sung API nộp hàm và mã mẫu cho C++17/Python 3 tại mục 9. Áp dụng cho giải mới dùng môi trường `bot_entrypoint=functions-v1`; giữ nguyên bản chụp luật, image, submission và replay của giải cũ.

Thay đổi 2026-10-17.1: cho phép giải hai đội theo ngoại lệ tại mục 4.1 và làm rõ quyền khóa đăng ký chủ động của BTC.

Tài liệu này quy định luật chơi, thể thức giải đấu, điều kiện nộp bot, cách tính điểm và Elo, cùng quy trình xử lý sự cố và khiếu nại của OTTv2. Ban tổ chức (BTC), đội thi và hệ thống thi đấu áp dụng thống nhất các quy định trong tài liệu.

Phiên bản này có hiệu lực theo mốc công bố và phạm vi áp dụng nêu trên. Với giải đã khóa danh sách, việc áp dụng thay đổi tuân theo mục 11.4. Khi có khác biệt với tài liệu hướng dẫn hoặc bản luật cũ, tài liệu này là căn cứ áp dụng trong phạm vi hiệu lực của phiên bản.

## 2. Thuật ngữ, chế độ và thời gian

- **Ván (game):** Một bàn cờ từ khởi tạo đến thắng, hòa hoặc thua.
- **Trận (encounter):** Cuộc đối đầu giữa hai đội, gồm số ván quy định cho từng chế độ.
- **Lượt vòng bảng:** Một đợt lịch thi trong cùng vòng bảng; không mở quyền thay bot.
- **Vòng thi:** Toàn bộ vòng bảng cùng đấu phân hạng, hoặc một cấp knockout.
- **Submission:** Mã nguồn bất biến có ID riêng, thuộc một chế độ và cửa sổ nộp cụ thể.
- **Bot đã khóa:** Submission được cố định cho một đội trong một vòng thi.
- **BYE:** Suất đi tiếp do vị trí nhánh trống; không tạo trận giả, điểm hoặc Elo.
- **Kết quả hành chính:** Kết quả do thiếu điều kiện thi đấu, bỏ cuộc hoặc quyết định kỷ luật; không được ghi thành ván thực đấu.

| Chế độ | Ghép đối thủ | Số ván/trận | Điểm và Elo | Bot sử dụng |
| --- | --- | --- | --- | --- |
| Tournament | Lịch giải do hệ thống lập, BTC xác nhận | Bảng: 2; knockout: 6 chính, tối đa 4 phụ | Điểm giải; Elo cho trận thực đấu hợp lệ | Khóa theo vòng thi |
| Daily | Đội chủ động tìm; hệ thống ghép đối thủ | 1 | Xếp theo Elo chung; điểm Daily 1/0,5/0 tham khảo | Bài Daily sẵn sàng trong ngày, khóa riêng cho từng trận |
| Challenge | Đội mời, đối thủ chấp nhận | 2, đổi phe | Giao hữu, không điểm chính hoặc Elo | Bài `ready` được mỗi đội chọn lúc chấp nhận |
| Practice | Đội chọn bot mẫu BTC | 1 | Không điểm hoặc Elo | Bài `ready` của đội, kể cả bài cũ tương thích |

Daily mặc định tắt. Mỗi đội được nộp tối đa **5 bài Daily/ngày**, kể cả bài lỗi code, và chơi tối đa **5 trận Daily/ngày**. Hai quota độc lập. Ngày được xác định theo UTC+7. Trận đã ghép (kể cả đang chờ, đang chạy hoặc lỗi hạ tầng chờ xử lý) giữ một lượt; trận bị hủy vì hạ tầng trả lại lượt. Retry cùng trận hoặc kiểm tra lại cùng bài lỗi hạ tầng không tạo lượt mới.

Nộp bài không tự tham gia hàng chờ. Đội chọn một bài Daily `ready` của chính đội, đã nộp trong ngày, rồi chủ động **Tìm trận**. Mỗi đội chỉ có một yêu cầu tìm trận hoặc một trận Daily chưa xử lý xong. Có thể hủy tìm trước khi ghép mà không mất lượt. Khi ghép, hệ thống khóa hai submission và môi trường của chúng; bài nộp mới không thay bot của trận cũ. Sau mỗi trận, đội phải chủ động tìm lại. Hàng chờ hết hiệu lực khi sang ngày mới; bài hôm trước không dùng để tìm trận mới.

Hệ thống ưu tiên yêu cầu chờ lâu trong mỗi nhóm bot tương thích, sau đó chọn đối thủ ít gặp trong ngày và phương án giảm chênh lệch số lượt cầm Nam/Bắc. Nếu bằng nhau, dùng ngẫu nhiên có seed lưu lại. Cho phép tái đấu nếu không có đối thủ phù hợp khác; không bảo đảm tuyệt đối phe cân bằng khi số lượt lẻ hoặc các đội tham gia không đều. Không ghép bot khác bản chụp luật hoặc môi trường. Bảng Daily xếp theo Elo chung với Tournament, sau đó tổng điểm Daily và ID đội; bộ lọc ngày chỉ lọc đội/trận Daily, Elo vẫn là giá trị chung hiện tại.

Practice tối đa 10 trận được chấp nhận/ngày/đội, mỗi đội chỉ có một trận Practice đang chờ hoặc chạy. Retry hạ tầng không tiêu tốn thêm quota. Job Practice có ưu tiên thấp hơn Tournament.

Challenge mặc định tắt cho tới khi BTC công bố quota và thời hạn chấp nhận. Khi bật, mỗi đội chỉ được có một Challenge đang chờ hoặc chạy; số trận chấp nhận/ngày không được vượt quota đã công bố. Retry hạ tầng không tiêu tốn thêm quota.

Không chạy đồng thời hai trận tính Elo có cùng một đội. Tournament có ưu tiên xếp lịch cao hơn Daily; nếu Daily đã chạy thì để hoàn tất, giữ Tournament chờ thay vì hủy ván hợp lệ. Không nhận yêu cầu tìm trận Daily mới cho đội đang có trận Tournament chờ hoặc chạy; vẫn cho nộp/kiểm tra bot trong quota riêng. Mỗi kết quả Elo của đội phải được chốt trước khi bắt đầu trận tính Elo tiếp theo của đội đó.

Tất cả hạn dùng thời gian nhận của server; giao diện hiển thị UTC+7. Bài được nhận khi thời điểm nhận nhỏ hơn hoặc bằng hạn đóng. Cửa sổ đăng ký và cửa sổ nộp bot là hai cửa sổ riêng.

## 3. Luật bàn cờ

### 3.1. Bàn cờ, quân và thế cờ đầu

Bàn cờ 9×9, tọa độ từ `a1` đến `i9`; tọa độ lập trình `a1=(0,0)`, `i9=(8,8)`. Nam (`south`) đi trước, Bắc (`north`) đi sau. Mỗi phe có 3 Đấm (`rock`), 3 Kéo (`scissors`), 4 Lá (`paper`).

| Phe | Lá (`paper`) | Đấm (`rock`) | Kéo (`scissors`) |
| --- | --- | --- | --- |
| Nam | S01:b5, S04:c4, S07:d3, S10:e2 | S03:b4, S06:c3, S09:d2 | S02:c5, S05:d4, S08:e3 |
| Bắc | N01:e8, N04:f7, N07:g6, N10:h5 | N02:f8, N05:g7, N08:h6 | N03:e7, N06:f6, N09:g5 |

Đổi phe chỉ đổi đội điều khiển Nam/Bắc, không đổi ID, loại hoặc vị trí xuất phát của quân. Mỗi ván khởi tạo lại toàn bộ thế cờ chuẩn.

### 3.2. Nước đi

Mỗi lượt di chuyển một quân đúng một ô theo một trong tám hướng. Không ra ngoài bàn và không vào ô đồng đội. Đấm ăn Kéo, Kéo ăn Lá, Lá ăn Đấm. Chỉ được vào ô địch nếu quân di chuyển ăn được quân địch; quân bị ăn bị loại ngay. Hai quân cùng loại chặn nhau, không cùng bị loại.

### 3.3. Thắng, hòa và thứ tự kiểm tra

Một phe thắng khi quân của mình đến đích: Nam đến `i9`, Bắc đến `a1`; hoặc ăn hết một loại quân bất kỳ của đối thủ. Đối thủ phạm lỗi bot cũng làm phe còn lại thắng ván.

Thứ tự xử lý:

1. Ghi nhận thế cờ khởi tạo là lần xuất hiện thứ nhất, gồm bên đến lượt.
2. Trước khi gọi bot, nếu bên đến lượt không có nước hợp lệ thì kết luận hòa; không gọi bot.
3. Nếu bot được gọi nhưng trả lời lỗi, kết luận thua ván cho bot đó.
4. Nếu nước hợp lệ, áp dụng nước đi và kiểm tra hai điều kiện thắng trên bàn cờ trước.
5. Nếu chưa có bên thắng, đổi bên đến lượt và kiểm tra hòa theo thứ tự: không có nước hợp lệ, lặp ba lần, đạt 200 nước cá nhân.

Khóa nhận dạng lặp gồm ID, phe, loại và vị trí của từng quân sống, cùng bên đến lượt. Thứ tự phần tử trong JSON không làm thay đổi khóa. Một lượt Nam và một lượt Bắc là hai nước cá nhân. Nước thứ 200 vẫn được xét thắng trước khi xét hòa. Một ván chỉ có một kết quả và một lý do kết thúc chính.

## 4. Đăng ký, chia bảng và lịch giải

Luồng chính: đăng ký, khóa đăng ký, chụp Elo, chia và xác nhận bảng, mở nộp vòng bảng, xử lý bài đúng hạn, khóa bot, thi vòng bảng, phân hạng khi cần, chốt đội đi tiếp, lập và xác nhận nhánh, mở nộp cho từng cấp knockout, khóa bot, thi và công bố kết quả.

### 4.1. Cấu hình bảng

Ban tổ chức cấu hình số đội tối đa trong mỗi bảng và số đội được đi tiếp từ mỗi bảng. Hai giá trị này phải là số nguyên dương.

Số bảng được tính bằng tổng số đội đăng ký hợp lệ tại lúc khóa, chia cho số đội tối đa mỗi bảng, rồi làm tròn lên. Các đội được chia đều để số đội giữa các bảng chênh nhau tối đa một đội. Với giải có từ ba đội, số đội trong mỗi bảng phải lớn hơn số suất đi tiếp của bảng đó, và tổng số suất đi tiếp của toàn giải phải có ít nhất hai. Nếu không đáp ứng các điều kiện này thì không được xác nhận cấu hình.

Giải có đúng hai đội: xếp cả hai đội vào một bảng. Số đội tối đa mỗi bảng phải từ hai trở lên và số suất đi tiếp của bảng phải là hai. Hai đội chơi một encounter vòng bảng hai ván đổi phe, rồi cả hai đội còn đủ điều kiện đi tiếp vào chung kết theo cùng luồng chốt bảng và xác nhận nhánh. Chung kết dùng sáu ván chính và các cặp phụ tại mục 6.2. Các quy định về bot, phân hạng, hành chính, Elo và khiếu nại vẫn áp dụng. Với trường hợp này, cho phép đặt cả số đội tối đa và số suất đi tiếp của bảng là hai.

BTC được khóa đăng ký chủ động khi còn ở giai đoạn đăng ký, không phải đợi hạn đóng đã công bố. Khóa lập tức dừng nhận đội mới và chụp danh sách hiện có; chỉ khóa khi danh sách và cấu hình tạo được giải hợp lệ với ít nhất hai đội. Không mở lại hoặc thay đổi danh sách đã khóa bằng thao tác khóa lặp lại.

Giải hiện tại có 14 đội, tối đa 4 đội mỗi bảng và 2 suất đi tiếp mỗi bảng: chia thành 4 bảng gồm 4, 4, 3 và 3 đội. Mỗi bảng chọn 2 đội, tổng cộng 8 suất vào tứ kết. Nếu đủ 8 đội đi tiếp hợp lệ thì nhánh knockout không có BYE.

Ví dụ cấu hình không hợp lệ: giải có 5 đội, tối đa 4 đội mỗi bảng và 2 suất đi tiếp mỗi bảng sẽ tạo hai bảng có 3 và 2 đội. Bảng hai đội không đáp ứng điều kiện phải có nhiều đội hơn số suất đi tiếp. Có thể sửa cấu hình thành tối đa 5 đội mỗi bảng để cả 5 đội thi chung một bảng, rồi chọn 2 đội đi tiếp. Ví dụ này minh họa lỗi cấu hình chia bảng, không quy định số đội tối thiểu của toàn giải.

Số đội tối thiểu để mở giải là 2. Giải có đúng hai đội áp dụng ngoại lệ nêu trên; giải có từ ba đội áp dụng các điều kiện chia bảng thông thường. Không mở giải với dưới hai đội.

### 4.2. Chia hạt giống có thể tái lập

1. Chụp Elo khi khóa đăng ký; đội chưa có Elo dùng 1 000. Thay đổi Elo sau đó không làm đổi bảng.
2. Xếp Elo giảm dần; bằng Elo thì dùng bốc thăm có lưu.
3. Tính trước dung lượng từng bảng; các bảng lớn được gán ID trước các bảng nhỏ.
4. Chia danh sách đội thành các nhóm hạt giống liên tiếp. Số đội tối đa trong mỗi nhóm bằng số bảng.
5. Với từng nhóm hạt giống, bốc thăm thứ tự các bảng còn chỗ và phân lần lượt, mỗi bảng nhận tối đa một đội từ nhóm đó. Khi nhóm hạt giống cuối cùng có ít đội hơn số bảng, chỉ dùng các bảng còn chỗ.
6. Lưu danh sách đầu vào, dung lượng bảng, phiên bản thuật toán, nguồn/seed ngẫu nhiên và kết quả. BTC xác nhận trước khi mở nộp.

Mọi lần bốc thăm trong tài liệu phải có bản ghi gồm lý do, tập lựa chọn hợp lệ, phương pháp ngẫu nhiên, kết quả, thời điểm và người thực hiện. Không bốc lại để chọn kết quả thuận lợi.

### 4.3. Lịch vòng bảng

Mỗi cặp trong cùng bảng gặp nhau đúng một trận hai ván đổi phe. Mỗi đội gặp tất cả các đội còn lại trong bảng. Tổng số trận bằng số đội trong bảng nhân với số đội còn lại, rồi chia đôi. Bảng lẻ có lượt nghỉ, không được cộng điểm. Lịch và phe từng ván được lưu trước khi chạy. Một đội không được chơi hai trận Tournament đồng thời.

## 5. Điểm và xếp hạng vòng bảng

### 5.1. Điểm ván và điểm trận

Điểm ván: thắng 1, hòa 0,5, thua 0. Cộng điểm hai ván để xác định trận: điểm cao hơn là thắng, bằng nhau là hòa. Điểm trận: thắng 3, hòa 1, thua 0. Không cộng điểm ván trực tiếp vào điểm trận.

Chỉ chốt trận thực đấu khi cả hai ván hoàn tất hợp lệ, kể cả khi một ván thua do lỗi bot. Kết quả hành chính dùng quy tắc riêng tại mục 8.

### 5.2. Thứ tự xếp hạng

Xếp các đội còn đủ điều kiện đi tiếp trước các đội thiếu bot bị cấm đi tiếp trong vòng này. Các tiêu chí và phân hạng dưới đây áp dụng trong nhóm đội còn đủ điều kiện. Đội thiếu bot vẫn có các kết quả hành chính theo mục 8 để tính điểm cho đối thủ, nhưng không được gọi vào đấu phân hạng; nếu cần hiển thị thứ tự giữa các đội thiếu bot thì dùng bốc thăm có lưu. Đội đã rút hoặc bị truất quyền được xử lý riêng theo mục 8.3.

1. Tổng điểm trận toàn bảng.
2. Điểm trận đối đầu trong nhóm có cùng tổng điểm trận tại bước 1.
3. Điểm ván đối đầu trong chính nhóm ban đầu đó.
4. Tổng điểm ván toàn bảng.
5. Số trận thắng toàn bảng.

So sánh lần lượt các tiêu chí; không tạo lại nhóm đối đầu nhỏ hơn sau từng bước. Trận bị loại khỏi bảng theo mục 8 không tham gia bất kỳ tiêu chí nào.

Nếu sau bước 5 còn một khối đội bằng nhau và khối đó chiếm ít nhất một vị trí được đi tiếp, toàn bộ đội trong khối chơi phân hạng. Khối bằng nhau nằm hoàn toàn ngoài các vị trí được đi tiếp không chơi phân hạng; bốc thăm có lưu để hoàn thiện thứ tự.

### 5.3. Phân hạng và suất đi tiếp

Mỗi khối cần phân hạng chơi đúng một vòng tròn phụ, mỗi cặp hai ván đổi phe, dùng bot vòng bảng đã khóa. Xếp hạng bằng năm tiêu chí ở mục 5.2, chỉ xét các trận trong vòng tròn phụ. Nếu vẫn bằng, bốc thăm có lưu cho phần còn bằng nhau; không mở vòng tròn phụ thứ hai.

Thứ tự phụ chỉ phân bổ lại các vị trí vốn thuộc khối đó. Điểm phụ không cộng vào bảng chính hoặc các tỷ lệ bảng chính dùng để xếp hạt giống knockout. Trận phụ thực đấu hợp lệ được tính Elo.

Chọn các đội đủ điều kiện có thứ hạng cao nhất trong mỗi bảng, tối đa bằng số suất đi tiếp đã cấu hình cho bảng đó. Đội rút giải, bị loại hoặc không có bot hợp lệ cho vòng bảng không được đi tiếp. Không lấy đội ở bảng khác để bù suất thiếu. Nếu tổng số đội đi tiếp còn một, đội đó là vô địch không qua trận chung kết; nếu không còn đội nào thì kết thúc giải không có vô địch. Các quyết định này không tạo trận hoặc Elo.

## 6. Nhánh và trận knockout

### 6.1. Hạt giống và BYE

Khi có ít nhất hai đội đi tiếp, số vị trí trong nhánh là lũy thừa của hai nhỏ nhất đủ chỗ cho tất cả các đội đó. Xếp hạt giống lần lượt theo:

1. Vị trí trong bảng đã chốt: tất cả đội nhất trước đội nhì, đội nhì trước đội ba, tiếp tục như vậy cho đến khi hết các đội đi tiếp.
2. Điểm trận bảng chính/số trận tính vào bảng chính.
3. Điểm ván bảng chính/số ván quy ước tính vào bảng chính.
4. Số trận thắng bảng chính/số trận tính vào bảng chính.
5. Bốc thăm có lưu.

Mỗi trận bảng không bị loại có hai ván quy ước cho mẫu số, kể cả trận hành chính; các ván quy ước không tạo bản ghi ván thực đấu. Không dùng điểm đấu phân hạng để tính các tỷ lệ. Các tỷ lệ được so sánh chính xác, không dùng số đã làm tròn trên giao diện. Khi mẫu số bằng 0, dùng tỷ lệ 0.

Để đặt hạt giống vào nhánh, bắt đầu danh sách vị trí `[1, 2]`. Mỗi lần tăng gấp đôi số vị trí, thay từng hạt giống bằng một cặp: giữ nguyên hạt giống đó ở trước; hạt giống ở sau bằng tổng số vị trí mới cộng một rồi trừ hạt giống ở trước. Lặp lại đến khi đủ số vị trí cần cho nhánh. Ví dụ, nhánh tám vị trí có thứ tự `[1, 8, 4, 5, 2, 7, 3, 6]`; mỗi hai vị trí liên tiếp tạo một cặp vòng đầu. Hạt giống vượt quá tổng số đội đi tiếp tương ứng với ô trống. Số BYE bằng số vị trí trong nhánh trừ số đội đi tiếp và được cấp cho các hạt giống cao nhất.

Cho phép tái đấu cùng bảng; không đổi vị trí để né đối thủ. Nhánh cố định sau xác nhận. Đội rút hoặc bị loại sau xác nhận tạo ô trống; không xếp hạt giống lại hoặc bổ sung đội đã bị loại.

### 6.2. Sáu ván chính và tối đa bốn ván phụ

Mọi trận knockout, kể cả chung kết, có sáu ván chính. Bốc thăm đội cầm Nam ván 1; đổi phe sau mỗi ván. Mỗi đội cầm Nam ba lần. Chơi đủ sáu ván, kể cả khi đã chắc chắn đội thắng, trừ khi có kết quả hành chính theo mục 8.

Đội có tổng điểm ván chính cao hơn thắng trận. Nếu hòa 3–3, chơi một cặp phụ hai ván đổi phe. Nếu cặp đó hòa, chơi cặp phụ hai ván thứ hai. Đội cầm Nam đầu cặp phụ thứ nhất là đội cầm Bắc ở ván chính đầu tiên; đầu cặp phụ thứ hai đổi lại. Mỗi cặp phụ tự cân bằng phe.

Điểm từng cặp phụ được xét riêng. Nếu một cặp có bên hơn điểm thì bên đó thắng trận và không chơi cặp tiếp theo. Nếu cả hai cặp đều hòa, bốc thăm có lưu để chọn đội đi tiếp; kết quả thực đấu của trận là hòa cho Elo.

Tối đa 10 ván/trận. Không dùng Elo, thành tích vòng bảng, thời gian chạy hoặc số quân còn lại để quyết định trận. Không có trận tranh hạng ba trong phiên bản này.

## 7. Nộp bài, kiểm tra và khóa bot

### 7.1. Quota và chọn bài

Mỗi đội được tối đa ba lần nộp Tournament/vòng thi. Một yêu cầu đã được server nhận, cấp submission ID và lưu mã nguồn tiêu tốn một lượt, kể cả sau đó kiểm tra thất bại. Yêu cầu bị từ chối trước khi tạo submission vì ngoài cửa sổ, sai định dạng hoặc quá kích thước không tiêu tốn lượt. Retry xử lý cùng submission không tiêu tốn lượt mới. Hệ thống phải chống việc gửi đồng thời vượt quota hoặc tạo trùng bài khi gửi lại cùng yêu cầu.

Mở cửa sổ riêng trước vòng bảng và trước từng cấp knockout. Vòng bảng cùng mọi trận phân hạng dùng chung một quota và một bot khóa. Một cấp knockout dùng cùng bot cho tất cả ván chính, ván phụ và lần chạy lại.

Sau khi đóng cửa sổ, xử lý xong mọi bài đã nhận đúng hạn rồi mới khóa. Bài có thể chuyển `ready` sau giờ đóng nếu đã được nhận đúng hạn. Chọn bài `ready` hợp lệ có thời điểm nhận mới nhất; nếu trùng thời điểm, dùng thứ tự nhận bất biến của server. Một bài mới thất bại không làm mất bài `ready` cũ trong cùng cửa sổ.

Nếu cửa sổ không có bài `ready`, kế thừa bot khóa ở vòng Tournament trước khi bot đó tương thích phiên bản luật và môi trường. Không tự chọn bài Practice hoặc Daily thay thế. Không có bài đủ điều kiện thì xử lý theo mục 8. Không có ngoại lệ sửa bot giữa vòng do bot chơi kém hoặc phát sinh lỗi runtime.

### 7.2. Ý nghĩa của `ready`

Một bài được gắn `ready` sau khi kiểm tra định dạng, cú pháp/biên dịch và vượt qua kiểm tra giao thức cơ bản ở cả hai phe. Kiểm tra phải dùng phiên bản protocol và sandbox của vòng, gửi nhiều lượt hợp lệ để kiểm tra việc đọc liên tục, phản hồi, flush và tiếp tục chạy. Kiểm tra không chấm sức mạnh bot và không chứng nhận bot sẽ đúng ở mọi thế cờ.

Bot kiểm tra được chạy với bộ nhớ mới, không dùng kết quả hoặc trạng thái của Practice để cấp `ready`. Khi thay đổi luật hoặc môi trường không tương thích, phải kiểm tra lại điều kiện tương thích trước khi cho kế thừa; không tự đổi nội dung submission.

Giao diện phải hiển thị cửa sổ nộp, số lượt đã dùng, trạng thái xử lý, lỗi kiểm tra, submission được chọn, ID bot khóa, phiên bản luật và lý do kế thừa hoặc không đủ điều kiện.

## 8. Thiếu bot, bỏ cuộc và kỷ luật

### 8.1. Thiếu bot khi khóa vòng

Đội không có bot đủ điều kiện khi khóa không được thi hoặc đi tiếp trong vòng đó. Trong bảng đã xác nhận:

| Tình huống | Điểm trận | Điểm ván quy ước | Elo |
| --- | --- | --- | --- |
| Một đội thiếu bot | Đội đủ điều kiện 3, đội thiếu bot 0 | 2–0 | Không tính |
| Cả hai đội thiếu bot | Mỗi đội 0 | Mỗi đội 0 | Không tính |

Kết quả hành chính một bên thua được tính là một trận thắng của bên đủ điều kiện cho xếp hạng. Hai bên cùng thiếu bot không có đội thắng. Không tạo nước đi, ván thực đấu hoặc replay giả cho các kết quả này.

Ở knockout, đội duy nhất đủ điều kiện đi tiếp hành chính; nếu cả hai không đủ điều kiện thì vị trí đi tiếp để trống. Khi vòng sau có một đội và một vị trí trống, đội đó đi tiếp không thực đấu; hai vị trí trống tiếp tục tạo vị trí trống. Nếu cuối nhánh không còn đội đủ điều kiện thì giải không có vô địch.

### 8.2. Lỗi bot trong khi thực đấu

Bot đã đủ điều kiện nhưng lỗi JSON, đi sai, crash hoặc hết thời gian thì thua ván đó. Đây là kết quả thực đấu hợp lệ, không phải thiếu bot hoặc lỗi hạ tầng. Tiếp tục các ván còn lại bằng bot khóa và khởi tạo tiến trình mới ở mỗi ván. Lỗi runtime không tự loại đội khỏi giải và không mở thêm quyền nộp.

### 8.3. Bỏ cuộc, rút giải và truất quyền

Đội chủ động bỏ cuộc một trận đang hoặc sắp thực đấu nhận kết quả hành chính thua cả trận; đối thủ nhận thắng. Trong vòng bảng dùng điểm hành chính 3 và 2–0; hai bên cùng bỏ cuộc nhận mỗi bên 0 điểm trận và 0 điểm ván. Ở knockout chỉ đội còn đủ điều kiện được đi tiếp. Giữ lịch sử ván đã chơi để tra cứu nhưng không tính Elo cho trận bị thay bằng kết quả hành chính.

Nếu đội rút toàn bộ giải hoặc bị truất quyền trước khi bảng được chốt cuối cùng, loại đội khỏi xếp hạng và bỏ toàn bộ trận của đội đó khỏi điểm bảng cùng mọi tiêu chí đối đầu và hạt giống. Trận thực đấu đã hoàn tất hợp lệ vẫn giữ Elo. Hủy các trận chưa hoàn tất với đội đó, không tính Elo. Kết quả hành chính chưa tạo Elo nên không có Elo để giữ.

Sau khi bảng đã chốt nhưng trước khi nhánh được xác nhận, bỏ đội không còn đủ điều kiện và chọn đội đủ điều kiện tiếp theo trong bảng theo thứ tự đã chốt; không tính lại các trận của bảng. Sau khi nhánh được xác nhận, áp dụng ô trống ở mục 6.1, không thay đội hoặc sửa lại bảng đã chốt.

BTC có thể truất quyền khi xác minh có gian lận danh tính, can thiệp kết quả, cố ý phá sandbox hoặc vi phạm điều kiện tham dự đã công bố. Quyết định lưu người thực hiện và cho phép khiếu nại theo mục 11; căn cứ và bằng chứng dạng văn bản là tùy chọn. Lỗi bot thông thường không phải căn cứ tự động truất quyền.

## 9. Giao thức bot và sandbox

### 9.1. Mã nguồn và biên dịch

Chấp nhận Python 3 (`python`) hoặc C++17 (`cpp`), một tệp UTF-8 tối đa **100 000 byte**. Nộp hàm `choose_move(state)` cho Python hoặc `Move chooseMove(const BoardState& state)` cho C++. Có thể thêm hàm phụ và dùng thư viện chuẩn. Không nộp `main()`, vòng đọc input hoặc mã in nước đi; hệ thống cung cấp phần chạy, phân tích JSON và xuất kết quả. Không nhận ZIP, project nhiều tệp hoặc file thực thi. Không cài thêm thư viện lúc chạy. C++ không cần `nlohmann/json`.

Lệnh biên dịch chuẩn:

```text
g++ -std=c++17 -O2 -pipe -static -s
```

Hệ thống ghép kiểu dữ liệu và phần chạy với hàm C++ trước khi biên dịch bằng các cờ trên. File thực thi tối đa **5 000 000 byte**. BTC công bố phiên bản Python, g++, image sandbox và cấu hình trước cửa sổ nộp.

### 9.2. Input và output

Phần chạy do hệ thống cung cấp đọc một dòng JSON từ `stdin` mỗi lượt, gọi hàm của đội một lần, rồi xuất một dòng JSON ra `stdout` và flush. Đội chỉ xử lý trạng thái đã được phân tích và trả về nước đi. Tiến trình chạy liên tục trong một ván; không giữ giả định bộ nhớ qua ván. Không in debug ra `stdout`; dùng `stderr`.

Bot nhận một JSON object qua `stdin` mỗi khi đến lượt, với các trường bắt buộc sau:

| Trường | Kiểu JSON | Ý nghĩa |
| --- | --- | --- |
| `protocol_version` | integer | Luôn là `2` |
| `move_number` | integer | Số nước cá nhân hiện tại, bắt đầu từ `1`, tăng sau mỗi nước của bất kỳ phe nào |
| `side` | string | Phe của bot: `"south"` hoặc `"north"` |
| `board_size` | integer | Luôn là `9` |
| `pieces` | array of objects | Toàn bộ quân còn sống của cả hai phe |
| `legal_moves` | array of objects | Toàn bộ nước hợp lệ của bot tại lượt đó |

Bot chỉ nhận input khi đến lượt mình; vì vậy hai input liên tiếp của cùng bot thường cách nhau hai số nước. Không dựa vào thứ tự phần tử trong `pieces` hoặc `legal_moves` để xác định phe, loại quân hay ưu tiên nước đi.

Mỗi phần tử của `pieces` có các trường:

| Trường | Kiểu JSON | Ý nghĩa |
| --- | --- | --- |
| `id` | string | ID quân, ví dụ `"S01"`; không đổi trong ván |
| `side` | string | `"south"` hoặc `"north"` |
| `type` | string | `"rock"`, `"scissors"` hoặc `"paper"` |
| `x` | integer | Cột: `a` là `0`, đến `i` là `8` |
| `y` | integer | Hàng: `1` là `0`, đến `9` là `8` |

Quân đã bị ăn không xuất hiện trong `pieces`. Tọa độ chuỗi `a1` tương ứng `x=0, y=0`; `i9` tương ứng `x=8, y=8`.

Mỗi phần tử của `legal_moves` có các trường:

| Trường | Kiểu JSON | Ý nghĩa |
| --- | --- | --- |
| `piece_id` | string | ID quân di chuyển |
| `from` | string | Ô xuất phát, ví dụ `"c1"` |
| `to` | string | Ô đích, ví dụ `"d2"` |
| `capture` | string hoặc null | ID quân địch bị ăn, hoặc `null` nếu không ăn quân |

Ví dụ input giữa ván, mỗi bên còn ba quân. Ví dụ chứa đầy đủ quân sống và nước hợp lệ của thế cờ này. JSON dưới đây được trình bày nhiều dòng để dễ đọc; khi truyền thực tế, toàn bộ object nằm trên một dòng và kết thúc bằng ký tự xuống dòng:

```json
{
  "protocol_version": 2,
  "move_number": 157,
  "side": "south",
  "board_size": 9,
  "pieces": [
    {"id":"S03","side":"south","type":"rock","x":0,"y":0},
    {"id":"S02","side":"south","type":"scissors","x":1,"y":0},
    {"id":"S01","side":"south","type":"paper","x":2,"y":0},
    {"id":"N02","side":"north","type":"rock","x":3,"y":1},
    {"id":"N03","side":"north","type":"scissors","x":7,"y":8},
    {"id":"N01","side":"north","type":"paper","x":8,"y":8}
  ],
  "legal_moves": [
    {"piece_id":"S03","from":"a1","to":"a2","capture":null},
    {"piece_id":"S03","from":"a1","to":"b2","capture":null},
    {"piece_id":"S02","from":"b1","to":"a2","capture":null},
    {"piece_id":"S02","from":"b1","to":"b2","capture":null},
    {"piece_id":"S02","from":"b1","to":"c2","capture":null},
    {"piece_id":"S01","from":"c1","to":"b2","capture":null},
    {"piece_id":"S01","from":"c1","to":"c2","capture":null},
    {"piece_id":"S01","from":"c1","to":"d1","capture":null},
    {"piece_id":"S01","from":"c1","to":"d2","capture":"N02"}
  ]
}
```

Output là một JSON object có đúng hai trường chuỗi `piece_id` và `to`, không khóa trùng:

```json
{"piece_id":"S01","to":"d2"}
```

Cặp giá trị phải khớp một phần tử của `legal_moves`. Với input ví dụ, phản hồi trên chọn quân Lá `S01` từ `c1` đến `d2` để ăn quân Đấm `N02`. Không gửi `from` hoặc `capture` trong output.

Mỗi lượt đúng một phản hồi; không gửi trước phản hồi cho lượt chưa nhận. Dòng tối đa 256 byte UTF-8, gồm ký tự xuống dòng. Phần chạy hệ thống tự flush sau mỗi phản hồi. Hàm phải có đúng tên và chữ ký nêu dưới đây. Khi ván kết thúc, tiến trình được dừng; ván mới khởi động tiến trình mới. Engine không gọi hàm khi không có nước hợp lệ.

#### C++17: trạng thái có kiểu sẵn

Chỉ nộp hàm và các hàm phụ cần thiết. Các kiểu sau do hệ thống cung cấp, không cần khai báo lại:

```cpp
struct Piece {
    std::string id, side, type;
    int x, y;
};
struct Move {
    std::string piece_id, from, to;
    std::optional<std::string> capture;
};
struct BoardState {
    int protocol_version, move_number, board_size;
    std::string side;
    std::vector<Piece> pieces;
    std::vector<Move> legal_moves;
    std::array<std::array<std::optional<Piece>, 9>, 9> board;
    const Piece* at(int x, int y) const;
};
```

`state.at(x, y)` trả về con trỏ tới quân tại ô đó, hoặc `nullptr` nếu ô trống hay ngoài bàn. `state.board[y][x]` là `std::optional<Piece>`; kiểm tra có giá trị trước khi dùng `->id`, `->side`, `->type`. Chỉ truy cập mảng với tọa độ 0 đến 8. Trạng thái được dựng mới mỗi lượt, nên quân bị ăn không còn trong `pieces` hoặc `board`. Con trỏ từ `at()` chỉ dùng trong lần gọi hàm hiện tại.

Mã mẫu nộp được ngay:

```cpp
// API: chooseMove(const BoardState& state) returns Move.
// state: side, move_number, board_size, pieces, legal_moves.
// Piece: id, side, type, x, y (coordinates 0..8).
// Move: piece_id, from, to, capture (optional<string>).
// state.at(x,y): const Piece* or nullptr for an empty square.
// state.board[y][x]: optional<Piece>. Types are provided.
// Return a legal move. No main() or input/output code.
Move chooseMove(const BoardState& state) {
    // Viết thuật toán của bạn tại đây.
    return state.legal_moves.front();
}
```

Trả về một `Move` trong `state.legal_moves`. Có thể ưu tiên nước ăn bằng cách kiểm tra `move.capture.has_value()`. Hệ thống chỉ xuất `piece_id` và `to` của nước được trả về; judge kiểm tra nước đó có hợp lệ hay không.

#### Python 3: trạng thái dạng dict

`state` là dict theo bảng trường JSON phía trên. Hàm trả về một phần tử của `state["legal_moves"]` hoặc dict có hai chuỗi `piece_id` và `to`. Hệ thống tự bỏ các trường `from`, `capture` khi xuất JSON.

```python
# API: choose_move(state) returns a move dict.
# state: side, move_number, board_size, pieces, legal_moves.
# Piece: id, side, type, x, y (coordinates 0..8).
# Move: piece_id, from, to, capture (None or enemy ID).
# Return a legal move, or {"piece_id": ..., "to": ...}.
# Judge handles input/output. Add helper functions as needed.
def choose_move(state):
    # Viết thuật toán của bạn tại đây.
    return state["legal_moves"][0]
```

### 9.3. Giới hạn

| Tài nguyên | Giới hạn |
| --- | --- |
| Phản hồi mỗi lượt thông thường | 1,5 giây thời gian thực |
| Phản hồi lượt đầu mỗi bot, gồm khởi động | 15 giây thời gian thực, thay giới hạn 1,5 giây ở lượt đó |
| RAM mỗi bot | 256 MB |
| CPU mỗi bot | Tương đương 1 CPU |
| Tiến trình mỗi bot | 32 |
| Vùng tạm mỗi bot | 32 MB |
| Biên dịch g++ trong sandbox | 25 giây |
| Worker chờ biên dịch | 35 giây |

Thời gian lượt thường tính từ khi hệ thống gửi xong input đến khi nhận đủ dòng output. Lượt đầu tính từ khi bắt đầu khởi chạy bot đến khi nhận đủ phản hồi đầu tiên; không cộng thêm 1,5 giây. Worker hỏng hoặc không khởi tạo được sandbox không tự trở thành lỗi bot.

JSON không hợp lệ, trường sai, dòng quá dài, phản hồi thừa, nước không hợp lệ, crash, thoát sớm trong khi ván chưa kết thúc hoặc quá hạn đều làm bot thua ván. Không có nước hợp lệ thì engine kết luận hòa trước khi gọi bot. Không cho bot truy cập mạng, bí mật, dữ liệu đội khác hoặc can thiệp sandbox.

## 10. Elo

Elo thuộc tài khoản đội, khởi tạo 1 000 tại mốc bật rating đã công bố. Không tự tính lại toàn bộ lịch sử trước mốc đó. Elo dùng để chia hạt giống và xếp hạng sức mạnh; không thay điểm giải.

Với RA, RB là Elo trước trận:

```text
EA = 1 / (1 + 10 ** ((RB - RA) / 400))
delta = 32 * (SA - EA)
RA_new = RA + delta
RB_new = RB - delta
```

SA = 1 khi A thắng thực đấu, 0,5 khi hòa thực đấu, 0 khi thua thực đấu. Lưu số thập phân và chỉ làm tròn khi hiển thị.

- Daily hợp lệ tạo một sự kiện Elo cho cả trận một ván.
- Trận vòng bảng hoặc phân hạng hợp lệ tạo một sự kiện sau khi đủ hai ván.
- Knockout tạo một sự kiện sau khi đủ sáu ván chính và các cặp phụ cần thiết. Nếu ván chính có kết quả thắng/thua thì dùng kết quả đó; nếu phải chơi phụ thì dùng kết quả cặp phụ quyết định. Nếu đi tiếp bằng bốc thăm sau hai cặp hòa thì SA = 0,5.
- Thua ván do lỗi bot là kết quả hợp lệ và được tính trong kết quả trận.
- Practice, Challenge, BYE, trận hủy và kết quả hành chính không tạo Elo.
- Trận chờ xử lý hạ tầng chưa tạo Elo. Sau khi chạy lại hoàn tất hợp lệ, trận tạo đúng một sự kiện.

Mỗi encounter chỉ có tối đa một sự kiện Elo đang hiệu lực, bất kể retry hoặc replay bao nhiêu lần. Lưu thứ tự sự kiện bất biến. Không chạy chồng các trận tính Elo của cùng đội; trận sau dùng Elo đã chốt của trận trước.

Sửa kết quả đã tính hoặc chuyển trận thành kết quả hành chính phải vô hiệu hóa sự kiện cũ và tái tính chuỗi Elo phụ thuộc theo thứ tự đã lưu. Rút đội khỏi bảng theo mục 8.3 tự nó không xóa Elo của các trận thực đấu đã hoàn tất hợp lệ.

## 11. Vận hành, sự cố, khiếu nại và thay đổi luật

### 11.1. Trách nhiệm vận hành

BTC xác nhận cấu hình, bảng, nhánh, cửa sổ nộp, khóa bot, các mốc chốt kết quả và xử lý sự cố. Worker nhận job từ hàng chờ chung; `WORKER_ID` chỉ là nhãn, không bảo đảm một đội chạy trên một máy cụ thể. Website/API và database có thể ở Render; bot chạy trong Docker của worker nhận job. Không có worker thì job có thể chờ dù website vẫn truy cập được.

BTC có thể bật **Tự động điều hành khi đến hạn** để ủy quyền worker khóa đăng ký, xác nhận bảng/nhánh đã tính theo luật, mở cửa sổ nộp, khóa bot, chạy các lượt, phân hạng và chốt vòng. Worker dùng các kiểm tra thông thường, không tự force, giữ thời hạn khiếu nại 30 phút ở vòng bảng và không bỏ qua bài đang kiểm tra, hold hoặc lỗi hạ tầng. Loại trực tiếp chạy liên tục trong cùng vòng, kể cả các cặp phụ; kết quả đủ ván được chốt ngay nếu không có hold, không chờ 30 phút tự động. Các thao tác tự động được lưu với người thực hiện `scheduler`; bật/tắt chế độ cũng được ghi nhật ký.

Giờ đã lưu của vòng được giữ nguyên. Với vòng mới chưa có lịch, chế độ tự động mở cửa sổ ngay khi vòng được tạo và đóng sau thời lượng BTC đặt, mặc định 1440 phút. Mỗi vòng loại trực tiếp (tứ kết, bán kết, chung kết hoặc vòng trước đó) có cửa sổ nộp riêng, chỉ mở sau khi mọi suất đi tiếp của vòng trước đã chốt; không mở riêng cho đội thắng sớm. Kế thừa bot tương thích theo mục 7 khi không có bài ready mới. BTC có thể tắt tự động ở bất kỳ giai đoạn hoạt động nào; các ván đã đưa vào hàng chờ vẫn được xử lý. Khi khôi phục worker, hệ thống tiếp tục từ trạng thái đã lưu, không tạo trùng ván hoặc suất đi tiếp.

Replay là dựng lại nước đi đã lưu, không phải video hoặc luồng trực tiếp. Bot của đội không cần người chơi trực tuyến khi thi đấu.

### 11.2. Lỗi hạ tầng và hủy trận

Giữ nguyên ván đã hoàn tất hợp lệ. Kiểm tra và chạy lại riêng ván bị ảnh hưởng bằng đúng submission khóa, phe, thế cờ, phiên bản luật và môi trường của ván gốc. Không cho sửa bot trong lúc chờ chạy lại. Nếu cần chuyển worker, môi trường phải tương đương cấu hình đã khóa.

Lưu ván bị ảnh hưởng, worker, cấu hình, lần thử và người thao tác; lý do dạng văn bản là tùy chọn. Mã thoát 64/70/71 chỉ là tín hiệu kỹ thuật, không đủ để tự kết luận đội có lỗi.

BTC có thể công bố hạn xử lý sự cố và lịch hoãn; để trống chính sách không chặn mở hoặc xác nhận giải. Nếu đã công bố hạn mà chưa khôi phục được, hoãn lượt hoặc vòng liên quan và công bố lịch mới. Không bốc thăm, tự cộng thắng hoặc biến lỗi hạ tầng thành lỗi bot để chạy tiếp nhánh.

Chỉ được hủy encounter mà không thay thế khi hủy toàn bộ lượt/vòng/giải liên quan, hoặc khi đội rút/bị loại theo mục 8. Hủy do hạ tầng không tạo điểm trận hay Elo; ván hợp lệ còn lại được giữ để tra cứu và không đủ để tự quyết định trận thiếu ván. Nếu hủy vòng/giải, không công bố kết quả vô địch dựa trên các encounter chưa hoàn tất. Phạm vi hủy được lưu và công khai; lý do dạng văn bản là tùy chọn.

### 11.3. Khiếu nại và chốt kết quả

Công bố kết quả tạm cùng điểm, lý do kết thúc, submission ID và replay/log được phép xem. Ở vòng bảng và phân hạng, hạn khiếu nại là 30 phút kể từ thời điểm muộn hơn giữa công bố kết quả và dữ liệu đối chiếu được cung cấp cho đội. Với kết quả hành chính vòng bảng không có replay, dùng thời điểm công bố quyết định cùng căn cứ. Loại trực tiếp không có khoảng chờ khiếu nại tự động: kết quả đủ ván có thể chốt ngay; báo cáo được BTC ghi thành hold sẽ dừng tiến trình liên quan để xử lý.

BTC có thể công bố kênh chính thức để đội nộp khiếu nại; trường liên hệ trên website là tùy chọn. BTC lưu người xử lý, loại quyết định và thời điểm; nội dung quyết định và bằng chứng là tùy chọn. Không chốt suất đi tiếp của kết quả còn trong hạn hoặc đang có khiếu nại hợp lệ. Nếu sửa kết quả vòng bảng, công bố dữ liệu mới và mở lại 30 phút cho phần đã sửa. Sửa kết quả loại trực tiếp trong lúc xử lý báo cáo phải giữ hold cho tới khi có quyết định giải quyết. Không sửa trực tiếp suất đi tiếp của vòng đã chốt. Hoãn lịch phụ thuộc khi cần.

Bảng được chốt sau khi mọi trận cần thiết, phân hạng và khiếu nại liên quan đã hoàn tất. Chỉ xác nhận nhánh sau mốc này và xử lý đội rút trước xác nhận. Mỗi vòng knockout chỉ mở vòng tiếp theo sau khi tất cả các suất đi tiếp của vòng hiện tại được chốt. Khi bật tự động, các ván chạy liên tục; chỉ dừng để nhận bot ở ranh giới vòng, xử lý hold/báo cáo hoặc lỗi hạ tầng.

### 11.4. Phiên bản luật

Công bố luật và môi trường trước giải. Không đổi luật giữa các lượt của cùng vòng bảng, trong đấu phân hạng hoặc giữa các ván một encounter. Thay đổi chỉ áp dụng từ vòng thi tiếp theo sau thông báo và cửa sổ nộp bot mới.

Không hồi tố luật mới để sửa kết quả đã hợp lệ theo luật lúc thi đấu. Nếu luật hoặc môi trường mới không tương thích, không tự kế thừa bot cũ; phải kiểm tra tương thích hoặc yêu cầu bài mới trước khóa. Mọi thay đổi phải sửa mục tương ứng trong chính tài liệu này, tăng phiên bản và ghi rõ ngày hiệu lực cùng phạm vi áp dụng. Không nối thêm phương án trái với nội dung đang giữ.

## 12. Thể thức giải 14 đội

Với giải có 14 đội, tối đa 4 đội mỗi bảng và 2 suất đi tiếp mỗi bảng, chia bảng A và B mỗi bảng 4 đội, bảng C và D mỗi bảng 3 đội. Mỗi bảng chọn 2 đội đủ điều kiện đi tiếp, tạo nhánh tứ kết 8 đội không có BYE khi đủ suất.

Đội ở bảng 4 đội chơi 3 trận, tương ứng 6 ván vòng bảng; đội ở bảng 3 đội chơi 2 trận, tương ứng 4 ván. Xếp hạng trong bảng theo mục 5; so hạt giống giữa các bảng khác kích thước dùng các tỷ lệ ở mục 6.1.

| Giai đoạn | Số trận | Số ván chính |
| --- | --- | --- |
| Bảng A và B: mỗi bảng 6 trận hai ván | 12 | 24 |
| Bảng C và D: mỗi bảng 3 trận hai ván | 6 | 12 |
| Tổng vòng bảng | 18 | 36 |
| Tứ kết | 4 | 24 |
| Bán kết | 2 | 12 |
| Chung kết | 1 | 6 |
| Tổng toàn giải | 25 | 78 |

Tổng toàn giải gồm 18 trận vòng bảng và 7 trận knockout, chưa gồm đấu phân hạng và ván phụ. Bảy trận knockout có thể thêm tối đa 28 ván phụ, nên tổng tối đa 106 ván nếu không có đấu phân hạng, hủy hoặc kết quả hành chính.
