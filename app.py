import m3u8
import urllib.parse
from urllib.parse import urljoin, quote
import hashlib
import os
import aiofiles
import shutil
import time
import asyncio
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.responses import StreamingResponse, PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import httpx
import logging
import json
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Quản lý các tiến trình đang tải (Single Flight) để chống tải trùng
download_locks = {} 

# Đảm bảo đường dẫn tuyệt đối để chạy ổn định trên Linux/Systemd
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
MAX_CACHE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
MAX_CACHE_AGE = 6 * 60 * 60              # 6 giờ (giây)

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR, exist_ok=True)

def _cleanup_disk():
    now = time.time()
    total_size = 0
    cleaned_sessions = 0
    all_files = []

    for item in os.listdir(CACHE_DIR):
        item_path = os.path.join(CACHE_DIR, item)
        if os.path.isdir(item_path):
            stats = os.stat(item_path)
            if now - stats.st_mtime > MAX_CACHE_AGE:
                try:
                    shutil.rmtree(item_path)
                    cleaned_sessions += 1
                except Exception:
                    pass
            else:
                for root, _, files in os.walk(item_path):
                    for f in files:
                        fpath = os.path.join(root, f)
                        fstats = os.stat(fpath)
                        total_size += fstats.st_size
                        if not f.endswith(".part"):
                            all_files.append((fpath, fstats.st_mtime, fstats.st_size))
                        else:
                            if now - fstats.st_mtime > 3600:
                                try: os.remove(fpath)
                                except: pass
        else:
            try: os.remove(item_path)
            except: pass

    if cleaned_sessions > 0:
        logger.info(f"Đã xóa {cleaned_sessions} phiên làm việc (session) hết hạn.")

    if total_size > MAX_CACHE_SIZE:
        all_files.sort(key=lambda x: x[1])
        removed_size = 0
        for file_path, _, size in all_files:
            if total_size - removed_size <= MAX_CACHE_SIZE:
                break
            try:
                os.remove(file_path)
                removed_size += size
                logger.info(f"Đã xóa file lẻ cũ để giảm dung lượng: {file_path}")
            except Exception:
                pass

async def prune_cache():
    """Tự động dọn dẹp cache không block event loop"""
    while True:
        try:
            await asyncio.to_thread(_cleanup_disk)
        except Exception as e:
            logger.error(f"Lỗi dọn dẹp cache: {e}")
            
        await asyncio.sleep(3600)  # Chạy mỗi giờ một lần

app = FastAPI(title="HomeFlix Proxy Player")

# Phục vụ các file tĩnh (manifest, icons)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

@app.on_event("startup")
async def startup_event():
    # Khởi động tác vụ dọn dẹp cache ngầm
    asyncio.create_task(prune_cache())


# Khởi tạo Jinja2 templates với đường dẫn tuyệt đối
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Cho phép CORS để Player JS có thể truy cập streams từ bất kì đâu
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP client dùng để proxy
client = httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True)

def make_proxy_url(request: Request, path: str, target_url: str, sid: str = None) -> str:
    """Tạo URL đi qua proxy của chúng ta"""
    proxy_base = str(request.base_url).rstrip("/")
    encoded_target = urllib.parse.quote(target_url, safe="")
    res = f"{proxy_base}{path}?url={encoded_target}"
    if sid:
        res += f"&sid={sid}"
    return res

@app.get("/")
async def root(request: Request):
    """Trang chủ - giao diện Player"""
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/proxy/m3u8")
async def proxy_m3u8(request: Request, url: str, sid: str = None):
    """Proxy phân tích m3u8 và viết lại các URL bên trong"""
    if not sid:
        sid = hashlib.md5(url.encode()).hexdigest()
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        response = await client.get(url, headers=headers, follow_redirects=True)
        response.raise_for_status()
        
        # Phân tích nội dung M3U8 với thư viện m3u8
        playlist = m3u8.loads(response.text, uri=url)
        
        # Phân nhánh 1: Nếu là luồng MASTER
        if playlist.is_variant:
            for item in playlist.playlists:
                abs_uri = item.absolute_uri
                item.uri = make_proxy_url(request, "/proxy/m3u8", abs_uri, sid)
            if playlist.iframe_playlists:
                for iframe in playlist.iframe_playlists:
                    abs_uri = iframe.absolute_uri
                    iframe.uri = make_proxy_url(request, "/proxy/m3u8", abs_uri, sid)
            if playlist.media:
                for media in playlist.media:
                    if media.uri:
                        abs_uri = media.absolute_uri
                        media.uri = make_proxy_url(request, "/proxy/m3u8", abs_uri, sid)
                        
        # Phân nhánh 2: Nếu là luồng MEDIA
        else:
            for segment in playlist.segments:
                abs_uri = segment.absolute_uri
                segment.uri = make_proxy_url(request, "/proxy/ts", abs_uri, sid)
                
            for key in playlist.keys:
                if key and key.uri:
                    abs_uri = key.absolute_uri
                    key.uri = make_proxy_url(request, "/proxy/ts", abs_uri, sid)
                    
        return PlainTextResponse(
            playlist.dumps(), 
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache"}
        )
        
    except Exception as e:
        logger.error(f"Error fetching proxy m3u8 '{url}': {e}")
        return PlainTextResponse(f"Proxy Error: {str(e)}", status_code=500)

@app.get("/proxy/ts")
async def proxy_ts(request: Request, url: str, sid: str = "default"):
    """Proxy và cache .ts với cơ chế Pass-through Stream và Part-files theo Session"""
    session_dir = os.path.join(CACHE_DIR, sid)
    if not os.path.exists(session_dir):
        os.makedirs(session_dir, exist_ok=True)
        
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cache_path = os.path.join(session_dir, f"{url_hash}.ts")
    part_path = os.path.join(session_dir, f"{url_hash}.ts.part")
    lock_id = f"{sid}_{url_hash}"
    
    # 1. Kiểm tra cache trên đĩa
    if os.path.exists(cache_path):
        return FileResponse(cache_path, media_type="video/MP2T", headers={"X-Cache": "HIT"})
    
    # 2. Cơ chế Single Flight: Kiểm tra xem có ai đang tải đoạn này chưa
    if lock_id in download_locks:
        await download_locks[lock_id].wait()
        if os.path.exists(cache_path):
            return FileResponse(cache_path, media_type="video/MP2T", headers={"X-Cache": "HIT-QUEUED"})

    # 3. Tạo Lock và mở Stream tải xuống
    event = asyncio.Event()
    download_locks[lock_id] = event
    
    async def stream_and_cache():
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    event.set()
                    yield b""
                    return

                async with aiofiles.open(part_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        await f.write(chunk)
                        yield chunk
                
                # Ghi xong hoàn chỉnh, đổi tên file part thành ts
                if os.path.exists(part_path):
                    os.rename(part_path, cache_path)
                event.set()

        except asyncio.CancelledError:
            # Khách nhấn tua phim hoặc tắt trình duyệt, hủy luồng ngay lập tức
            event.set()
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except Exception:
                    pass
            raise

        except Exception as e:
            logger.error(f"Lỗi tải segment: {e}")
            event.set()
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except Exception:
                    pass
            yield b""
            
        finally:
            if lock_id in download_locks:
                del download_locks[lock_id]

    return StreamingResponse(
        stream_and_cache(),
        media_type="video/MP2T",
        headers={"X-Cache": "MISS"}
    )

def _calculate_cache_size():
    total_size = 0
    for root, _, files in os.walk(CACHE_DIR):
        for f in files:
            file_path = os.path.join(root, f)
            if os.path.isfile(file_path):
                total_size += os.path.getsize(file_path)
    return total_size

@app.get("/api/cache/status")
async def get_cache_status():
    """Lấy thông tin dung lượng cache hiện tại (chống nghẽn Event Loop)"""
    try:
        total_size = await asyncio.to_thread(_calculate_cache_size)
        percent = (total_size / MAX_CACHE_SIZE) * 100
        total_gb = total_size / (1024 * 1024 * 1024)
        
        return {
            "size_gb": round(total_gb, 2),
            "percent": round(percent, 1),
            "max_gb": round(MAX_CACHE_SIZE / (1024**3), 1)
        }
    except Exception as e:
        return {"error": str(e)}

def _clear_all_cache():
    for item in os.listdir(CACHE_DIR):
        item_path = os.path.join(CACHE_DIR, item)
        if os.path.isdir(item_path):
            try: shutil.rmtree(item_path)
            except: pass
        else:
            try: os.remove(item_path)
            except: pass

@app.post("/api/cache/clear")
async def clear_cache_endpoint():
    """Xóa sạch bộ nhớ đêm ngay lập tức (chống nghẽn Event Loop)"""
    try:
        await asyncio.to_thread(_clear_all_cache)
        return {"status": "success", "message": "Đã dọn sạch cache."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/search")
async def search_movies(q: str):
    """Proxy tìm kiếm phim từ PhimAPI"""
    try:
        url = f"https://phimapi.com/v1/api/tim-kiem?keyword={urllib.parse.quote(q)}&limit=30"
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error searching movies: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/movie/{slug}")
async def get_movie_detail(slug: str):
    """Proxy và chuẩn hóa chi tiết phim để tối ưu hiệu năng client"""
    try:
        url = f"https://phimapi.com/phim/{slug}"
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("status"):
            return data
            
        movie_raw = data.get("movie", {})
        # Rút gọn thông tin phim cần thiết
        movie_clean = {
            "name": movie_raw.get("name"),
            "slug": movie_raw.get("slug"),
            "origin_name": movie_raw.get("origin_name"),
            "poster_url": movie_raw.get("poster_url"),
            "year": str(movie_raw.get("year")),
            "episode_current": movie_raw.get("episode_current"),
            "time": movie_raw.get("time"),
            "quality": movie_raw.get("quality"),
            "lang": movie_raw.get("lang"),
            "content": movie_raw.get("content")
        }
        
        # Rút gọn danh sách tập phim (chỉ lấy server đầu tiên có dữ liệu)
        episodes_clean = []
        raw_eps = data.get("episodes", [])
        main_server = next((srv for srv in raw_eps if srv.get("server_data")), None)
        if main_server:
            for ep in main_server.get("server_data", []):
                if ep.get("link_m3u8"):
                    episodes_clean.append({
                        "name": ep.get("name"),
                        "link_m3u8": ep.get("link_m3u8")
                    })
                    
        return {
            "status": "success",
            "movie": movie_clean,
            "episodes": episodes_clean
        }
    except Exception as e:
        logger.error(f"Error getting movie detail '{slug}': {e}")
        return {"status": "error", "message": str(e)}

SAVED_MOVIES_FILE = os.path.join(BASE_DIR, "saved_movies.json")

def _load_saved_movies():
    if not os.path.exists(SAVED_MOVIES_FILE):
        return {}
    try:
        with open(SAVED_MOVIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_saved_movies(data):
    try:
        with open(SAVED_MOVIES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving movies json: {e}")

@app.get("/api/saved")
async def get_saved_movies_api():
    data = await asyncio.to_thread(_load_saved_movies)
    return list(data.values())

@app.post("/api/saved")
async def save_movie_api(movie: dict):
    slug = movie.get("slug")
    if not slug:
        return {"status": "error", "message": "Missing slug"}
    
    data = await asyncio.to_thread(_load_saved_movies)
    if slug in data:
        data[slug].update(movie)
    else:
        data[slug] = movie
        if "last_watched_episode" not in data[slug]:
            data[slug]["last_watched_episode"] = ""
        if "last_watched_url" not in data[slug]:
            data[slug]["last_watched_url"] = ""
            
    await asyncio.to_thread(_save_saved_movies, data)
    return {"status": "success", "movie": data[slug]}

@app.post("/api/saved/progress")
async def save_movie_progress_api(progress: dict):
    slug = progress.get("slug")
    last_ep = progress.get("last_watched_episode")
    last_url = progress.get("last_watched_url")
    if not slug:
        return {"status": "error", "message": "Missing slug"}
        
    data = await asyncio.to_thread(_load_saved_movies)
    if slug in data:
        data[slug]["last_watched_episode"] = last_ep
        data[slug]["last_watched_url"] = last_url
        
        # Khởi tạo episode_states nếu chưa có
        if "episode_states" not in data[slug]:
            data[slug]["episode_states"] = {}
            
        # Đổi trạng thái từ "watching" thành "watched" cho các tập trước đó
        for ep_url, state in list(data[slug]["episode_states"].items()):
            if state == "watching":
                data[slug]["episode_states"][ep_url] = "watched"
                
        # Lưu tập hiện tại ở trạng thái "watching"
        data[slug]["episode_states"][last_url] = "watching"
        
        await asyncio.to_thread(_save_saved_movies, data)
        return {"status": "success", "movie": data[slug]}
    return {"status": "error", "message": "Movie not in saved list"}

@app.delete("/api/saved/{slug}")
async def delete_saved_movie_api(slug: str):
    data = await asyncio.to_thread(_load_saved_movies)
    if slug in data:
        del data[slug]
        await asyncio.to_thread(_save_saved_movies, data)
        return {"status": "success"}
    return {"status": "error", "message": "Movie not found"}

@app.on_event("shutdown")
async def shutdown_event():
    await client.aclose()
