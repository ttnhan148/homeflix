# Tự động hóa trình phát (Gói 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hết tập thì đếm ngược 10 giây rồi phát tập kế (có nút Hủy); nút "Bỏ qua mở đầu" nhảy tới mốc cố định trong 90 giây đầu của tập; cả hai cài theo thiết bị.

**Architecture:** Logic thuần (đọc/ghi cài đặt, có hiện nút bỏ intro không, vị trí nhảy) nằm trong `static/player-utils.js`, chạy được trong trình duyệt và Node nên test bằng `node --test`. `static/player-auto.js` nối DOM: nút bỏ intro, lớp đếm ngược, bảng cài đặt. Cài đặt lưu trong `localStorage` của từng thiết bị (mỗi thiết bị một trình duyệt, đúng yêu cầu "theo thiết bị", không cần API mới). Lệnh cast `options` từ iPhone ghi vào cùng cài đặt trên TV, thay cho `castOptions` trong bộ nhớ.

**Tech Stack:** JS thuần, `node --test`, pytest + Playwright (`tests/ui`, cầu nối `page.route` như Gói 2/3).

**Spec:** `docs/superpowers/specs/2026-10-29-device-sessions-ios26-ui-design.md` (mục "Gói 4 — Tự động hóa trình phát"; điều khiển `options` ở "Gói 3 — Cast").

**Base:** nhánh `feat/player-automation` từ `main` (Gói 1–3 đã merge). Dựa vào `video` (`#videoPlayer`), `play(url, title, idx)`, `playNextEpisode()`, `playbackEpisodes`, `currentPlaybackIndex`, `exitPlayer()`, `castOptions` (`templates/index.html:2617`), `video.onended` (`templates/index.html:4009`), lệnh cast `options` (`static/cast.js:480`), `tests/ui/conftest.py` (`open_app`, `tap`).

## Global Constraints

- Đếm ngược tự qua tập: **10 giây**, có nút **Hủy**.
- Nút "Bỏ qua mở đầu" nhảy tới mốc cố định, mặc định **85 giây**, đặt được trong cài đặt; chỉ hiện trong **90 giây đầu** của tập.
- `phimapi.com` không có mốc intro: không viết code "phát hiện intro".
- Cài đặt theo thiết bị; không đăng nhập, không API mới.
- Mọi chuỗi từ dữ liệu phim render bằng text (đã có quy ước ở Gói 2), UI tiếng Việt.
- Thêm file tĩnh mới vào `SHELL_URLS` và tăng `CACHE_NAME` trong `static/sw.js`.
- Hành vi hiện tại (tự qua tập ngay khi hết tập) được thay bằng đếm ngược; mặc định `autoNext = true` để không mất tính năng đang có.

## Review Focus

- Hết tập cuối (không có tập kế): không hiện đếm ngược, không lỗi.
- Phim lẻ 1 tập: không đếm ngược, không nút bỏ intro sai chỗ.
- `introSeconds` lớn hơn thời lượng tập (video ngắn): không nhảy vượt cuối; nút không hiện.
- Người dùng tua/thoát player/đổi tập tay khi đang đếm ngược: đếm ngược hủy, không tự phát sau đó.
- `localStorage` bị chặn hoặc chứa JSON hỏng: dùng mặc định, không lỗi.
- Cast `options` tới TV khi bảng cài đặt đang mở: công tắc cập nhật theo.

---

## File Structure

- Create `static/player-utils.js`: hàm thuần, export `window.HFPlayerUtils` và `module.exports`.
- Create `static/player-auto.js`: nối DOM (`window.HFPlayerAuto`).
- Create `tests/js/player-utils.test.mjs`, `tests/ui/test_player_auto.py`.
- Modify `templates/index.html`: nạp 2 script, thêm nút/lớp/bảng, thay `video.onended`, bỏ `castOptions`.
- Modify `static/cast.js:480`: lệnh `options` gọi `HFPlayerAuto.setPrefs`.
- Modify `static/ios26.css`: kiểu cho nút, lớp đếm ngược, bảng (dùng token kính có sẵn).
- Modify `static/sw.js`, `README.md` (một đoạn mô tả).

### Task 1: Logic thuần `player-utils.js`

**Files:**
- Create: `static/player-utils.js`
- Test: `tests/js/player-utils.test.mjs`

**Interfaces:**
- Produces (`HFPlayerUtils`):
  - `DEFAULT_PREFS = {autoNext: true, autoIntro: false, introSeconds: 85}`
  - `loadPrefs(storage): Prefs`: đọc khóa `hf_player_prefs`; JSON hỏng/thiếu/storage ném lỗi → mặc định; ép kiểu từng trường (`introSeconds` số nguyên 10–300, ngoài khoảng → mặc định).
  - `savePrefs(storage, prefs): Prefs`: gộp với mặc định, ghi, trả bản đã chuẩn hóa; lỗi storage bị nuốt.
  - `showSkipIntro(currentTime, duration, prefs): boolean`: `true` khi `currentTime < 90` và `introSeconds < duration - 5` (duration không hữu hạn → `false`) và `currentTime < introSeconds`.
  - `introTarget(duration, prefs): number`: `introSeconds`, kẹp tối đa `duration - 5`.
  - `hasNextEpisode(index, count): boolean`.
  - `countdownLabel(secondsLeft): string` → `"Tập kế sau 7 giây"`.

- [ ] **Step 1: Viết test lỗi** trong `tests/js/player-utils.test.mjs` (`node:test`, nạp bằng `createRequire`): `loadPrefs` với storage rỗng = `DEFAULT_PREFS`; JSON hỏng = mặc định; `introSeconds: 5` và `9999` = 85; storage ném lỗi = mặc định; `savePrefs` rồi `loadPrefs` khứ hồi. `showSkipIntro(10, 1400, p)` true; `(95, 1400, p)` false; `(10, 60, {introSeconds: 85})` false; `(10, NaN, p)` false; `(90, 1400, {introSeconds: 300})` false (quá 90 giây đầu). `introTarget(70, {introSeconds: 85})` = 65. `hasNextEpisode(0, 1)` false, `(0, 2)` true, `(1, 2)` false, `(-1, 5)` false. `countdownLabel(7)` = `"Tập kế sau 7 giây"`.
- [ ] **Step 2: Chạy `node --test tests/js/player-utils.test.mjs`**, kỳ vọng FAIL (module chưa có).
- [ ] **Step 3: Cài đặt** các hàm trên trong `static/player-utils.js` theo đúng chữ ký; cùng kiểu đóng gói UMD đơn giản như `static/cast-utils.js`.
- [ ] **Step 4: Chạy lại**, kỳ vọng PASS toàn bộ (cũng chạy `node --test tests/js` để chắc không hỏng test cũ).
- [ ] **Step 5: Commit** `feat(player): pure helpers for auto-next and skip-intro`.

### Task 2: Tự qua tập có đếm ngược và nút Hủy

**Files:**
- Create: `static/player-auto.js`
- Modify: `templates/index.html` (script tag; lớp `#autoNextOverlay` trong `.player-wrapper` gồm chữ + nút `#autoNextCancel` + nút "Phát ngay" `#autoNextNow`; thay khối `video.onended` ở dòng ~4009), `static/ios26.css`
- Test: `tests/ui/test_player_auto.py`

**Interfaces:**
- Consumes: `HFPlayerUtils.loadPrefs/hasNextEpisode/countdownLabel`; trong `index.html`: `playNextEpisode()`, `playbackEpisodes`, `currentPlaybackIndex`, `video`.
- Produces (`HFPlayerAuto`):
  - `init({video, getEpisodes: () => ({list, index}), playNext: () => void, storage})`
  - `getPrefs(): Prefs`, `setPrefs(partial): Prefs` (lưu và phát sự kiện `hf-prefs` trên `document`)
  - `onEnded()`: nếu `autoNext` và còn tập kế thì bắt đầu đếm 10 giây; hết giờ gọi `playNext`.
  - `cancelCountdown()`: dừng và ẩn lớp. Tự gọi khi `video` phát `seeking`/`play`, khi `play()` được gọi cho tập mới và khi `exitPlayer()`.

- [ ] **Step 1: Viết test UI lỗi** `test_countdown_then_next` (dùng bộ tập giả có ≥2 tập như `test_cast_receiver.py`; nhảy `video.currentTime` sát cuối và phát sự kiện `ended` bằng `page.evaluate("video.dispatchEvent(new Event('ended'))")`): lớp `#autoNextOverlay` hiện chữ "Tập kế sau 10 giây"; bật đồng hồ giả của Playwright (`page.clock`) tiến 10 giây thì `currentPlaybackIndex` tăng 1. Thêm `test_cancel_stops_next` (bấm `#autoNextCancel`, tiến 15 giây, index không đổi, lớp ẩn), `test_last_episode_no_countdown`, `test_single_movie_no_countdown`, `test_manual_seek_cancels` (phát `seeking` khi đang đếm), `test_autonext_off_no_countdown` (`localStorage` `hf_player_prefs` = `{"autoNext": false}`).
- [ ] **Step 2: Chạy `pytest tests/ui/test_player_auto.py -v`**, kỳ vọng FAIL.
- [ ] **Step 3: Cài đặt** `HFPlayerAuto` đếm bằng `setInterval` 1 giây; dùng `textContent` cho nhãn; trong `index.html` gọi `HFPlayerAuto.init(...)` sau khi khai báo `playbackEpisodes` và thay thân `video.onended` bằng `HFPlayerAuto.onEnded()`; gọi `cancelCountdown()` ở đầu `play()` và `exitPlayer()`. Kiểu lớp theo token kính của `ios26.css`, nút Hủy đủ lớn cho ngón cái (cùng cỡ nút Phát ở Gói 2) và nằm ngoài vùng an toàn.
- [ ] **Step 4: Chạy lại**, kỳ vọng PASS; chạy `pytest tests/ui/test_three_taps.py tests/ui/test_cast_receiver.py` để chắc không hỏng luồng phát cũ.
- [ ] **Step 5: Commit** `feat(player): auto-next countdown with cancel`.

### Task 3: Nút "Bỏ qua mở đầu"

**Files:**
- Modify: `static/player-auto.js`, `templates/index.html` (nút `#skipIntroBtn` trong `.player-wrapper`), `static/ios26.css`
- Test: `tests/ui/test_player_auto.py`

**Interfaces:**
- Consumes: `HFPlayerUtils.showSkipIntro/introTarget`.
- Produces: `HFPlayerAuto.skipIntro(): boolean` (đặt `video.currentTime = introTarget(...)`, trả `true` nếu đã nhảy). Nút cập nhật theo `timeupdate`/`loadedmetadata`; với `prefs.autoIntro` bật, tự gọi `skipIntro()` một lần cho mỗi tập khi nút đáng lẽ hiện.

- [ ] **Step 1: Viết test lỗi:** `test_skip_button_visible_first_90s` (đặt `currentTime=10`, duration 1400 → nút hiện; `currentTime=95` → ẩn); `test_skip_jumps_to_85` (bấm nút → `video.currentTime` ≈ 85); `test_skip_uses_custom_seconds` (`introSeconds: 60`, bấm nút → nhảy tới 60); `test_short_video_hides_button` (duration 60); `test_auto_intro_skips_once` (`autoIntro: true`: mở tập, `currentTime` tự thành 85, tua lùi về 5 thì không nhảy lần hai trong cùng tập; đổi tập thì nhảy lại).
- [ ] **Step 2: Chạy**, kỳ vọng FAIL.
- [ ] **Step 3: Cài đặt** theo Interfaces; cờ "đã tự nhảy" reset khi `play()` bắt đầu tập mới (thêm `HFPlayerAuto.onEpisodeStart()`, gọi từ `play()` cùng chỗ với `cancelCountdown()`; `onEpisodeStart` gọi `cancelCountdown`).
- [ ] **Step 4: Chạy lại**, kỳ vọng PASS.
- [ ] **Step 5: Commit** `feat(player): skip-intro button with fixed offset`.

### Task 4: Bảng cài đặt và nối lệnh cast `options`

**Files:**
- Modify: `templates/index.html` (nút ⚙ `#playerPrefsBtn` trong `#playerHeader`, bảng `#playerPrefs`: công tắc `#prefAutoNext`, `#prefAutoIntro`, ô số `#prefIntroSeconds` 10–300), `static/cast.js:480` và dòng `castOptions` ở `templates/index.html:2617` (xóa), `static/ios26.css`, `static/sw.js`, `README.md`
- Test: `tests/ui/test_player_auto.py`, `tests/ui/test_cast_receiver.py` (cập nhật nếu có test dùng `castOptions`)

**Interfaces:**
- Consumes: `HFPlayerAuto.getPrefs/setPrefs`, sự kiện `hf-prefs`.
- Produces: bảng cài đặt đồng bộ hai chiều với `HFPlayerAuto`; lệnh cast `options` gọi `HFPlayerAuto.setPrefs({autoNext?, autoIntro?})`; `static/sw.js` có `player-utils.js`, `player-auto.js` trong `SHELL_URLS` và `CACHE_NAME = 'homeflix-shell-v4'`.

- [ ] **Step 1: Viết test lỗi:** `test_prefs_panel_persists` (đổi công tắc và `introSeconds=60`, tải lại trang, giá trị còn); `test_invalid_intro_seconds_rejected` (nhập 5 hoặc chữ → giữ giá trị cũ); `test_cast_options_command_updates_prefs` (gửi lệnh `options {autoNext: false}` qua cầu SSE giống `test_cast_receiver.py` → `localStorage` đổi và công tắc cập nhật khi bảng đang mở); `test_prefs_survive_blocked_storage` (chặn `localStorage` bằng init script ném lỗi → trang vẫn mở, cài đặt chạy trong bộ nhớ).
- [ ] **Step 2: Chạy**, kỳ vọng FAIL.
- [ ] **Step 3: Cài đặt** bảng, nối `hf-prefs`, đổi `cast.js` sang `HFPlayerAuto.setPrefs`, xóa `castOptions`, cập nhật `sw.js` và README.
- [ ] **Step 4: Chạy toàn bộ bộ kiểm:** `pytest -q` và `node --test tests/js`, kỳ vọng tất cả PASS. Chụp ảnh Playwright ở 430×932 và 1920×1080 cho nút bỏ intro, đếm ngược, bảng cài đặt (theo kiểu `tests/ui/test_screenshots.py`) và xem kết quả bằng mắt.
- [ ] **Step 5: Commit** `feat(player): per-device prefs panel, cast options write prefs`.

## Self-Review

- Spec: tự qua tập 10 giây + Hủy + theo thiết bị → Task 1, 2, 4. Bỏ intro mốc cố định 85 giây, 90 giây đầu, đặt được → Task 1, 3, 4. Lệnh `options` của cast (Gói 3) → Task 4.
- Tên khớp giữa các task: `HFPlayerUtils.loadPrefs/savePrefs/showSkipIntro/introTarget/hasNextEpisode/countdownLabel`, `HFPlayerAuto.init/getPrefs/setPrefs/onEnded/cancelCountdown/skipIntro`.
- Kiểm thử thủ công trên iPhone thật (toàn màn hình, nút bỏ intro với ngón cái, đếm ngược) vẫn cần sau khi merge, như spec yêu cầu.
