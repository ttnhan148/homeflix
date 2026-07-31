# Thiết kế: Danh sách Tập Phim Bộ Đồng Nhất (Unified Episode List)

Ngày: 2026-07-31 · Trạng thái: đã duyệt (brainstorm)

## Vấn đề

1. **Hai render path tách biệt, không đồng nhất**:
   - Player drawer: `.ep-btn` màu xám trung tính, chỉ có icon tải, **không hiển thị trạng thái xem**.
   - Trang "Phim Đã Lưu": `.saved-ep-btn` tô màu cả button (xanh lá = đã xem, vàng pulse = đang xem), + icon tải.
   - Cùng một dữ liệu `[{name, link_m3u8}]` nhưng markup + CSS hoàn toàn khác nhau.
2. **Trang chi tiết không có danh sách tập** — người dùng phải vào player mới thấy tập nào có.
3. **Watch state** chỉ tồn tại server-side cho phim đã lưu (`episode_states` keyed by `link_m3u8`, cập nhật qua `POST /api/saved/progress`). Phim chưa lưu → không có watch state.
4. **Download state** qua `downloadStatuses = {slug: {ep_name: status}}`, poll 5s, hiển thị bằng `.ep-dl-indicator` (data-attr).
5. **Không xử lý scale**: phim 1000+ tập render toàn bộ button vào hộp scroll (`max-height` 300px drawer / 130px saved) → DOM phình, khó dùng.

## Giải pháp

### Một component dùng chung 3 nơi

Hàm render duy nhất trong `templates/index.html`:

```js
renderEpisodeList(containerEl, {
  episodes,          // [{name, link_m3u8}]
  movieSlug,         // string | null (null → không hiển thị watch state / không cho tải)
  watchStates,       // { [link_m3u8]: 'watching' | 'watched' } — rỗng {} nếu phim chưa lưu
  onPlay,            // (ep, index) => void — hành vi khi bấm tập
  autoFocusWatching  // bool — mở chunk chứa tập 'watching' nếu có (mặc định true)
})
```

3 call site:
- **Trang chi tiết** (mới): section `.ep-list` hiển thị ngay dưới `.detail-overview`, trước `.detail-actions`, khi `showMovieDetail` render và `episodes.length > 0`. Bấm tập → `playEpisodesListDirect(currentDetailEpisodes, url)`.
- **Player drawer** (thay `buildEpisodesGridInDrawer`): render vào `#drawerEpGrid`, giữ nguyên toggle/collapse `.episodes-drawer`.
- **Trang đã lưu** (thay vòng lặp episode trong `renderSavedMovies`): render vào `.episodes-container` của từng row.

**Xóa** 2 class cũ `.ep-btn` và `.saved-ep-btn` (thay bằng `.ep-item` chung). Giữ cơ chế `.ep-dl-indicator` + `updateDownloadIndicatorsUI()` (data-attr `data-movie-slug`/`data-ep-name`) — không đổi backend, không đổi polling.

### Trạng thái hiển thị (đơn giản)

Button `.ep-item`: nền trung tính glass, viền `--glass-border`. Hai indicator nhỏ:

- **Trái** (`watch-state`): 
  - `.is-watched` → icon ✓ xanh lá (`#2cd054`), title "Đã xem"
  - `.is-watching` → icon ▶ màu accent đỏ + viền accent `rgba(229,9,20,.4)`, title "Đang xem — tiếp tục"
  - không có state → không hiển thị (chưa xem)
- **Phải** (`.ep-dl-indicator`, giữ nguyên 5 trạng thái cũ): `not_started` (↘ tải), `pending` (⏰ vàng), `downloading` (spinner vàng), `completed` (✓ xanh "Đã tải"), `failed` (✕ đỏ).

Mỗi button hiển thị tên tập (`ep.name`, đã là "Tập N") làm nhãn chính — không thêm chip số riêng (tránh trùng lặp "1" + "Tập 1"); số tập chỉ parse từ tên (`parseInt(name.match(/\d+/))`) cho mục đích jump + pager. Nhãn `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`. Bấm vào indicator tải không kích hoạt play (`event.stopPropagation()`).

### Scale >100/1000 tập — chunked + jump

- **Chunk cố định 100 tập/trang** (`const EP_CHUNK = 100`). DOM tối đa ~100 button.
- **Pager** (đầu danh sách, sau header):
  - `‹` / `›` điều hướng chunk
  - Label giữa: `Tập 1–100 / 1100`
  - Ô nhập số: "Nhảy tới tập" — Enter → tìm chunk chứa tập số đó → render. Số > tổng → clamp về chunk cuối.
- **Chunk mặc định**: nếu `autoFocusWatching` và có tập `watching` → chunk chứa tập đó; không có → chunk 1 (đầu).
- **Tập không parse được số** (Trailer, Full, tập đặc biệt) → xếp chunk 1, không nhảy tới được.
- **Header**: `Danh sách tập (N)` + thông tin tóm tắt nếu có dữ liệu: `· Đã xem X · Đã tải Y`.
- **Chunk state** lưu trên element: `containerEl._epChunk = n` — mỗi danh sách (mỗi phim) độc lập.

### Trang chi tiết — layout mới

Section `.ep-list` được render trong `showMovieDetail()` (chỉ khi `episodesList.length > 0`), chèn sau `.detail-overview` trước `.detail-actions`:
- Header + pager + grid (cùng component).
- `watchStates` lấy từ `savedMoviesList` nếu phim đã lưu (tìm theo slug), else `{}`.
- Bấm tập: `currentMovie = {slug, name, poster_url}`; `playEpisodesListDirect(currentDetailEpisodes, ep.link_m3u8)`.

### CSS (theo design-taste-frontend-v1, adapt vanilla CSS dark-glass)

- Màu: giữ **1 accent đỏ** hiện có (`--accent-color`, `#e50914`); xanh lá `#2cd054` = đã xem/đã tải; vàng `--accent-gold` = đang tải/đợi. Không thêm accent, không neon glow.
- `.ep-item`: `display:inline-flex`, bo góc `--radius-md`, viền `1px var(--glass-border)`, nền `rgba(18,18,20,.7)`, padding vừa, font 0.8–0.85rem.
  - hover: `transform: translateY(-1px)`, viền sáng hơn.
  - active (bấm): `transform: scale(.97)`.
  - `.is-watching`: viền `rgba(229,9,20,.4)` + pulse mờ giữ nguyên nhịp.
- Grid: `display:grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr)); gap:.5rem;` `max-height` + `overflow-y:auto` cho vùng list (giữ cuộn nội bộ, không phình trang).
- Giữ nguyên variant: `.device-tv .ep-item` (font 1.05rem, focus ring 4px, scale 1.05), `.device-mobile` (2 cột, font 0.9rem), tắt backdrop-filter trên mobile.
- Cập nhật selector focus TV: thêm `.ep-item` (thay `.ep-btn`).

### Hiệu năng & edge cases

- Chunking giới hạn DOM ≤ 100 items/render.
- Phim chưa lưu (`movieSlug` null) → bỏ `.ep-dl-indicator` và watch-state, chỉ số + tên.
- `episodes` rỗng → không render section, không lỗi.
- Tên tập trùng → vẫn hoạt động (keyed by index trong chunk + link_m3u8 cho watch/download).
- `updateDownloadIndicatorsUI()` giữ nguyên — poll 5s cập nhật indicator theo data-attr.
- Không sửa: backend `app.py`, `.ep-dl-indicator` CSS cơ bản, cơ chế `/api/saved/progress`, drawer toggle.

### Kiểm thử (CDP headless Chrome + curl)

1. Trang chi tiết phim bộ hiện section tập; bấm tập → player mở đúng tập.
2. Drawer player dùng cùng markup `.ep-item`; trạng thái watch hiển thị sau khi phát (saved movie).
3. Trang đã lưu: cùng markup, watch + download state hiện đúng.
4. Chunk: inject mảng 1100 tập giả → pager ‹/›, label `Tập 1–100/1100`, jump tới tập 550 → chunk chứa 550. Phim thật >100 tập nếu có.
5. Download: bấm indicator → pending → downloading → completed (hoặc failed), poll 5s cập nhật.
6. Regression: phát tiếp tự động (auto-advance), Xem tiếp (resume), lưu/xóa phim, TV focus, mobile 2 cột.

## Phạm vi (ngoài scope)

- Không thêm watch state cho phim chưa lưu (không localStorage, không backend mới).
- Không "tải tất cả tập".
- Không đổi cấu trúc API `/api/movie/{slug}` (giữ `episodes: [{name, link_m3u8}]`).
- Không dọn `.episodes-section` CSS chết (nếu tiện, dọn trong cùng commit không bắt buộc).
