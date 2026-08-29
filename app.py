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
import threading
from datetime import datetime, date
from contextlib import asynccontextmanager
from functools import lru_cache
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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
POSTER_CACHE_DIR = os.path.join(CACHE_DIR, "posters")
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
DB_FILE = os.path.join(BASE_DIR, "homeflix.db")
DOWNLOADS_STATUS_FILE = os.path.join(BASE_DIR, "downloads.json")

MAX_CACHE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
MAX_CACHE_AGE = 6 * 60 * 60              # 6 giờ (giây)

PREFETCH_CONCURRENCY = 4
prefetch_semaphore = asyncio.Semaphore(PREFETCH_CONCURRENCY)

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR, exist_ok=True)

if not os.path.exists(POSTER_CACHE_DIR):
    os.makedirs(POSTER_CACHE_DIR, exist_ok=True)

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- SQLite Connection Pool (thread-safe singleton) ---
# Thay vì mở/đóng connection cho mỗi query, dùng 1 connection chia sẻ
# với Lock để đảm bảo thread-safe khi chạy với asyncio.to_thread.
_db_conn = None
_db_lock = threading.Lock()

def get_db() -> sqlite3.Connection:
    """Trả về connection SQLite singleton (thread-safe).
    
    Connection được tạo 1 lần và tái sử dụng, tránh overhead mở/đóng
    liên tục. PRAGMA được thiết lập cho hiệu năng tối đa:
    - WAL: ghi không block đọc
    - synchronous=NORMAL: cân bằng tốc độ/an toàn
    - cache_size=-64MB: cache lớn trong RAM
    - temp_store=MEMORY: temp table trong RAM
    - mmap_size=256MB: memory-mapped I/O
    """
    global _db_conn
    if _db_conn is None:
        with _db_lock:
            if _db_conn is None:
                conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=-65536")   # 64MB
                conn.execute("PRAGMA temp_store=MEMORY")
                conn.execute("PRAGMA mmap_size=268435456")  # 256MB
                conn.execute("PRAGMA wal_autocheckpoint=1000")
                _db_conn = conn
                logger.info("[DB] SQLite connection pool initialized (WAL, 64MB cache, 256MB mmap)")
    return _db_conn

def db_execute(sql: str, params=(), commit: bool = True) -> sqlite3.Cursor:
    """Thực thi SQL với thread-safe lock, trả về cursor."""
    conn = get_db()
    with _db_lock:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        if commit:
            conn.commit()
        return cursor

def db_query(sql: str, params=()) -> list:
    """Query SELECT với thread-safe lock, trả về list of rows."""
    conn = get_db()
    with _db_lock:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()

def db_query_one(sql: str, params=()) -> sqlite3.Row | None:
    """Query SELECT trả về 1 row đầu tiên."""
    conn = get_db()
    with _db_lock:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchone()

def init_db():
    conn = get_db()
    with _db_lock:
        cursor = conn.cursor()
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS episode_downloads (
                movie_slug TEXT NOT NULL,
                episode_name TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                delete_at REAL,
                PRIMARY KEY (movie_slug, episode_name)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                response_json TEXT NOT NULL,
                cached_at REAL NOT NULL,
                ttl_seconds INTEGER NOT NULL
            )
        """)
        # --- INDEX: tăng tốc query thường dùng ---
        # episode_downloads: query theo movie_slug (rất thường xuyên)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ep_dl_slug ON episode_downloads(movie_slug)")
        # episode_downloads: cleanup worker query theo delete_at
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ep_dl_delete_at ON episode_downloads(delete_at)")
        # episode_downloads: query status pending/downloading khi restart
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ep_dl_status ON episode_downloads(status)")
        # saved_movies: ORDER BY updated_at DESC
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_movies_updated ON saved_movies(updated_at DESC)")
        # api_cache: cleanup theo cached_at
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_cache_cached_at ON api_cache(cached_at)")
        conn.commit()
    logger.info("[DB] Tables + indexes initialized")

def migrate_downloads_json_to_db():
    if not os.path.exists(DOWNLOADS_STATUS_FILE):
        return
    try:
        with open(DOWNLOADS_STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        conn = get_db()
        with _db_lock:
            cursor = conn.cursor()
            for key, info in data.items():
                parts = key.split("/", 1)
                if len(parts) == 2:
                    movie_slug, ep_name = parts
                    status = info.get("status", "not_started")
                    error = info.get("error")
                    delete_at = info.get("delete_at")
                    cursor.execute("""
                        INSERT OR REPLACE INTO episode_downloads (movie_slug, episode_name, status, error, delete_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (movie_slug, ep_name, status, error, delete_at))
            conn.commit()
        os.remove(DOWNLOADS_STATUS_FILE)
        logger.info("[Migration] Successfully migrated downloads.json into SQLite DB.")
    except Exception as e:
        logger.error(f"[Migration] Error migrating downloads.json: {e}")

# --- API Cache helpers (SQLite-backed, stale-fallback) ---
def _db_get_cache(cache_key: str):
    row = db_query_one(
        "SELECT response_json, cached_at, ttl_seconds FROM api_cache WHERE cache_key = ?",
        (cache_key,)
    )
    if row:
        data = json.loads(row[0])
        is_fresh = (time.time() - row[1]) < row[2]
        return {"data": data, "is_fresh": is_fresh}
    return None

def _db_set_cache(cache_key: str, response_obj, ttl_seconds: int):
    db_execute("""
        INSERT OR REPLACE INTO api_cache (cache_key, response_json, cached_at, ttl_seconds)
        VALUES (?, ?, ?, ?)
    """, (cache_key, json.dumps(response_obj, ensure_ascii=False), time.time(), ttl_seconds))

async def cached_fetch(cache_key: str, ttl_seconds: int, fetch_fn):
    """Cache-aside with stale-fallback: trả cache cũ nếu phimapi.com lỗi."""
    cached = await asyncio.to_thread(_db_get_cache, cache_key)
    if cached and cached["is_fresh"]:
        logger.info(f"[Cache] HIT key={cache_key}")
        return cached["data"]
    logger.info(f"[Cache] MISS key={cache_key}, gọi phimapi.com")
    try:
        fresh_data = await fetch_fn()
        await asyncio.to_thread(_db_set_cache, cache_key, fresh_data, ttl_seconds)
        return fresh_data
    except Exception as e:
        logger.warning(f"[Cache] Lỗi gọi phimapi.com cho key={cache_key}: {e}")
        if cached:
            logger.info(f"[Cache] Trả stale cache cho key={cache_key} (đã hết hạn)")
            return cached["data"]
        raise

# Helper SQLite cho saved_movies
def _db_get_all_movies():
    rows = db_query("SELECT * FROM saved_movies ORDER BY updated_at DESC")
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
    row = db_query_one("SELECT * FROM saved_movies WHERE slug = ?", (slug,))
    if row:
        m = dict(row)
        m["episodes"] = json.loads(m["episodes"]) if m["episodes"] else []
        m["episode_states"] = json.loads(m["episode_states"]) if m["episode_states"] else {}
        return m
    return None

async def get_movie_from_db(slug):
    return await asyncio.to_thread(_db_get_movie, slug)

def _db_save_movie(movie):
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
    
    db_execute("""
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

async def save_movie_to_db(movie):
    await asyncio.to_thread(_db_save_movie, movie)

def _db_delete_movie(slug):
    db_execute("DELETE FROM saved_movies WHERE slug = ?", (slug,))

async def delete_movie_from_db(slug):
    await asyncio.to_thread(_db_delete_movie, slug)

# Tải xuống MP4 qua ffmpeg
@lru_cache(maxsize=512)
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

def _db_get_download_status(movie_slug: str, ep_name: str) -> dict:
    row = db_query_one(
        "SELECT status, error, delete_at FROM episode_downloads WHERE movie_slug = ? AND episode_name = ?",
        (movie_slug, ep_name)
    )
    if row:
        return {"status": row[0], "error": row[1], "delete_at": row[2]}
    return {"status": "not_started", "error": None, "delete_at": None}

def _db_update_download_status(movie_slug: str, ep_name: str, status: str, error_msg: str = None, delete_at: float = None):
    db_execute("""
        INSERT INTO episode_downloads (movie_slug, episode_name, status, error, delete_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(movie_slug, episode_name) DO UPDATE SET
            status=excluded.status,
            error=excluded.error,
            delete_at=excluded.delete_at
    """, (movie_slug, ep_name, status, error_msg, delete_at))

def _db_get_all_downloads_status(movie_slug: str = None) -> dict:
    if movie_slug:
        rows = db_query(
            "SELECT episode_name, status FROM episode_downloads WHERE movie_slug = ?",
            (movie_slug,)
        )
        res = {row[0]: row[1] for row in rows}
    else:
        rows = db_query("SELECT movie_slug, episode_name, status FROM episode_downloads")
        res = {}
        for row in rows:
            m_slug, ep_name, status = row
            if m_slug not in res:
                res[m_slug] = {}
            res[m_slug][ep_name] = status
    return res

async def get_download_status(movie_slug: str, ep_name: str) -> str:
    info = await asyncio.to_thread(_db_get_download_status, movie_slug, ep_name)
    status = info.get("status", "not_started")
    clean_ep = clean_filename(ep_name)
    file_path = os.path.join(DOWNLOAD_DIR, movie_slug, f"{clean_ep}.mp4")
    if status == "completed" and not os.path.exists(file_path):
        await asyncio.to_thread(_db_update_download_status, movie_slug, ep_name, "not_started")
        return "not_started"
    return status

async def update_download_status(movie_slug: str, ep_name: str, status: str, error_msg: str = None, delete_at: float = None):
    await asyncio.to_thread(_db_update_download_status, movie_slug, ep_name, status, error_msg, delete_at)

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

def _db_get_deletable_episodes(now: float) -> list:
    return db_query(
        "SELECT movie_slug, episode_name FROM episode_downloads WHERE delete_at IS NOT NULL AND delete_at <= ?",
        (now,)
    )

def _db_delete_download_record(movie_slug: str, ep_name: str):
    db_execute(
        "DELETE FROM episode_downloads WHERE movie_slug = ? AND episode_name = ?",
        (movie_slug, ep_name)
    )

async def delayed_cleanup_worker():
    await asyncio.sleep(10)
    while True:
        try:
            now = time.time()
            deletable = await asyncio.to_thread(_db_get_deletable_episodes, now)
            for movie_slug, ep_name in deletable:
                clean_ep = clean_filename(ep_name)
                file_path = os.path.join(DOWNLOAD_DIR, movie_slug, f"{clean_ep}.mp4")
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        logger.info(f"[Cleanup] Deleted watched episode file after 3h delay: {file_path}")
                    except Exception as e:
                        logger.warning(f"[Cleanup] Failed to delete file {file_path}: {e}")
                await asyncio.to_thread(_db_delete_download_record, movie_slug, ep_name)
        except Exception as e:
            logger.error(f"Error in delayed_cleanup_worker: {e}")
        await asyncio.sleep(60)

def _db_reset_stuck_downloads():
    db_execute(
        "UPDATE episode_downloads SET status = 'pending' WHERE status = 'downloading'"
    )
    logger.info("[Startup] Reset stuck 'downloading' episodes to 'pending'")

def _db_get_queued_downloads() -> list:
    return db_query(
        "SELECT movie_slug, episode_name FROM episode_downloads WHERE status IN ('pending', 'downloading')"
    )

async def download_worker():
    await asyncio.sleep(2)
    try:
        queued = await asyncio.to_thread(_db_get_queued_downloads)
        movies = await get_all_movies_from_db()
        movies_by_slug = {m["slug"]: m for m in movies}
        for movie_slug, ep_name in queued:
            if movie_slug in movies_by_slug:
                movie = movies_by_slug[movie_slug]
                ep_url = None
                for ep in movie.get("episodes", []):
                    if ep.get("name") == ep_name:
                        ep_url = ep.get("link_m3u8")
                        break
                if ep_url:
                    key = f"{movie_slug}/{ep_name}"
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

# HTTP client (khởi tạo trong lifespan; None cho đến khi app start)
client: httpx.AsyncClient | None = None

# --- Lifespan handler (thay thế @app.on_event deprecated) ---
# Quản lý startup + shutdown trong 1 context manager duy nhất.
# Background tasks được track để cancel sạch khi shutdown.
_bg_tasks: list[asyncio.Task] = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, workers. Shutdown: close httpx client, cancel tasks."""
    global client
    # Khởi tạo database SQLite
    init_db()
    # Chạy di chuyển dữ liệu downloads.json nếu có
    migrate_downloads_json_to_db()
    # Reset các download bị treo do server restart
    await asyncio.to_thread(_db_reset_stuck_downloads)
    # Khởi tạo HTTP client (chuyển vào lifespan để đóng sạch khi shutdown)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0, write=5.0, pool=10.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    # Khởi động các background worker
    _bg_tasks.append(asyncio.create_task(prune_cache()))
    _bg_tasks.append(asyncio.create_task(download_worker()))
    _bg_tasks.append(asyncio.create_task(delayed_cleanup_worker()))
    _bg_tasks.append(asyncio.create_task(home_cache_warmer()))
    logger.info("[Startup] HomeFlix ready — DB, workers, httpx client initialized")
    yield
    # Shutdown: hủy background tasks + đóng httpx client
    for t in _bg_tasks:
        t.cancel()
    await asyncio.gather(*_bg_tasks, return_exceptions=True)
    if client is not None:
        await client.aclose()
    logger.info("[Shutdown] HomeFlix stopped — tasks cancelled, httpx client closed")

app = FastAPI(title="HomeFlix Proxy Player", lifespan=lifespan)

# --- CachedStaticFiles: thêm Cache-Control header cho static files ---
# Static files (icons, manifest, CSS, JS) hiếm khi thay đổi → browser cache
# giảm RTT và bandwidth. Cache-Control: max-age=7d + immutable cho phép
# browser sử dụng cached mà không cần revalidate.
class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        # Chỉ cache response 200 (không cache 404)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return response

# Phục vụ các file tĩnh (manifest, icons) với cache headers
app.mount("/static", CachedStaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


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

# --- Browser cache cho API responses (GET) ---
# Các API /api/home/* và /api/search, /api/movie/* đã có server-side cache
# (cached_fetch). Thêm Cache-Control header để browser cũng cache tạm thời,
# giảm RTT khi user điều hướng lại trang. max-age ngắn (60s) + stale-while-
# revalidate để luôn tươi mà không chờ.
@app.middleware("http")
async def api_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.method == "GET" and response.status_code == 200:
        path = request.url.path
        # Cache ngắn cho homepage sections (data thay đổi mỗi 10-30 phút)
        if path.startswith("/api/home/"):
            response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=600"
        # Cache search & movie detail ngắn hơn (user-specific nhưng ổn định)
        elif path.startswith("/api/search") or path.startswith("/api/movie/"):
            response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300"
        elif path.startswith("/proxy/image"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response

# GZip nén response HTTP (JSON, HTML) giảm bandwidth ~70% cho text responses
app.add_middleware(GZipMiddleware, minimum_size=1024)

@app.get("/proxy/image")
async def proxy_image(url: str):
    """Proxy và disk-cache poster/hỉnh ảnh phim để tăng tốc độ tải trên client & PWA"""
    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid image URL")
    
    url_hash = hashlib.md5(url.encode()).hexdigest()
    # Tìm ext thích hợp
    ext = ".jpg"
    if ".png" in url.lower():
        ext = ".png"
    elif ".webp" in url.lower():
        ext = ".webp"
    
    file_path = os.path.join(POSTER_CACHE_DIR, f"{url_hash}{ext}")
    
    if os.path.exists(file_path):
        media_type = "image/png" if ext == ".png" else "image/webp" if ext == ".webp" else "image/jpeg"
        return FileResponse(file_path, media_type=media_type, headers={"X-Cache": "HIT", "Cache-Control": "public, max-age=31536000, immutable"})
    
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        resp = await client.get(url, headers=headers, follow_redirects=True, timeout=15.0)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Unable to fetch remote image")
        
        content = resp.content
        part_path = f"{file_path}.part"
        async with aiofiles.open(part_path, "wb") as f:
            await f.write(content)
        if os.path.exists(part_path):
            os.rename(part_path, file_path)
            
        content_type = resp.headers.get("content-type", "image/jpeg")
        return Response(content=content, media_type=content_type, headers={"X-Cache": "MISS", "Cache-Control": "public, max-age=31536000, immutable"})
    except Exception as e:
        logger.warning(f"[ImageProxy] Lỗi tải ảnh {url}: {e}")
        # Redirect đến url gốc nếu fail
        return Response(status_code=307, headers={"Location": url})

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
    """Proxy tìm kiếm phim từ PhimAPI (cached 30 phút, stale-fallback)"""
    async def _fetch():
        url = f"https://phimapi.com/v1/api/tim-kiem?keyword={urllib.parse.quote(q)}&limit=30"
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
    try:
        return await cached_fetch(f"search:{q}", 1800, _fetch)
    except Exception as e:
        logger.error(f"Error searching movies: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/movie/{slug}")
async def get_movie_detail(slug: str):
    """Proxy và chuẩn hóa chi tiết phim để tối ưu hiệu năng client (cached 60 phút, stale-fallback)"""
    async def _fetch():
        url = f"https://phimapi.com/phim/{slug}"
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("status"):
            return data
            
        movie_raw = data.get("movie", {})
        # Rút gọn thông tin phim cần thiết
        poster_raw = movie_raw.get("poster_url") or ""
        poster_url = _make_img_proxy_url(poster_raw)
        movie_clean = {
            "name": movie_raw.get("name"),
            "slug": movie_raw.get("slug"),
            "origin_name": movie_raw.get("origin_name"),
            "poster_url": poster_url,
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
    try:
        return await cached_fetch(f"movie:{slug}", 3600, _fetch)
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
        
        # 1. Xóa các record tải xuống của phim trong DB
        await asyncio.to_thread(
            lambda ms=slug: db_execute("DELETE FROM episode_downloads WHERE movie_slug = ?", (ms,))
        )
        
        # 2. Xóa các thư mục cache phân đoạn liên quan đến từng tập phim
        for ep in movie.get("episodes", []):
            ep_url = ep.get("link_m3u8")
            if ep_url:
                sid = hashlib.md5(ep_url.encode()).hexdigest()
                session_dir = os.path.join(CACHE_DIR, sid)
                if os.path.exists(session_dir):
                    try:
                        shutil.rmtree(session_dir)
                        logger.info(f"Đã xóa thư mục cache tập phim: {session_dir}")
                    except Exception as e:
                        logger.warning(f"Lỗi khi xóa cache {session_dir}: {e}")
                        
        # 3. Xóa thư mục tải xuống của phim
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
    raw_status = await asyncio.to_thread(_db_get_all_downloads_status)
    res = {}
    for movie_slug, eps in raw_status.items():
        res[movie_slug] = {}
        for ep_name, status in eps.items():
            clean_ep = clean_filename(ep_name)
            file_path = os.path.join(DOWNLOAD_DIR, movie_slug, f"{clean_ep}.mp4")
            if status == "completed" and not os.path.exists(file_path):
                status = "not_started"
            res[movie_slug][ep_name] = status
    return res

@app.get("/api/download/status/{movie_slug}")
async def get_movie_downloads_status_api(movie_slug: str):
    raw_status = await asyncio.to_thread(_db_get_all_downloads_status, movie_slug)
    res = {}
    for ep_name, status in raw_status.items():
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

def _make_img_proxy_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    full_url = raw_url if raw_url.startswith("http") else f"https://phimimg.com/{raw_url.lstrip('/')}"
    return f"/proxy/image?url={urllib.parse.quote(full_url, safe='')}"

def _normalize_movie_list(items: list) -> list:
    """Normalize KKPhim v1 API items to uniform format."""
    result = []
    for item in items:
        poster = item.get("poster_url", "")
        thumb = item.get("thumb_url", "")
        result.append({
            "slug": item.get("slug"),
            "name": item.get("name"),
            "origin_name": item.get("origin_name"),
            "poster_url": _make_img_proxy_url(poster),
            "thumb_url": _make_img_proxy_url(thumb),
            "year": item.get("year", ""),
            "quality": item.get("quality", ""),
            "lang": item.get("lang", ""),
            "episode_current": item.get("episode_current", ""),
            "tmdb": item.get("tmdb", {})
        })
    return result

def _danh_sach_cache_key(section: str, page: int, category: str = "", country: str = "", year: str = "") -> str:
    """Cache key cho một danh sách + bộ lọc. Rỗng = không lọc."""
    return f"home:{section}:{page}:{category}:{country}:{year}"


async def _fetch_danh_sach(type_slug: str, page: int, category: str = "", country: str = "", year: str = ""):
    """Fetch + normalize danh sách phim từ phimapi với bộ lọc (AND)."""
    url = f"https://phimapi.com/v1/api/danh-sach/{type_slug}"
    params = {"page": page}
    if category:
        params["category"] = category
    if country:
        params["country"] = country
    if year:
        params["year"] = year
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data", {}).get("items", []) if isinstance(data.get("data"), dict) else data.get("items", [])
    pagination = data.get("data", {}).get("params", {}).get("pagination", {}) if isinstance(data.get("data"), dict) else data.get("pagination", {})
    return {"items": _normalize_movie_list(items), "pagination": pagination}

# --- Smart Homepage sections ---

@app.get("/api/home/latest")
async def home_latest(page: int = 1):
    """Phim mới cập nhật (KKPhim home API, cache 30 phút)"""
    async def _fetch():
        url = f"https://phimapi.com/v1/api/home?page={page}"
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get("items", []) if isinstance(data.get("data"), dict) else data.get("items", [])
        pagination = data.get("data", {}).get("params", {}).get("pagination", {}) if isinstance(data.get("data"), dict) else data.get("pagination", {})
        return {
            "items": _normalize_movie_list(items),
            "pagination": pagination
        }
    try:
        return await cached_fetch(f"home:latest:{page}", 1800, _fetch)
    except Exception as e:
        logger.error(f"Error fetching home/latest: {e}")
        return {"items": [], "pagination": {}, "error": str(e)}

@app.get("/api/home/phim-le")
async def home_phim_le(page: int = 1, category: str = "", country: str = "", year: str = ""):
    """Phim Lẻ + bộ lọc thể loại/quốc gia/năm (cache 1 giờ theo tổ hợp lọc)"""
    try:
        return await cached_fetch(
            _danh_sach_cache_key("phim-le", page, category, country, year), 3600,
            lambda: _fetch_danh_sach("phim-le", page, category, country, year))
    except Exception as e:
        logger.error(f"Error fetching home/phim-le: {e}")
        return {"items": [], "pagination": {}, "error": str(e)}

@app.get("/api/home/phim-chieu-rap")
async def home_phim_chieu_rap(page: int = 1, category: str = "", country: str = "", year: str = ""):
    """Phim Chiếu Rạp + bộ lọc thể loại/quốc gia/năm (cache 1 giờ theo tổ hợp lọc)"""
    try:
        return await cached_fetch(
            _danh_sach_cache_key("phim-chieu-rap", page, category, country, year), 3600,
            lambda: _fetch_danh_sach("phim-chieu-rap", page, category, country, year))
    except Exception as e:
        logger.error(f"Error fetching home/phim-chieu-rap: {e}")
        return {"items": [], "pagination": {}, "error": str(e)}


@app.get("/api/home/phim-bo")
async def home_phim_bo(page: int = 1, category: str = "", country: str = "", year: str = ""):
    """Phim Bộ + bộ lọc thể loại/quốc gia/năm (cache 1 giờ theo tổ hợp lọc)"""
    try:
        return await cached_fetch(
            _danh_sach_cache_key("phim-bo", page, category, country, year), 3600,
            lambda: _fetch_danh_sach("phim-bo", page, category, country, year))
    except Exception as e:
        logger.error(f"Error fetching home/phim-bo: {e}")
        return {"items": [], "pagination": {}, "error": str(e)}


@app.get("/api/home/tv-shows")
async def home_tv_shows(page: int = 1, category: str = "", country: str = "", year: str = ""):
    """TV Shows + bộ lọc thể loại/quốc gia/năm (cache 1 giờ theo tổ hợp lọc)"""
    try:
        return await cached_fetch(
            _danh_sach_cache_key("tv-shows", page, category, country, year), 3600,
            lambda: _fetch_danh_sach("tv-shows", page, category, country, year))
    except Exception as e:
        logger.error(f"Error fetching home/tv-shows: {e}")
        return {"items": [], "pagination": {}, "error": str(e)}

async def _fetch_filter_list(api_path: str):
    """Fetch danh sách lọc (thể loại/quốc gia) từ phimapi."""
    resp = await client.get(f"https://phimapi.com/v1/api/{api_path}")
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data", {}).get("items", [])
    return {"items": [{"name": it.get("name", ""), "slug": it.get("slug", "")} for it in items if it.get("slug")]}


@app.get("/api/home/categories")
async def home_categories():
    """Danh sách thể loại cho bộ lọc (cache 24 giờ)"""
    try:
        return await cached_fetch("home:categories", 86400, lambda: _fetch_filter_list("the-loai"))
    except Exception as e:
        logger.error(f"Error fetching home/categories: {e}")
        return {"items": [], "error": str(e)}


@app.get("/api/home/countries")
async def home_countries():
    """Danh sách quốc gia cho bộ lọc (cache 24 giờ)"""
    try:
        return await cached_fetch("home:countries", 86400, lambda: _fetch_filter_list("quoc-gia"))
    except Exception as e:
        logger.error(f"Error fetching home/countries: {e}")
        return {"items": [], "error": str(e)}

# --- Background cache warmer cho Homepage ---
WARM_CACHE_INTERVAL = 600  # 10 phút refresh cache homepage

async def _warm_section(section_name: str, page: int, ttl: int, url_template: str):
    """Helper: fetch + cache một section."""
    async def _fetch():
        url = url_template.format(page=page)
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get("items", []) if isinstance(data.get("data"), dict) else data.get("items", [])
        pagination = data.get("data", {}).get("params", {}).get("pagination", {}) if isinstance(data.get("data"), dict) else data.get("pagination", {})
        return {
            "items": _normalize_movie_list(items),
            "pagination": pagination
        }
    try:
        await cached_fetch(f"home:{section_name}:{page}", ttl, _fetch)
        logger.info(f"[CacheWarmer] OK section={section_name} page={page}")
    except Exception as e:
        logger.warning(f"[CacheWarmer] FAIL section={section_name} page={page}: {e}")

async def _warm_danh_sach(section: str, page: int):
    """Làm nóng cache cho một danh sách (không lọc) với đúng định dạng key mới."""
    try:
        await cached_fetch(
            _danh_sach_cache_key(section, page), 3600,
            lambda: _fetch_danh_sach(section, page))
        logger.info(f"[CacheWarmer] OK section={section} page={page}")
    except Exception as e:
        logger.warning(f"[CacheWarmer] FAIL section={section} page={page}: {e}")

async def home_cache_warmer():
    """Định kỳ làm mới cache homepage để user luôn có data nhanh."""
    while True:
        logger.info("[CacheWarmer] Bắt đầu làm mới cache homepage...")
        # Page 1 cho cả 5 section (có thể mở rộng thêm page sau)
        await _warm_section("latest", 1, 1800, "https://phimapi.com/v1/api/home?page={page}")
        await _warm_danh_sach("phim-le", 1)
        await _warm_danh_sach("phim-chieu-rap", 1)
        await _warm_danh_sach("phim-bo", 1)
        await _warm_danh_sach("tv-shows", 1)
        logger.info(f"[CacheWarmer] Đợi {WARM_CACHE_INTERVAL}s cho lần refresh tiếp theo...")
        await asyncio.sleep(WARM_CACHE_INTERVAL)
