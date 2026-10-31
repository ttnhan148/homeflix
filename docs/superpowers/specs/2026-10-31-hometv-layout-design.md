# HomeTV — Bố cục giống HomeFlix, lọc Alive, trạng thái kênh — Design Spec

> **Ngày:** 2026-10-31
> **Trạng thái:** Chờ review spec
> **Thuộc dự án:** HomeTV, bổ sung sau gói 4. Nền: `docs/superpowers/specs/2026-10-31-hometv-ui-design.md` (gói 3) và `docs/superpowers/specs/2026-10-31-hometv-health-design.md` (gói 4).

## Mục tiêu

Giao diện HomeTV có bố cục và giao diện play giống HomeFlix: thanh tab Home / Tìm kiếm / Tủ kênh, hàng "Kênh xem gần đây", thanh lọc, lưới kênh, và trang play kiểu theatre kèm danh sách phát cuộn tối đa 10 dòng. Thêm bộ lọc **Alive** và hiện trạng thái sống/chết của kênh.

## Nguyên tắc: dùng lại code HomeFlix, không viết lại

Chủ dự án muốn tiết kiệm token và giữ hai bên đồng nhất. Vì vậy:

- Phần nào HomeFlix đã có thì **tách thành hàm hoặc macro dùng chung** trong `templates/index.html`, cho HomeFlix gọi lại và HomeTV gọi cùng hàm đó. Không sao chép, không viết bản song song.
- Hàm tách ra phải giữ nguyên hành vi mặc định của HomeFlix. Mỗi lần tách có test hồi quy hiện có (hoặc test mới nhỏ) chứng minh HomeFlix không đổi.
- Khi nào HomeFlix sau này sửa hàm dùng chung thì HomeTV tự đổi theo. Đó là mục đích.
- `static/hometv.js` chỉ giữ phần riêng của kênh: tải dữ liệu, trạng thái, phát stream dự phòng, yêu thích, remote TV. Mọi phần dựng DOM đã có bên HomeFlix thì gọi lại.

## Quyết định đã chốt

| Chủ đề | Quyết định |
|---|---|
| Tab | Home = Kênh xem gần đây + lọc + lưới. Tìm kiếm = trang riêng. Tủ kênh = Yêu thích + Xem gần đây |
| Thứ tự tab | Giữ đúng thứ tự từng thanh của HomeFlix. Thanh tab dưới (điện thoại): Home, Tìm kiếm, Tủ kênh. Thanh header (TV, máy tính): Home, Tủ kênh, Tìm kiếm (nút thứ ba thay "Nhập link") |
| Alive | Kênh có ít nhất một stream hợp lệ vừa kiểm thành công (`stream_health.ok = 1`). Kênh chưa kiểm là `unchecked`, không lọt bộ lọc Alive |
| Bộ kiểm tắt (server ngoài Việt Nam) | Ẩn nút lọc Alive và ẩn chấm trạng thái |
| Danh sách phát | Danh sách kênh đang lọc, tối đa 10 dòng hiển thị, cuộn bên trong, bấm để chuyển kênh |
| Hardening | Không đổi. Vẫn nằm trong `docs/superpowers/backlog-hometv.md` |

## Server

### Trạng thái kênh (`hometv/catalog.py`)

`list_channels(now, use_health)` thêm trường `status` cho mỗi kênh:

| Giá trị | Điều kiện (chỉ khi `use_health` bật) |
|---|---|
| `alive` | có ít nhất một stream hợp lệ với `stream_health.ok = 1` |
| `dead` | có stream đã kiểm, không stream nào `ok = 1` (kênh vẫn hiện vì chưa đủ `FAIL_HIDE` lần) |
| `unchecked` | không stream hợp lệ nào có dòng trong `stream_health` |

Khi `use_health` tắt, `status` là `null` (không có dữ liệu để nói). Tính trong cùng một truy vấn bằng `MAX(h.ok)` và `COUNT(h.stream_id)` trên các stream hợp lệ, không thêm truy vấn thứ hai.

### API công khai (`hometv/public_api.py`)

`GET /api/hometv/channels` đổi từ mảng thành đối tượng `{"health": bool, "channels": [...]}`. `health` là `geo.current().enabled`. Client dùng cờ này để ẩn lọc Alive và chấm trạng thái. `static/hometv.js` là nơi duy nhất gọi route này; cập nhật cùng lúc, cùng test.

## Phần dùng chung tách từ HomeFlix (`templates/index.html`)

| Cần | HomeFlix đang có | Cách tách |
|---|---|---|
| Chuyển tab | `switchTab(tabId)` cố định theo id | Thêm móc: khi `HFTV.active()`, ánh xạ `search-tab` → `tv-home-tab`, `saved-tab` → `tv-saved-tab`, `player-tab` → `tv-search-tab`, rồi chạy phần còn lại của `switchTab` như cũ (kích hoạt nút, `.tab-content.active`, `syncFloatTabs`, `initTabbing`) và gọi `HFTV.onTab(tabId)` để vẽ nội dung. Nút tab giữ nguyên, đổi nhãn bằng thuộc tính `data-tv-label` (hàm `HFTV.enter/leave` đổi `textContent`), không nhân đôi nút |
| Thẻ trong lưới | `buildMovieCard(movie)` | Tách `buildPosterCard({ title, meta, imgSrc, onClick })` (khối `poster-wrap` + `movie-info`). `buildMovieCard` gọi nó. HomeTV gọi nó rồi gắn thêm sao yêu thích và chấm trạng thái. Chỗ ảnh logo lỗi thì `HFTV` thay bằng chữ cái đầu như hiện nay |
| Hàng "Xem tiếp" | `buildContinueCard(m, className)` | Thêm tham số thứ ba `h` (bộ xử lý): `{ poster, meta, pct, label, onPlay, swipe }`. Mặc định là bộ xử lý phim hiện tại. HomeTV truyền bộ riêng: ảnh là logo, `meta` là nhóm, `pct` bằng 0 (ẩn thanh tiến độ), `swipe: false`, `onPlay` mở kênh |
| Khối markup Home | `<section class="home-section"> … sec-head …` | Jinja macro `continue_section(id, title, link)` ở đầu `index.html`, dùng cho `homeContinue` và cho hàng của HomeTV |
| Thanh lọc | `filter-bar`, `filter-dropdown`, `filter-menu`, `grid-tabs-nav`, `result-count` | Jinja macro `filter_dropdown(type, label)`. Tách phần cuối của `renderFilterOptions` thành `renderFilterMenu(menu, options, current, onPick)`. `toggleFilterDropdown`, `closeAllFilterDropdowns`, `clearAllFilters` giữ nguyên cách dùng; thêm bảng đăng ký `filterProviders[type]` để loại lọc nào đăng ký (HomeFlix: category/country/year, HomeTV: `group`) thì dùng chung đóng/mở menu và đóng khi bấm ra ngoài |
| Khung player | `playerHeader` + `.player-wrapper` + `<video>` + `player-error` trong `globalPlayerContainer` | Jinja macro `player_shell(ids)` sinh nút quay lại, tiêu đề, khung video và hộp lỗi. `globalPlayerContainer` gọi macro với id hiện có (không đổi id), kèm các thành phần riêng của phim (AirPlay, bỏ qua mở đầu, tự qua tập) qua `{% call %}`. `tvPlayer` gọi macro với id `tv*`. Chỉ markup được dùng chung; logic phát của phim (tiến độ, cast, AirPlay) **không** chạy cho HomeTV |
| Danh sách phát | `renderEpisodeList(containerEl, opts)` | Thêm tuỳ chọn, mặc định giữ nguyên: `unit` (nhãn đơn vị, mặc định `tập`), `rows` (nếu có: thêm lớp `ep-list-rows`, một cột, cao đúng `rows` dòng rồi cuộn, tắt phân trang), `decorate(btn, ep)` (móc để gắn chấm trạng thái), `nameOf(ep)` (mặc định `HFUtils.epShort(ep.name)`). Kênh được chuyển thành `{ name, link_m3u8: String(channel.id) }` và kênh đang phát đánh dấu `watchStates[id] = 'watching'` để dùng lớp `is-watching` sẵn có |
| CSS theatre | Quy tắc `body.player-active …` | Mở rộng bộ chọn thành `body.player-active, body.tv-player-active` cho các quy tắc định dạng khung (độ rộng, bo góc, `video` cao tối đa). Các quy tắc khác của `player-active` (ví dụ ẩn header) kiểm từng cái khi lập plan |

Phần tách này đổi code HomeFlix hiện có. Chạy lại toàn bộ test giao diện HomeFlix sau mỗi lần tách; không được đỏ.

## Giao diện HomeTV

Mọi khối dưới đây dựng bằng macro và hàm dùng chung ở trên, class CSS của HomeFlix. CSS riêng cho HomeTV chỉ gồm chấm trạng thái, sao yêu thích và `ep-list-rows`.

### Thanh tab

Trong mode HomeTV, thanh header và thanh tab dưới hiện lại (bỏ quy tắc `body.mode-hometv .tabs-nav, .float-tabbar { display: none }`). Nhãn đổi theo `data-tv-label`: Home, Tìm kiếm, Tủ kênh. Nút chuyển HomeFlix ⇄ HomeTV giữ nguyên. Tab Cài đặt hoạt động như hiện nay (rời mode HomeTV khi bấm).

### Tab Home (`tv-home-tab`)

1. Hàng **Kênh xem gần đây**: tối đa 3 thẻ `buildContinueCard`, kênh mới xem nhất đứng đầu (`is-lead`). Nút "Tủ kênh" ở đầu hàng sang tab Tủ kênh. Ẩn khi chưa xem kênh nào.
2. Thanh lọc, dựng bằng `grid-tabs-nav` và `filter-bar`:
   - Hàng nút: **Tất cả** và **Alive** (ẩn Alive khi `health` là false).
   - Hàng dropdown: **Nhóm** (nhóm kênh hiện có, kèm "Tất cả"), nút **Xóa lọc**, số kênh `result-count`.
3. Lưới kênh dựng bằng `buildPosterCard`, tải thêm theo lô 60 như hiện nay.

Các bộ lọc ghép với nhau: Alive ∧ Nhóm. Thẻ kênh: góc poster có **chấm trạng thái** (xanh `alive`, đỏ `dead`, xám `unchecked`; không hiện khi `health` false) và sao yêu thích như hiện nay. Chấm có `aria-label` ("Đang sống", "Đã chết", "Chưa kiểm") để không chỉ truyền đạt bằng màu.

### Tab Tìm kiếm (`tv-search-tab`)

`search-bar` của HomeFlix (cùng markup và class) và một lưới kết quả. Tìm theo tên kênh không dấu (`HFHomeTV.filterChannels`), bộ lọc Alive áp dụng nếu đang bật. Trống thì hiện "Không có kênh nào khớp." bằng class `filter-empty`.

### Tab Tủ kênh (`tv-saved-tab`)

Hai mục kiểu `library-section`: **Yêu thích** và **Xem gần đây** (lưới thẻ kênh). Cả hai trống thì hiện "Tủ kênh trống" (`library-empty`).

### Trang play

Bấm kênh mở khung theatre giống HomeFlix: hàng đầu có nút quay lại, tên kênh, rồi khung video, rồi danh sách phát. Mở trang play thì ẩn nội dung tab, đóng thì hiện lại tab đang chọn (giống `exitPlayer` của HomeFlix). Bỏ lớp phủ toàn màn hình `.tv-player` cũ và các quy tắc CSS của nó.

- Danh sách phát: `renderEpisodeList` với `rows: 10`, `unit: 'kênh'`. Nguồn là danh sách kênh đang hiển thị theo bộ lọc hiện tại (cùng danh sách mà phím Lên/Xuống chuyển kênh). Mỗi dòng có logo nhỏ, tên và chấm trạng thái (qua `decorate`). Kênh đang phát được tô sáng và tự cuộn vào tầm nhìn. Bấm dòng thì chuyển kênh, vẫn gộp 400 ms.
- Logic phát giữ nguyên: stream dự phòng lần lượt, timeout 8 giây, thông báo "Đang thử nguồn x/y", hộp lỗi và "Thử lại", ghi lại kênh xem gần đây khi có khung hình đầu.
- Nút Esc và nút quay lại đóng trang play; focus quay về thẻ kênh vừa xem.

### Remote TV

Giữ cơ chế `HFSettingsUtils.gridMove`. Các hàng điều hướng gồm: hàng Xem gần đây, hàng nút lọc, hàng dropdown, rồi lưới (thanh tab do HomeFlix xử lý sẵn bằng `initTabbing`). Trong trang play, Lên/Xuống chuyển kênh, Esc đóng. Danh sách phát cuộn bằng Lên/Xuống khi focus nằm trong bảng.

## Kiểm thử (gọn, theo `lean-tests-single-user`)

- `tests/hometv/test_hometv_catalog.py`: `status` đúng cho `alive`, `dead`, `unchecked`, và `null` khi `use_health` tắt; một truy vấn.
- `tests/hometv/test_hometv_public_api.py`: `/channels` trả `{health, channels}`.
- `tests/js/hometv-utils.test.mjs`: lọc Alive, ghép với nhóm và từ khoá.
- `tests/ui/`: cập nhật `test_hometv_ui.py`, `test_hometv_player.py`, `test_hometv_style.py` theo bố cục mới, kèm các ca mới: ba tab đúng nhãn và thứ tự ở hai thanh; hàng Xem gần đây hiện đúng thẻ; lọc Alive ẩn kênh `dead`/`unchecked`; chấm trạng thái ẩn khi `health` false; bảng danh sách phát cao đúng 10 dòng và cuộn bên trong khi hơn 10 kênh; bấm dòng chuyển kênh; kênh đang phát được tô và nằm trong tầm nhìn.
- Hồi quy HomeFlix: chạy lại các test giao diện phim, "Xem tiếp", bộ lọc Thể loại/Quốc gia/Năm và danh sách tập hiện có; cả bộ phải xanh sau từng lần tách hàm.

## Ngoài phạm vi

- Gia cố bảo mật và các mục trong `docs/superpowers/backlog-hometv.md`.
- EPG (gói 5).
- Đổi logic phát hay bộ kiểm stream.
- Tính năng nào của phim chưa có ở HomeTV (tiến độ xem, tải xuống, cast) vẫn không có.

## Rủi ro cần xem khi lập plan

- Các quy tắc `body.player-active` ngoài phần định dạng khung có thể ẩn header hoặc đổi bố cục; kiểm từng quy tắc trước khi mở rộng bộ chọn.
- `switchTab` và `initTabbing` được nhiều đoạn gọi; móc ánh xạ tab phải không đổi gì khi `HFTV.active()` là false.
- `renderEpisodeList` dựng lại toàn bộ DOM mỗi lần gọi; với vài trăm kênh phải đo xem cuộn bảng có giật không, và nếu có thì chỉ vẽ lại lớp tô sáng thay vì cả bảng.
