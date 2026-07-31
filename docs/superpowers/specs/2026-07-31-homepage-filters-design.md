# Homepage Filters — Design Spec

> **Ngày:** 2026-07-31
> **Trạng thái:** Đã duyệt (đang chờ review spec cuối)
> **Đề xuất triển khai bởi:** Brainstorming session

## Mục tiêu

Bổ sung bộ lọc **Thể loại + Quốc gia + Năm phát hành** bên cạnh 2 tab danh sách hiện tại (Phim Chiếu Rạp, Phim Lẻ) trên trang chủ, cho phép kết hợp **nhiều tiêu chí cùng lúc** (AND) theo bộ lọc chung của API kkphim/phimapi.

## Quyết định đã chốt (từ brainstorming)

| Câu hỏi | Lựa chọn |
|---|---|
| Tiêu chí lọc | Thể loại + Quốc gia + Năm phát hành (không Sort, không Ngôn ngữ) |
| Số giá trị / tiêu chí | **1 giá trị** / tiêu chí, kết hợp AND giữa các tiêu chí |
| Kiểu tương tác | Dropdown từng nút cạnh tab |
| Tab cơ bản | Giữ nguyên 2 tab (Phim Chiếu Rạp, Phim Lẻ) |
| Hướng tiếp cận | **A — Backend proxy bộ lọc** (mở rộng endpoint backend, cache SQLite) |

## Nghiên cứu API kkphim (đã test trực tiếp)

- Endpoint lọc: `/v1/api/danh-sach/{type}` hỗ trợ `page`, `category`, `country`, `year`, `sort_field`, `sort_type`, `sort_lang`.
- **Kết hợp nhiều tiêu chí** hoạt động: `category=hanh-dong&country=han-quoc` → kết quả lọc cả 2.
- **Nhiều giá trị / tiêu chí** hoạt động (không dùng trong scope này): `category=a,b` hoặc `category[]=a&category[]=b` (OR trong nhóm).
- **Khoảng năm** hoạt động: `year=2014,2024` (không dùng — scope chọn 1 năm).
- **Danh sách lọc** (đã test OK):
  - `https://phimapi.com/v1/api/the-loai` → `{"status":"success","data":{"items":[{"_id","name","slug"}...]}}`
  - `https://phimapi.com/v1/api/quoc-gia` → cùng shape.
  - (Lưu ý: `/v1/the-loai` và `/v1/quoc-gia` — thiếu `/api` — trả `{"status":false,"msg":"hmmm!"}`, không dùng.)

## Kiến trúc

### Backend (app.py)

**Mở rộng 2 endpoint danh sách** — `/api/home/phim-le` và `/api/home/phim-chieu-rap`:

```
GET /api/home/phim-le?page=1&category=hanh-dong&country=han-quoc&year=2024
GET /api/home/phim-chieu-rap?page=1&category=...&country=...&year=...
```

- Tham số mới: `category: str = ""`, `country: str = ""`, `year: str = ""` (mặc định rỗng = không lọc).
- URL phimapi: gắn thêm tham số khi khác rỗng.
- Cache key: `home:phim-le:{page}:{category}:{country}:{year}` (TTL 3600s như cũ). Không lọc → key `home:phim-le:{page}:::` (cache cũ định dạng `home:phim-le:{page}` sẽ hết hạn tự nhiên theo TTL, không gây lỗi).
- Cache warmer (`home_cache_warmer`, `_warm_section`) **không đổi** — chỉ làm nóng bản không lọc.

**Thêm 2 endpoint danh sách lọc:**

```
GET /api/home/categories  → proxy https://phimapi.com/v1/api/the-loai
GET /api/home/countries   → proxy https://phimapi.com/v1/api/quoc-gia
```

- Trả `{"items": [{"name": "Hành Động", "slug": "hanh-dong"}, ...]}`.
- Cache key: `home:categories`, `home:countries`, TTL 86400 (24h).
- Dùng `cached_fetch` (kèm stale-fallback sẵn có).

### Frontend (templates/index.html)

**UI — thanh bộ lọc** trong `.grid-tabs-nav`, sau 2 nút tab:

```
[Phim Chiếu Rạp] [Phim Lẻ]   [Thể loại ▾] [Quốc gia ▾] [Năm ▾]   [✕ Xóa lọc]
```

- Mỗi nút dropdown: mở danh sách cuộn được, mục đầu "Tất cả" (xóa tiêu chí đó).
- Nút đang có lọc: màu accent + dấu chấm `•`.
- Nút **"✕ Xóa lọc"** chỉ hiện khi có ≥1 tiêu chí đang lọc; reset toàn bộ về mặc định.
- Đóng dropdown khi: click ngoài / nhấn ESC / chọn xong. Chỉ 1 dropdown mở tại 1 thời điểm. Có `tabindex` hỗ trợ TV D-pad.
- Dữ liệu Thể loại/Quốc gia: fetch `/api/home/categories`, `/api/home/countries` **1 lần**, cache trong JS variable.
- Dữ liệu Năm: sinh client-side, từ `currentYear` về `1990`.

**State** — mỗi tab giữ bộ lọc riêng:

```js
homeState = {
  latest: { items: [], page: 1, loading: false, hasMore: true },
  phimLe: {
    items: [], page: 1, loading: false, hasMore: true, fetchSeq: 0,
    filters: { category: '', country: '', year: '' }
  },
  phimChieuRap: { /* cùng shape như phimLe */ },
  activeGridTab: 'phim-chieu-rap'
}
```

**Luồng dữ liệu:**

- Chọn lọc → tăng `fetchSeq` (bỏ qua response cũ — chống race), reset `page=1`, xóa `items`, gọi `fetchHomeSection(tab, 1)` → `renderGridCards(false)`.
- `fetchHomeSection(section, page)` gộp `filters` vào URL.
- Infinite scroll tiếp tục với bộ lọc đang active.
- Chuyển tab → giữ nguyên bộ lọc riêng của từng tab (không reset nhau).

## Xử lý lỗi & trạng thái

- API phimapi lỗi → stale-fallback sẵn có.
- Tổ hợp lọc không có phim → `items: []` → UI hiện "Không tìm thấy phim phù hợp với bộ lọc."
- categories/countries lỗi → nút dropdown bị ẩn/disabled, không crash.
- Race condition khi đổi filter nhanh → `fetchSeq` (token generation).

## Kiểm thử (thủ công — repo không có test framework)

**Backend (curl):**
1. `curl "http://localhost:6969/api/home/phim-le?page=1&category=hanh-dong&country=han-quoc&year=2024"` → đúng items.
2. Gọi lại lần 2 → cache HIT (kiểm tra log `[Cache] HIT`).
3. Tổ hợp không có kết quả → `items: []`.
4. `curl "http://localhost:6969/api/home/categories"` → đầy đủ danh sách thể loại.
5. `curl "http://localhost:6969/api/home/countries"` → đầy đủ danh sách quốc gia.

**Frontend (trình duyệt):**
1. Mở trang, chọn lọc từng tiêu chí và kết hợp → kết quả đúng, URL gọi đúng.
2. Reset lọc (từng nút "Tất cả" + nút "✕ Xóa lọc") → trả về danh sách đầy đủ.
3. Chuyển tab → state lọc riêng biệt được giữ.
4. Infinite scroll với bộ lọc đang active.
5. Đóng dropdown khi click ngoài / ESC.
6. TV mode: điều hướng dropdown bằng keyboard.

## Phạm vi (ngoài scope)

- Không thêm tab cơ bản mới (Phim Bộ, Hoạt Hình, TV Shows).
- Không thêm Sort, không thêm Ngôn ngữ.
- Không multi-select trong 1 tiêu chí.
