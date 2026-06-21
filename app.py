import m3u8
import urllib.parse
import hashlib
import os
import aiofiles
import shutil
import time
import asyncio
import sqlite3
import re
import unicodedata
from datetime import datetime, date
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse, FileResponse
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
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
DB_FILE = os.path.join(BASE_DIR, "homeflix.db")
DOWNLOADS_STATUS_FILE = os.path.join(BASE_DIR, "downloads.json")

MAX_CACHE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
MAX_CACHE_AGE = 6 * 60 * 60              # 6 giờ (giây)

PREFETCH_CONCURRENCY = 4
prefetch_semaphore = asyncio.Semaphore(PREFETCH_CONCURRENCY)

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR, exist_ok=True)

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_movies (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            poster_url TEXT,
            year TEXT,
            episode_current TEXT,
            total_episodes INTEGER,
            last_watched_episode TEXT,
            last_watched_url TEXT,
            episodes TEXT,
            episode_states TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

# Helper SQLite cho saved_movies
def _db_get_all_movies():
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM saved_movies ORDER BY updated_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    movies = []
    for r in rows:
        m = dict(r)
        m["episodes"] = json.loads(m["episodes"]) if m["episodes"] else []
        m["episode_states"] = json.loads(m["episode_states"]) if m["episode_states"] else {}
        movies.append(m)
    return movies

async def get_all_movies_from_db():
    return await asyncio.to_thread(_db_get_all_movies)

def _db_get_movie(slug):
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM saved_movies WHERE slug = ?", (slug,))
    row = cursor.fetchone()
    conn.close()
    if row:
        m = dict(row)
        m["episodes"] = json.loads(m["episodes"]) if m["episodes"] else []
        m["episode_states"] = json.loads(m["episode_states"]) if m["episode_states"] else {}
        return m
    return None

async def get_movie_from_db(slug):
    return await asyncio.to_thread(_db_get_movie, slug)

def _db_save_movie(movie):
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    cursor = conn.cursor()
    
    slug = movie.get("slug")
    name = movie.get("name", "")
    poster_url = movie.get("poster_url", "")
    year = str(movie.get("year", ""))
    episode_current = movie.get("episode_current", "")
    total_episodes = movie.get("total_episodes")
    last_watched_episode = movie.get("last_watched_episode", "")
    last_watched_url = movie.get("last_watched_url", "")
    
    episodes = json.dumps(movie.get("episodes", []), ensure_ascii=False)
    episode_states = json.dumps(movie.get("episode_states", {}), ensure_ascii=False)
    updated_at = datetime.now().isoformat()
    
    cursor.execute("""
        INSERT INTO saved_movies (
            slug, name, poster_url, year, episode_current, total_episodes,
            last_watched_episode, last_watched_url, episodes, episode_states, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            name=excluded.name,
            poster_url=excluded.poster_url,
            year=excluded.year,
            episode_current=excluded.episode_current,
            total_episodes=excluded.total_episodes,
            last_watched_episode=excluded.last_watched_episode,
            last_watched_url=excluded.last_watched_url,
            episodes=excluded.episodes,
            episode_states=excluded.episode_states,
            updated_at=excluded.updated_at
    """, (
        slug, name, poster_url, year, episode_current, total_episodes,
        last_watched_episode, last_watched_url, episodes, episode_states, updated_at
    ))
    conn.commit()
    conn.close()

async def save_movie_to_db(movie):
    await asyncio.to_thread(_db_save_movie, movie)

def _db_delete_movie(slug):
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM saved_movies WHERE slug = ?", (slug,))
    conn.commit()
    conn.close()

async def delete_movie_from_db(slug):
    await asyncio.to_thread(_db_delete_movie, slug)

# Tải xuống MP4 qua ffmpeg
def clean_filename(name: str) -> str:
    # Bỏ dấu tiếng Việt để tránh lỗi filesystem/locale khi truyền vào subprocess ffmpeg
    nfkd_form = unicodedata.normalize('NFKD', name)
    no_accent = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    no_accent = no_accent.replace('đ', 'd').replace('Đ', 'D')
    
    s = re.sub(r'[\\/*?:"<>|]', "", no_accent)
    s = re.sub(r'\s+', '-', s).strip()
    if not s:
        s = hashlib.md5(name.encode()).hexdigest()
    return s

def _load_downloads_status():
    if not os.path.exists(DOWNLOADS_STATUS_FILE):
        return {}
    try:
        with open(DOWNLOADS_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _save_downloads_status(data):
    try:
        # Ghi tạm ra file tmp rồi rename để tránh hỏng file khi mất điện/crash giữa chừng
        tmp_file = DOWNLOADS_STATUS_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_file, DOWNLOADS_STATUS_FILE)
    except Exception as e:
        logger.error(f"Error saving downloads status: {e}")

async def get_download_status(movie_slug: str, ep_name: str) -> str:
    data = await asyncio.to_thread(_load_downloads_status)
    key = f"{movie_slug}/{ep_name}"
    clean_ep = clean_filename(ep_name)
    file_path = os.path.join(DOWNLOAD_DIR, movie_slug, f"{clean_ep}.mp4")
    status = data.get(key, {}).get("status", "not_started")
    if status == "completed" and not os.path.exists(file_path):
        return "not_started"
    return status

async def update_download_status(movie_slug: str, ep_name: str, status: str, error_msg: str = None, delete_at: float = None):
    data = await asyncio.to_thread(_load_downloads_status)
    key = f"{movie_slug}/{ep_name}"
    if key not in data:
        data[key] = {}
    data[key]["status"] = status
    if error_msg:
        data[key]["error"] = error_msg
    else:
        data[key].pop("error", None)
    if delete_at is not None:
        data[key]["delete_at"] = delete_at
    else:
        data[key].pop("delete_at", None)
    await asyncio.to_thread(_save_downloads_status, data)

download_queue = asyncio.Queue()
queued_items = set()

async def add_to_download_queue(movie_slug: str, ep_name: str, ep_url: str):
    key = f"{movie_slug}/{ep_name}"
    status = await get_download_status(movie_slug, ep_name)
    if status in ("completed", "downloading"):
        return
    if key in queued_items:
        return
    queued_items.add(key)
    await update_download_status(movie_slug, ep_name, "pending")
    await download_queue.put((movie_slug, ep_name, ep_url))

async def enforce_download_window(movie_slug: str):
    # Disabled automatic queueing per user request
    pass

async def delayed_cleanup_worker():
    await asyncio.sleep(10)
    while True:
        try:
            now = time.time()
            data = await asyncio.to_thread(_load_downloads_status)
            modified = False
            for key, info in list(data.items()):
                delete_at = info.get("delete_at")
                if delete_at and now >= delete_at:
                    parts = key.split("/", 1)
                    if len(parts) == 2:
                        movie_slug, ep_name = parts
                        clean_ep = clean_filename(ep_name)
                        file_path = os.path.join(DOWNLOAD_DIR, movie_slug, f"{clean_ep}.mp4")
                        if os.path.exists(file_path):
                            try:
                                os.remove(file_path)
                                logger.info(f"[Cleanup] Deleted watched episode file after 3h delay: {file_path}")
                            except Exception as e:
                                logger.warning(f"[Cleanup] Failed to delete file {file_path}: {e}")
                        data.pop(key, None)
                        modified = True
            if modified:
                await asyncio.to_thread(_save_downloads_status, data)
        except Exception as e:
            logger.error(f"Error in delayed_cleanup_worker: {e}")
        await asyncio.sleep(60)

async def download_worker():
    await asyncio.sleep(2)
    try:
        data = await asyncio.to_thread(_load_downloads_status)
        movies = await get_all_movies_from_db()
        movies_by_slug = {m["slug"]: m for m in movies}
        for key, info in data.items():
            status = info.get("status")
            if status in ("pending", "downloading"):
                parts = key.split("/", 1)
                if len(parts) == 2:
                    movie_slug, ep_name = parts
                    if movie_slug in movies_by_slug:
                        movie = movies_by_slug[movie_slug]
                        ep_url = None
                        for ep in movie.get("episodes", []):
                            if ep.get("name") == ep_name:
                                ep_url = ep.get("link_m3u8")
                                break
                        if ep_url:
                            queued_items.discard(key)
                            await add_to_download_queue(movie_slug, ep_name, ep_url)
    except Exception as e:
        logger.error(f"Error re-queuing downloads on startup: {e}")

    while True:
        try:
            movie_slug, ep_name, ep_url = await download_queue.get()
            key = f"{movie_slug}/{ep_name}"
            queued_items.discard(key)
            
            await update_download_status(movie_slug, ep_name, "downloading")
            movie_dir = os.path.join(DOWNLOAD_DIR, movie_slug)
            os.makedirs(movie_dir, exist_ok=True)
            
            clean_ep = clean_filename(ep_name)
            output_path = os.path.join(movie_dir, f"{clean_ep}.mp4")
            part_path = output_path + ".part"
            
            logger.info(f"[Download Worker] Bắt đầu tải {key} -> {output_path}")
            ua_str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            
            cmd = [
                "ffmpeg", "-y",
                "-user_agent", ua_str,
                "-i", ep_url,
                "-c", "copy",
                "-f", "mp4",
                part_path
            ]
            
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
            except FileNotFoundError:
                logger.error("[Download Worker] Lệnh ffmpeg không tồn tại! Hãy chắc chắn ffmpeg đã được cài đặt trên hệ thống.")
                await update_download_status(movie_slug, ep_name, "failed", "ffmpeg not installed")
                continue
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600.0)
                exit_code = proc.returncode
            except asyncio.TimeoutError:
                logger.error(f"[Download Worker] Timeout tải {key}")
                try: proc.kill()
                except: pass
                exit_code = -1
                stderr = b"Timeout error"
                
            if exit_code != 0:
                logger.warning(f"[Download Worker] Thất bại -c copy, đang thử lại bằng re-encoding audio...")
                cmd_retry = [
                    "ffmpeg", "-y",
                    "-user_agent", ua_str,
                    "-i", ep_url,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-f", "mp4",
                    part_path
                ]
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd_retry,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600.0)
                    exit_code = proc.returncode
                except FileNotFoundError:
                    logger.error("[Download Worker] Lệnh ffmpeg không tồn tại khi retry!")
                    await update_download_status(movie_slug, ep_name, "failed", "ffmpeg not installed")
                    continue
                except asyncio.TimeoutError:
                    logger.error(f"[Download Worker] Timeout retry tải {key}")
                    try: proc.kill()
                    except: pass
                    exit_code = -1
                    stderr = b"Timeout error"
                    
            if exit_code == 0 and os.path.exists(part_path):
                os.rename(part_path, output_path)
                await update_download_status(movie_slug, ep_name, "completed")
                logger.info(f"[Download Worker] Hoàn thành tải {key}")
            else:
                err_msg = stderr.decode('utf-8', errors='ignore')[-200:] if stderr else "Unknown error"
                await update_download_status(movie_slug, ep_name, "failed", err_msg)
                logger.error(f"[Download Worker] Lỗi tải {key}: {err_msg}")
                if os.path.exists(part_path):
                    try: os.remove(part_path)
                    except: pass
                    
        except Exception as e:
            logger.error(f"[Download Worker] Lỗi hệ thống trong worker: {e}")
        finally:
            download_queue.task_done()


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

async def _prefetch_one(target_url: str, sid: str):
    session_dir = os.path.join(CACHE_DIR, sid)
    if not os.path.exists(session_dir):
        os.makedirs(session_dir, exist_ok=True)

    url_hash = hashlib.md5(target_url.encode()).hexdigest()
    cache_path = os.path.join(session_dir, f"{url_hash}.ts")
    part_path = os.path.join(session_dir, f"{url_hash}.ts.part")
    lock_id = f"{sid}_{url_hash}"

    if os.path.exists(cache_path):
        return

    if lock_id in download_locks:
        return

    async with prefetch_semaphore:
        # Re-check to avoid race condition
        if os.path.exists(cache_path) or lock_id in download_locks:
            return

        event = asyncio.Event()
        download_locks[lock_id] = event
        try:
            logger.info(f"[Prefetch] Tải: {target_url}")
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            async with client.stream("GET", target_url, headers=headers) as resp:
                if resp.status_code != 200:
                    raise httpx.HTTPStatusError(
                        f"Origin trả {resp.status_code}", request=resp.request, response=resp
                    )

                async with aiofiles.open(part_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        await f.write(chunk)

                if os.path.exists(part_path):
                    os.rename(part_path, cache_path)
            logger.info(f"[Prefetch] Hoàn thành: {target_url}")
        except Exception as e:
            logger.warning(f"[Prefetch] Thất bại cho {target_url}: {e}")
            if os.path.exists(part_path):
                try: os.remove(part_path)
                except Exception: pass
        finally:
            event.set()
            if lock_id in download_locks:
                del download_locks[lock_id]

async def prefetch_episode(segment_urls: list[str], key_url: str | None, sid: str):
    if key_url:
        logger.info(f"[Prefetch] Bắt đầu tải Key giải mã trước: {key_url}")
        await _prefetch_one(key_url, sid)

    if segment_urls:
        logger.info(f"[Prefetch] Bắt đầu tải {len(segment_urls)} segment của tập phim...")
        tasks = [asyncio.create_task(_prefetch_one(url, sid)) for url in segment_urls]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[Prefetch] Hoàn thành toàn bộ prefetch cho tập phim: {sid}")

app = FastAPI(title="HomeFlix Proxy Player")

# Phục vụ các file tĩnh (manifest, icons)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

@app.on_event("startup")
async def startup_event():
    # Khởi tạo database SQLite
    init_db()
    # Khởi động tác vụ dọn dẹp cache ngầm
    asyncio.create_task(prune_cache())
    # Khởi động background worker tải phim MP4
    asyncio.create_task(download_worker())
    # Khởi động background worker xóa phim đã xem sau 3h
    asyncio.create_task(delayed_cleanup_worker())


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
    encoded_target = urllib.parse.quote(target_url, safe="")
    res = f"{path}?url={encoded_target}"
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
            original_segment_urls = []
            original_key_url = None

            for segment in playlist.segments:
                abs_uri = segment.absolute_uri
                original_segment_urls.append(abs_uri)
                segment.uri = make_proxy_url(request, "/proxy/ts", abs_uri, sid)
                
            for key in playlist.keys:
                if key and key.uri:
                    abs_uri = key.absolute_uri
                    original_key_url = abs_uri
                    key.uri = make_proxy_url(request, "/proxy/ts", abs_uri, sid)
            
            # Khởi chạy prefetch toàn bộ tập phim dưới nền
            asyncio.create_task(prefetch_episode(original_segment_urls, original_key_url, sid))
                    
        return PlainTextResponse(
            playlist.dumps(), 
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache"}
        )
        
    except Exception as e:
        logger.error(f"Error fetching proxy m3u8 '{url}': {e}")
        return PlainTextResponse(f"Proxy Error: {str(e)}", status_code=500)

async def fetch_and_cache_full(url: str, cache_path: str, part_path: str, event: asyncio.Event) -> bytes:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                event.set()
                raise HTTPException(
                    status_code=502,
                    detail=f"Origin trả status code {resp.status_code} cho segment: {url}"
                )
            chunks = []
            async with aiofiles.open(part_path, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    await f.write(chunk)
                    chunks.append(chunk)
        if os.path.exists(part_path):
            os.rename(part_path, cache_path)
        event.set()
        return b"".join(chunks)
    except HTTPException as he:
        event.set()
        if os.path.exists(part_path):
            try: os.remove(part_path)
            except Exception: pass
        raise he
    except asyncio.CancelledError:
        event.set()
        if os.path.exists(part_path):
            try: os.remove(part_path)
            except Exception: pass
        raise
    except Exception as e:
        logger.error(f"Lỗi tải segment: {e}")
        event.set()
        if os.path.exists(part_path):
            try: os.remove(part_path)
            except Exception: pass
        raise HTTPException(status_code=502, detail=f"Lỗi tải segment từ nguồn: {str(e)}")

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
        size = os.path.getsize(cache_path)
        media_type = "application/octet-stream" if size == 16 else "video/MP2T"
        return FileResponse(cache_path, media_type=media_type, headers={"X-Cache": "HIT"})
    
    # 2. Cơ chế Single Flight: Kiểm tra xem có ai đang tải đoạn này chưa
    if lock_id in download_locks:
        await download_locks[lock_id].wait()
        if os.path.exists(cache_path):
            size = os.path.getsize(cache_path)
            media_type = "application/octet-stream" if size == 16 else "video/MP2T"
            return FileResponse(cache_path, media_type=media_type, headers={"X-Cache": "HIT-QUEUED"})

    # 3. Tạo Lock và tải xuống
    event = asyncio.Event()
    download_locks[lock_id] = event
    try:
        data = await fetch_and_cache_full(url, cache_path, part_path, event)
    finally:
        if lock_id in download_locks:
            del download_locks[lock_id]

    media_type = "application/octet-stream" if len(data) == 16 else "video/MP2T"
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "X-Cache": "MISS",
            "Content-Length": str(len(data))
        }
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
            "poster_url": movie_raw.get("poster_url") if movie_raw.get("poster_url", "").startswith("http") else f"https://phimimg.com/{movie_raw.get('poster_url', '').lstrip('/')}",
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

@app.get("/api/saved")
async def get_saved_movies_api():
    movies = await get_all_movies_from_db()
    return movies

@app.post("/api/saved")
async def save_movie_api(movie: dict):
    slug = movie.get("slug")
    if not slug:
        return {"status": "error", "message": "Missing slug"}
    
    existing = await get_movie_from_db(slug)
    if existing:
        existing.update(movie)
        movie = existing
    else:
        if "last_watched_episode" not in movie:
            movie["last_watched_episode"] = ""
        if "last_watched_url" not in movie:
            movie["last_watched_url"] = ""
        if "episode_states" not in movie:
            movie["episode_states"] = {}
            
    # Sửa lỗi total_episodes dựa trên độ dài episodes
    if "episodes" in movie and isinstance(movie["episodes"], list):
        movie["total_episodes"] = len(movie["episodes"])
        
    await save_movie_to_db(movie)
    return {"status": "success", "movie": movie}

@app.post("/api/saved/progress")
async def save_movie_progress_api(progress: dict):
    slug = progress.get("slug")
    last_ep = progress.get("last_watched_episode")
    last_url = progress.get("last_watched_url")
    if not slug:
        return {"status": "error", "message": "Missing slug"}
        
    movie = await get_movie_from_db(slug)
    if movie:
        movie["last_watched_episode"] = last_ep
        movie["last_watched_url"] = last_url
        
        if "episode_states" not in movie:
            movie["episode_states"] = {}
            
        previously_watching_urls = []
        for ep_url, state in list(movie["episode_states"].items()):
            if state == "watching":
                movie["episode_states"][ep_url] = "watched"
                previously_watching_urls.append(ep_url)
                
        movie["episode_states"][last_url] = "watching"
        
        # Xóa các file tập đã xem sau 3 giờ
        for ep in movie.get("episodes", []):
            if ep.get("link_m3u8") in previously_watching_urls:
                clean_ep = clean_filename(ep.get("name"))
                file_path = os.path.join(DOWNLOAD_DIR, slug, f"{clean_ep}.mp4")
                if os.path.exists(file_path):
                    try:
                        await update_download_status(slug, ep.get("name"), "completed", delete_at=time.time() + 10800)
                        logger.info(f"Đã lên lịch xóa file tập đã xem xong sau 3 giờ: {file_path}")
                    except Exception as e:
                        logger.warning(f"Không thể đặt lịch xóa file {file_path}: {e}")
                        
        await save_movie_to_db(movie)
        return {"status": "success", "movie": movie}
    return {"status": "error", "message": "Movie not in saved list"}

@app.delete("/api/saved/{slug}")
async def delete_saved_movie_api(slug: str):
    movie = await get_movie_from_db(slug)
    if movie:
        await delete_movie_from_db(slug)
        # Xóa toàn bộ file tải xuống của phim này
        movie_dir = os.path.join(DOWNLOAD_DIR, slug)
        if os.path.exists(movie_dir):
            try:
                shutil.rmtree(movie_dir)
                logger.info(f"Đã xóa thư mục tải xuống: {movie_dir}")
            except Exception as e:
                logger.warning(f"Lỗi khi xóa thư mục {movie_dir}: {e}")
        return {"status": "success"}
    return {"status": "error", "message": "Movie not found"}

@app.post("/api/download")
async def download_episode_api(payload: dict):
    movie_slug = payload.get("movie_slug")
    ep_name = payload.get("episode_name")
    ep_url = payload.get("episode_url")
    if not movie_slug or not ep_name or not ep_url:
        raise HTTPException(status_code=400, detail="Missing fields")
    await add_to_download_queue(movie_slug, ep_name, ep_url)
    return {"status": "success"}

@app.get("/api/download/status")
async def get_all_downloads_status_api():
    data = await asyncio.to_thread(_load_downloads_status)
    res = {}
    for ep_key, info in data.items():
        parts = ep_key.split("/", 1)
        if len(parts) == 2:
            movie_slug, ep_name = parts
            status = info.get("status", "not_started")
            clean_ep = clean_filename(ep_name)
            file_path = os.path.join(DOWNLOAD_DIR, movie_slug, f"{clean_ep}.mp4")
            if status == "completed" and not os.path.exists(file_path):
                status = "not_started"
            if movie_slug not in res:
                res[movie_slug] = {}
            res[movie_slug][ep_name] = status
    return res

@app.get("/api/download/status/{movie_slug}")
async def get_movie_downloads_status_api(movie_slug: str):
    data = await asyncio.to_thread(_load_downloads_status)
    res = {}
    for ep_key, info in data.items():
        if ep_key.startswith(f"{movie_slug}/"):
            ep_name = ep_key.split("/", 1)[1]
            status = info.get("status", "not_started")
            clean_ep = clean_filename(ep_name)
            file_path = os.path.join(DOWNLOAD_DIR, movie_slug, f"{clean_ep}.mp4")
            if status == "completed" and not os.path.exists(file_path):
                status = "not_started"
            res[ep_name] = status
    return res

@app.get("/api/download/status/{movie_slug}/{episode_name}")
async def get_episode_download_status_api(movie_slug: str, episode_name: str):
    status = await get_download_status(movie_slug, episode_name)
    return {"status": status}

@app.get("/media/{movie_slug}/{episode_name}.mp4")
async def get_media_file_api(movie_slug: str, episode_name: str):
    logger.info(f"[Media API] Yêu cầu: movie_slug={movie_slug}, episode_name={episode_name}")
    clean_ep = clean_filename(episode_name)
    file_path = os.path.join(DOWNLOAD_DIR, movie_slug, f"{clean_ep}.mp4")
    exists = os.path.exists(file_path)
    logger.info(f"[Media API] Đường dẫn file: {file_path}, Tồn tại: {exists}")
    if not exists:
        raise HTTPException(status_code=404, detail="File video chưa sẵn sàng hoặc không tồn tại.")
    return FileResponse(file_path, media_type="video/mp4")

@app.get("/api/recommendations")
async def get_recommendations_api():
    try:
        url = "https://phimapi.com/danh-sach/phim-moi-cap-nhat?page=1"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        
        movie_list = []
        for item in items:
            p_url = item.get("poster_url", "")
            if p_url and not p_url.startswith("http"):
                p_url = f"https://phimimg.com/{p_url.lstrip('/')}"
            
            movie_list.append({
                "slug": item.get("slug"),
                "name": item.get("name"),
                "poster_url": p_url,
                "year": item.get("year", "")
            })
            
        return {
            "Phim mới cập nhật": movie_list
        }
    except Exception as e:
        logger.error(f"Error fetching newly updated movies: {e}")
        return {"Phim mới cập nhật": []}

@app.on_event("shutdown")
async def shutdown_event():
    await client.aclose()
