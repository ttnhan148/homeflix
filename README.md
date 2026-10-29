# 🎬 HomeFlix — M3U8 Turbo Proxy Player

> Trình phát video HLS (M3U8) với proxy thông minh, cache phân đoạn theo phiên, tải trước nền (prefetch) và tích hợp dữ liệu phim từ `phimapi.com`.

HomeFlix là một ứng dụng web **một file backend (FastAPI)** + **frontend Vanilla JS** đóng gói trọn gói: proxy và rewrite mọi playlist HLS, cache segment `.ts` theo phiên xem (giới hạn 10GB / 6 giờ), tự động tải trước toàn bộ tập phim ở chế độ nền, chống tải trùng segment (single-flight), và chuyển đổi HLS → MP4 bằng `ffmpeg`.

---

## ✨ Tính năng nổi bật

| Tính năng | Mô tả |
|---|---|
| 🛰️ **Proxy & Rewrite M3U8/TS** | Mọi sub-playlist, key AES-128 và segment `.ts` đều được viết lại về proxy, che giấu URL gốc. |
| ⚡ **Pass-through Streaming** | Segment được truyền thẳng tới client ngay khi còn đang tải, không chờ tải xong. |
| 🔒 **Single-Flight Lock** | Nhiều client yêu cầu cùng một segment chỉ có **một** luồng tải từ nguồn, các luồng còn lại chờ kết quả chung. |
| 🧠 **Background Prefetching** | Sau khi manifest được phân tích, toàn bộ segment + key của tập phim được tải trước với độ song song `PREFETCH_CONCURRENCY = 4`. |
| 🧹 **Tự dọn cache** | Chạy ngầm hàng giờ: xoá session quá 6h, giữ tổng dung lượng ≤ 10GB. |
| 💾 **Tủ phim (SQLite)** | Lưu phim, đồng bộ danh sách tập, theo dõi tiến độ xem (chưa xem / đang xem / đã xem). |
| ⬇️ **Download HLS → MP4** | Hàng đợi download qua `ffmpeg` (bản sao `-c copy`, fallback encode lại audio AAC), tồn tại sau restart server. |
| 📱 **UI đa thiết bị** | Tối ưu riêng cho Smart TV/máy chiếu (10-foot UI, điều hướng bằng D-pad), mobile và desktop. |
| 📺 **AirPlay** | Phát hiện thiết bị AirPlay, nút phát nhanh, overlay điều khiển từ xa (play/pause, seek) với poster nền. |
| 🗃️ **Cache API ngoài** | Dữ liệu `phimapi.com` được cache trong SQLite kèm **stale-fallback** khi API chết. |

---

## 🧱 Kiến trúc tổng quan

```
┌──────────────────────┐
│  Frontend            │   templates/index.html (Vanilla JS, không build step)
│  - Vanilla JS/CSS    │   static/ (PWA: sw.js, manifest.json)
└──────────┬───────────┘
           │ HTTP (cùng origin)
┌──────────▼───────────┐
│  FastAPI (app.py)    │   Một file duy nhất: routes, proxy, cache, DB, worker
│  ┌─────────────────┐ │
│  │ /proxy/m3u8     │─┼──► rewrite playlist về /proxy/ts
│  │ /proxy/ts       │─┼──► single-flight + pass-through + cache disk
│  │ /api/*          │─┼──► SQLite (saved_movies, episode_downloads, api_cache)
│  │ download_worker │─┼──► ffmpeg HLS → MP4 → ./downloads/
│  │ cache warmer    │─┼──► làm nóng home cache mỗi 10 phút
│  └─────────────────┘ │
└──────────┬───────────┘
           │ HTTP (httpx, UA Chrome, timeout 10s)
┌──────────▼───────────┐
│  phimapi.com         │   Nguồn dữ liệu phim (search / detail / home)
│  CDN .ts segments    │
└──────────────────────┘
```

- **Session cache**: `./cache/{session_id}/` — mỗi phiên là một thư mục, segment lưu dạng `{md5(url)}.ts`.
- **Không phụ thuộc framework frontend**: không bundler, không build step, không npm.

---

## 📁 Cấu trúc thư mục

```
homeflix/
├── app.py                  # Toàn bộ backend FastAPI (~1.200 dòng)
├── templates/index.html    # Toàn bộ frontend (HTML + CSS + JS, ~3.200 dòng)
├── static/
│   ├── sw.js               # Service worker (PWA offline shell)
│   ├── manifest.json       # PWA manifest
│   └── logo.png            # App icon / apple-touch-icon
├── requirements.txt        # 6 gói Python
├── install.sh              # Cài đặt Linux + systemd (đường dẫn /var/www/homeflix)
├── update.sh               # Cập nhật in-place trên server
├── migrate_json_to_sqlite.py  # Di trú saved_movies.json → SQLite (chạy 1 lần)
├── cache/                  # (tự tạo) segment .ts theo phiên — 10GB / 6h
├── downloads/              # (tự tạo) MP4 đã chuyển đổi
└── homeflix.db             # (tự tạo) SQLite WAL
```

---

## 🚀 Chạy local (Development)

### Yêu cầu
- Python 3.10+
- `ffmpeg` trên PATH (chỉ bắt buộc cho tính năng download HLS → MP4)

### Cài đặt & chạy

```bash
git clone https://github.com/ttnhan148/homeflix.git
cd homeflix
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# ffmpeg (macOS)
brew install ffmpeg
# ffmpeg (Debian/Ubuntu)
sudo apt-get install -y ffmpeg

uvicorn app:app --reload --host 0.0.0.0 --port 6969
```

Mở **http://localhost:6969**.

> Server tự tạo `./cache/` và `./downloads/` khi khởi động; `--reload` chỉ nên dùng ở môi trường phát triển.

---

## 🖥️ Triển khai Linux (Production)

### Cài đặt mới

```bash
git clone https://github.com/ttnhan148/homeflix.git ~/homeflix
cd ~/homeflix
sudo chmod +x install.sh
sudo ./install.sh
```

Script `install.sh` sẽ:
1. Cài Python, `venv`, `ffmpeg`, `curl` (hỗ trợ `apt/dnf/yum/pacman`).
2. Copy mã nguồn vào `/var/www/homeflix`.
3. Tạo virtualenv và cài `requirements.txt`.
4. Tạo service systemd `homeflix.service` (auto-restart 3s).
5. Mở cổng `6969` (ufw / firewalld).

### Cập nhật bản mới

```bash
cd ~/homeflix
git reset --hard
git pull
sudo chmod +x update.sh
sudo ./update.sh
```

### Quản lý dịch vụ

```bash
sudo systemctl status homeflix        # trạng thái
sudo systemctl restart homeflix       # khởi động lại
sudo journalctl -u homeflix -f        # log real-time
```

---

## 📡 API Reference

Tất cả endpoint trả JSON (trừ proxy & trang chủ). Không có xác thực — **chỉ nên chạy trong mạng nội bộ/riêng tư** (xem phần ⚠️ Bảo mật).

### Trang & tĩnh
| Method | Path | Mô tả |
|---|---|---|
| GET | `/` | Trang chủ / player (Jinja2 render `index.html`) |
| GET | `/static/*` | File tĩnh (PWA) |

### Proxy HLS
| Method | Path | Mô tả |
|---|---|---|
| GET | `/proxy/m3u8?url=...&sid=...` | Tải + rewrite playlist. `sid` mặc định = `md5(url)`. Trả `application/vnd.apple.mpegurl`. |
| GET | `/proxy/ts?url=...&sid=...` | Proxy + cache segment. Header `X-Cache`: `HIT` / `HIT-QUEUED` / `MISS`. |

### Dữ liệu phim
| Method | Path | Cache TTL | Mô tả |
|---|---|---|---|
| GET | `/api/search?q=...` | 30 phút | Tìm phim (limit 30). |
| GET | `/api/movie/{slug}` | 60 phút | Chi tiết phim + danh sách tập `[{name, link_m3u8}]`. |
| GET | `/api/home/latest?page=N` | 30 phút | Phim mới cập nhật. |
| GET | `/api/home/phim-le?page=N&category=&country=&year=` | 60 phút | Danh sách phim lẻ, lọc theo thể loại/quốc gia/năm (AND). |
| GET | `/api/home/phim-chieu-rap?page=N&category=&country=&year=` | 60 phút | Danh sách phim chiếu rạp, lọc theo thể loại/quốc gia/năm (AND). |
| GET | `/api/home/categories` | 24 giờ | Danh sách thể loại cho bộ lọc. |
| GET | `/api/home/countries` | 24 giờ | Danh sách quốc gia cho bộ lọc. |

### Tủ phim (saved)
| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/saved` | Danh sách phim đã lưu (mới nhất trước). |
| POST | `/api/saved` | Lưu / upsert phim. Body: `{slug, name, poster_url, year, episode_current, total_episodes, episodes}`. |
| POST | `/api/saved/progress` | Ghi tiến độ xem; đánh dấu tập cũ `watched`, hẹn xoá MP4 sau 3 giờ. |
| DELETE | `/api/saved/{slug}` | Xoá phim + cascade: download records, cache session, file MP4. |

### Download HLS → MP4
| Method | Path | Mô tả |
|---|---|---|
| POST | `/api/download` | Thêm tập vào hàng đợi. Body: `{movie_slug, episode_name, episode_url}`. |
| GET | `/api/download/status` | Trạng thái tất cả download. |
| GET | `/api/download/status/{movie_slug}` | Trạng thái theo phim. |
| GET | `/api/download/status/{movie_slug}/{episode_name}` | Trạng thái 1 tập. |
| GET | `/media/{movie_slug}/{episode_name}.mp4` | Phát file MP4 đã tải. |

Trạng thái download: `not_started` → `pending` → `downloading` → `completed` / `failed`.

### Cache
| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/cache/status` | `{size_gb, percent, max_gb}` của session cache. |
| POST | `/api/cache/clear` | Xoá toàn bộ session cache. |

---

## 🗄️ Cơ sở dữ liệu

SQLite tại `homeflix.db`, chế độ **WAL**, timeout kết nối 30s. Ba bảng:

```sql
-- Phim đã lưu (PRIMARY KEY: slug)
saved_movies (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    poster_url TEXT,
    year TEXT,
    episode_current TEXT,
    total_episodes INTEGER,
    last_watched_episode TEXT,
    last_watched_url TEXT,
    episodes TEXT,        -- JSON: [{name, link_m3u8}]
    episode_states TEXT,  -- JSON: {m3u8_url: "watching"|"watched"}
    updated_at TEXT NOT NULL
)

-- Trạng thái download tập phim (PRIMARY KEY: movie_slug + episode_name)
episode_downloads (
    movie_slug TEXT NOT NULL,
    episode_name TEXT NOT NULL,
    status TEXT NOT NULL,   -- not_started|pending|downloading|completed|failed
    error TEXT,
    delete_at REAL,         -- epoch: lịch xoá MP4 của tập đã xem
    PRIMARY KEY (movie_slug, episode_name)
)

-- Cache API ngoài (PRIMARY KEY: cache_key)
api_cache (
    cache_key TEXT PRIMARY KEY,   -- "search:{q}", "movie:{slug}", "home:latest:{page}", ...
    response_json TEXT NOT NULL,
    cached_at REAL NOT NULL,
    ttl_seconds INTEGER NOT NULL
)
```

---

## 🧠 Chi tiết kỹ thuật proxy & cache

### Luồng xử lý `/proxy/m3u8`
1. Tải origin playlist với User-Agent Chrome + `follow_redirects=True`.
2. Parse bằng thư viện `m3u8`.
3. **Master playlist** → rewrite `playlists[].uri`, `iframe_playlists[].uri`, `media[].uri` về `/proxy/m3u8`.
4. **Media playlist** → rewrite `segment.uri` và `key.uri` về `/proxy/ts`, sau đó kích hoạt `prefetch_episode()` ở nền.
5. Trả playlist đã rewrite với `Cache-Control: no-cache` (tránh trình duyệt/player cache stale).

### Luồng xử lý `/proxy/ts` (single-flight)
1. **Disk hit** → trả `FileResponse` với `X-Cache: HIT`. Segment 16 byte được xác định là AES-128 key.
2. **Đang có luồng khác tải** → `await download_locks[lock_id].wait()` (single-flight), sau khi xong trả `X-Cache: HIT-QUEUED`.
3. **Miss** → vừa ghi `.part` vừa buffer, hoàn tất thì đổi tên `.ts`, trả `X-Cache: MISS`.

Bản đồ khoá `download_locks` được **dùng chung** giữa `/proxy/ts` và prefetch worker nên không bao giờ tải trùng segment.

### Prefetch
`prefetch_episode(segment_urls, key_url, sid)` tải key trước rồi song song các segment qua semaphore `PREFETCH_CONCURRENCY = 4`.

### Cache warmer
`home_cache_warmer()` làm nóng page 1 của 3 section home mỗi `WARM_CACHE_INTERVAL = 600s`, nguồn người xem không bao giờ phải chờ API ngoài lạnh.

### Dọn dẹp định kỳ (`prune_cache`, chạy mỗi giờ)
- Xoá cả thư mục session có `mtime` > 6 giờ.
- Xoá file `.part` mồ côi > 1 giờ.
- Xoá file cũ nhất đến khi tổng dung lượng ≤ 10GB.
- Xoá file lạc (non-directory) ở gốc `cache/`.

---

## ⬇️ Luồng download HLS → MP4

1. `POST /api/download` đẩy tập vào `asyncio.Queue` (bỏ qua nếu `completed`/`downloading`/đã trong hàng đợi).
2. `download_worker()` lấy việc, tạo `downloads/{movie_slug}/`, ghi ra file `.part`.
3. Thử lần 1: `ffmpeg -c copy -f mp4` (copy không mất thời gian). Lỗi → thử lần 2: `-c:v copy -c:a aac`.
4. Mỗi lần thử timeout 600s; thành công đổi tên `.part` → `.mp4`.
5. Worker re-queue lại record `pending`/`downloading` còn sót từ DB khi khởi động — **download không mất sau khi server restart**.
6. Khi tập đã xem, file MP4 bị xoá tự động sau 3 giờ (`delete_at`, kiểm tra mỗi 60s).

Tên file được làm sạch bằng `clean_filename()` (bỏ dấu tiếng Việt, thay ký tự không hợp lệ bằng `-`, fallback `md5`).

---

## ⚙️ Hằng số cấu hình

| Hằng số | Giá trị | Vị trí |
|---|---|---|
| `MAX_CACHE_SIZE` | 10 GB | app.py |
| `MAX_CACHE_AGE` | 6 giờ | app.py |
| `PREFETCH_CONCURRENCY` | 4 | app.py |
| `WARM_CACHE_INTERVAL` | 600 giây | app.py |
| Search TTL | 1800s | `cached_fetch` |
| Movie detail TTL | 3600s | `cached_fetch` |
| Home latest TTL | 1800s | `cached_fetch` |
| Phim Lẻ / Chiếu Rạp TTL | 3600s | `cached_fetch` |
| ffmpeg timeout | 600s / lần thử | `download_worker` |
| HTTP client timeout | 10s | `httpx.AsyncClient` |
| Xoá MP4 sau khi xem | 3 giờ | `/api/saved/progress` |
| User-Agent origin | Chrome 120 UA | mọi request origin + ffmpeg |

---

## 🧩 Frontend & thiết bị

- **Không framework, không build step** — mọi thứ nằm trong `templates/index.html`.
- **Phát hiện thiết bị** qua UA: `device-tv` (10-foot UI, buffer HLS nhỏ hơn, tắt low-latency), `device-mobile` (UI touch), desktop.
- **Native HLS**: Safari/iOS phát thẳng `application/vnd.apple.mpegurl`; các trình duyệt khác dùng **hls.js** (CDN jsDelivr).
- **AirPlay**: lắng nghe `webkitplaybacktargetavailabilitychanged` + `webkitcurrentplaybacktargetiswirelesschanged`; nút phát nhanh trong player header; overlay điều khiển từ xa khi đang AirPlay; giữ video element `opacity:0` thay vì `display:none` để ổn định kết nối.
- **PWA**: `static/sw.js` + `static/manifest.json` — cài đặt như app, chạy standalone.

---

## ⚠️ Bảo mật & lưu ý

- **Không có xác thực**: toàn bộ API (kể cả xoá cache, tải phim) đều mở. Nếu deploy ra internet công khai, hãy đặt sau reverse proxy có auth (vd. `basic-auth` của Caddy/nginx) hoặc mở qua VPN.
- **Không cần đăng ký API key**: dữ liệu phim lấy từ `phimapi.com` — một dịch vụ bên thứ ba không có SLA. HomeFlix cache + stale-fallback để giảm tác động khi nguồn chết.
- **Giới hạn ngang hàng**: `httpx` timeout 10s; nếu mạng chậm với origin nên cân nhắc tăng.
- **ffmpeg** bắt buộc phải có trên PATH khi dùng tính năng download; nếu thiếu, worker đánh dấu `failed` với lỗi "ffmpeg not installed".
- **Phạm vi nội dung**: dự án hướng đến mục đích giáo dục/cá nhân. Hãy tuân thủ luật bản quyền nơi bạn sinh sống và chỉ dùng nội dung hợp pháp.

---

## 🛠️ Troubleshooting

| Vấn đề | Hướng xử lý |
|---|---|
| `LỖI: TRÌNH DUYỆT KHÔNG HỖ TRỢ HLS` | Trình duyệt không có hls.js (CDN bị chặn) hoặc không hỗ trợ native HLS. Kiểm tra kết nối tới `cdn.jsdelivr.net`. |
| `[HLS Error]: Network Error` | `/proxy/m3u8` không tải được origin. Xem log server, kiểm tra phim tồn tại. |
| Download báo `failed` | Kiểm tra `ffmpeg -version`. Một số nguồn chặn UA Chrome 120 — sửa hằng số UA trong app.py. |
| Cache không giảm | `prune_cache` chạy mỗi giờ; hoặc gọi `POST /api/cache/clear` thủ công. |
| Phim không tải từ phimapi | Dữ liệu được cache stale; xoá dòng `api_cache` tương ứng trong `homeflix.db` để thử lại. |

---

## 🤝 Đóng góp

1. Fork repo, tạo nhánh tính năng.
2. Giữ **UI text, comment, docs bằng tiếng Việt** (quy ước dự án).
3. Backend là một file `app.py` — tuân thủ cấu trúc hiện có, không tách module.
4. Frontend là một file `index.html` — không đưa build step, bundler hay framework vào.
5. Thử nghiệm thủ công: không có test suite, không linter, không CI (xem AGENTS.md).
6. Mở Pull Request mô tả rõ thay đổi.

---

## 📄 Giấy phép

Repository chưa khai báo tệp `LICENSE`. Liên hệ tác giả nếu bạn muốn sử dụng lại mã nguồn trong sản phẩm thương mại.

---

> ⚠️ **Lưu ý pháp lý**: Dự án chỉ nên dùng với nội dung hợp pháp mà bạn sở hữu quyền hoặc được phép xem. Tác giả không chịu trách nhiệm về việc sử dụng trái phép.
