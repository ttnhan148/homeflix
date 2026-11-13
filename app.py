import m3u8
import urllib.parse
import hashlib
import hmac
import os
import aiofiles
import shutil
import time
import asyncio
import contextlib
import sqlite3
import re
import unicodedata
import threading
import uuid
import math
import secrets
from datetime import datetime
from functools import lru_cache
from fastapi import Depends, FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.responses import PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
import httpx
import logging
import json
import ipaddress
import admin
import settings
import hometv
from cast import CastHub, format_sse, validate_command, validate_state
from devices import (DEVICE_KINDS, client_ip, default_device_name, device_kind,
                     household_id, parse_trusted_proxies)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Track in-flight downloads (Single Flight) to avoid duplicate fetches
download_locks = {} 

# Use absolute paths so it runs reliably under Linux/Systemd
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
POSTER_CACHE_DIR = os.path.join(CACHE_DIR, "posters")
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
# Container mounts a volume here so the DB survives image rebuilds
DATA_DIR = os.environ.get("HOMEFLIX_DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "homeflix.db")
DOWNLOADS_STATUS_FILE = os.path.join(DATA_DIR, "downloads.json")

# --- Device identity (no login) ---
DEVICE_COOKIE = "hf_device"
DEVICE_COOKIE_MAX_AGE = 63072000  # 2 years
# Only issue the cookie on "/" and these routes; not on /proxy, /media, /static
DEVICE_COOKIE_PATHS = ("/api/saved", "/api/device", "/api/cast", "/api/settings", "/api/admin")
# Only trusted reverse proxy IPs (comma-separated) may have X-Forwarded-For read
TRUSTED_PROXIES = parse_trusted_proxies(os.environ.get("TRUSTED_PROXIES"))

# System settings (cache, prefetch, homepage refresh) are read directly from settings_store
_prefetch_sem: tuple[int, asyncio.Semaphore] | None = None

for _dir in (CACHE_DIR, POSTER_CACHE_DIR, DOWNLOAD_DIR):
    os.makedirs(_dir, exist_ok=True)

# Browser User-Agent sent upstream (some CDNs reject non-browser clients)
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
UPSTREAM_HEADERS = {"User-Agent": BROWSER_UA}


def _remove_quietly(path: str) -> None:
    """Delete a file, ignoring a missing file or any OS error."""
    with contextlib.suppress(OSError):
        os.remove(path)

# --- SQLite Connection Pool (thread-safe singleton) ---
# Instead of opening/closing a connection per query, use one shared connection
# guarded by a Lock for thread safety when used with asyncio.to_thread.
_db_conn = None
_db_lock = threading.Lock()

def get_db() -> sqlite3.Connection:
    """Return the singleton SQLite connection (thread-safe).
    
    The connection is created once and reused, avoiding the overhead of
    repeated open/close. PRAGMAs are tuned for maximum performance:
    - WAL: writes do not block reads
    - synchronous=NORMAL: balance of speed and safety
    - cache_size=-64MB: large in-RAM cache
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
    """Execute SQL under the thread-safe lock and return the cursor."""
    conn = get_db()
    with _db_lock:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        if commit:
            conn.commit()
        return cursor

def db_query(sql: str, params=()) -> list:
    """Run a SELECT under the thread-safe lock and return a list of rows."""
    conn = get_db()
    with _db_lock:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()

def db_query_one(sql: str, params=()) -> sqlite3.Row | None:
    """Run a SELECT and return the first row."""
    conn = get_db()
    with _db_lock:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchone()

settings_store = settings.SettingsStore(lambda: get_db(), _db_lock)

def setting(key: str) -> int | bool:
    return settings_store.get(key)

def get_prefetch_semaphore() -> asyncio.Semaphore:
    """Current semaphore; changing prefetch.concurrency creates a new one, while running prefetches keep the old one."""
    global _prefetch_sem
    n = setting("prefetch.concurrency")
    if _prefetch_sem is None or _prefetch_sem[0] != n:
        _prefetch_sem = (n, asyncio.Semaphore(n))
    return _prefetch_sem[1]

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
        # --- Devices: each device has its own saved list and watch progress ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                household TEXT NOT NULL,
                user_agent TEXT,
                last_seen REAL NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS device_movies (
                device_id TEXT NOT NULL,
                slug TEXT NOT NULL,
                name TEXT NOT NULL,
                poster_url TEXT,
                year TEXT,
                episode_current TEXT,
                total_episodes INTEGER,
                last_watched_episode TEXT,
                last_watched_url TEXT,
                position_sec REAL,
                duration_sec REAL,
                episodes TEXT,
                episode_states TEXT,
                saved INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (device_id, slug)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cast_allowed (
                target_id TEXT NOT NULL,
                controller_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (target_id, controller_id)
            )
        """)
        # Admin column for older DBs
        if "is_admin" not in [r[1] for r in cursor.execute("PRAGMA table_info(devices)")]:
            cursor.execute("ALTER TABLE devices ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        # Admin access requests via 6-digit code (expiry checked at read time)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_requests (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                code TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_requests_device ON admin_requests(device_id, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_requests_status ON admin_requests(status, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_devices_household ON devices(household)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_movies_updated ON device_movies(device_id, updated_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_movies_slug ON device_movies(slug)")
        # --- INDEX: speed up common queries ---
        # episode_downloads: queried by movie_slug (very frequently)
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
    settings_store.invalidate()  # drop stale cached values (the DB may have changed)
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
    """Cache-aside with stale-fallback: return stale cache if phimapi.com fails."""
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

# --- Devices ---
def get_household_salt() -> str:
    """Salt used to hash an IP into a "home". Generated once, stored in the DB."""
    conn = get_db()
    with _db_lock:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'household_salt'").fetchone()
        if row:
            return row[0]
        salt = secrets.token_hex(16)
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('household_salt', ?)", (salt,))
        conn.commit()
        return salt

DEVICE_LAST_SEEN_THROTTLE = 60  # seconds: do not write last_seen more often than this

def _db_touch_device(device_id: str, household: str, kind: str, user_agent: str, now: float,
                     adopt_legacy: bool = True) -> dict:
    """Insert the device if missing, otherwise update last_seen (at most every 60 seconds)."""
    conn = get_db()
    with _db_lock:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        created = row is None
        if created:
            conn.execute(
                "INSERT INTO devices (id, name, kind, household, user_agent, last_seen, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (device_id, default_device_name(kind, user_agent), kind, household, user_agent, now, now),
            )
            flagged = conn.execute("SELECT 1 FROM app_settings WHERE key = 'legacy_adopted'").fetchone()
            if adopt_legacy and not flagged:
                # The first device (via /api, not a bot loading "/") adopts the entire old shared library.
                # The legacy_adopted flag ensures this happens once, even after all devices are purged.
                conn.execute("INSERT INTO app_settings (key, value) VALUES ('legacy_adopted', '1')")
                conn.execute("""
                    INSERT INTO device_movies (
                        device_id, slug, name, poster_url, year, episode_current, total_episodes,
                        last_watched_episode, last_watched_url, position_sec, duration_sec,
                        episodes, episode_states, saved, updated_at)
                    SELECT ?, slug, name, poster_url, year, episode_current, total_episodes,
                           last_watched_episode, last_watched_url, NULL, NULL,
                           episodes, episode_states, 1, updated_at
                    FROM saved_movies
                """, (device_id,))
        elif row["household"] != household or now - row["last_seen"] >= DEVICE_LAST_SEEN_THROTTLE:
            conn.execute("UPDATE devices SET household = ?, last_seen = ? WHERE id = ?",
                         (household, now, device_id))
        conn.commit()
        result = dict(conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone())
    result["created"] = created
    return result

def _register_request_device(device_id: str, ip: str, user_agent: str, adopt_legacy: bool = True) -> dict:
    household = household_id(ip, get_household_salt())
    return _db_touch_device(device_id, household, device_kind(user_agent), user_agent, time.time(),
                            adopt_legacy=adopt_legacy)

def _public_device(row: dict) -> dict:
    # Never return "id": it is the hf_device auth cookie value itself.
    return {k: row[k] for k in ("name", "kind", "created_at", "last_seen")}

def _valid_device_cookie(value: str | None) -> str | None:
    """Accept only canonical hyphenated UUIDv4; anything else is treated as no cookie."""
    if not value:
        return None
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    if parsed.version != 4 or str(parsed) != value.lower():
        return None
    return str(parsed)

DEVICE_MAX_INACTIVE_DAYS = 180

def _db_delete_device(conn, device_id: str) -> None:
    """Delete a device and every row tied to it. Call while holding _db_lock; does not commit."""
    conn.execute("DELETE FROM device_movies WHERE device_id = ?", (device_id,))
    conn.execute("DELETE FROM cast_allowed WHERE target_id = ? OR controller_id = ?", (device_id, device_id))
    conn.execute("DELETE FROM app_settings WHERE key = ?", (_cast_allow_key(device_id),))
    conn.execute("DELETE FROM admin_requests WHERE device_id = ?", (device_id,))
    conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))

def _db_prune_inactive_devices(now: float, max_age_days: int = DEVICE_MAX_INACTIVE_DAYS) -> int:
    """Delete devices inactive for more than max_age_days along with their movies. Returns the number deleted."""
    cutoff = now - max_age_days * 86400
    conn = get_db()
    with _db_lock:
        old_ids = [r[0] for r in conn.execute("SELECT id FROM devices WHERE last_seen < ?", (cutoff,))]
        for device_id in old_ids:
            _db_delete_device(conn, device_id)
        conn.commit()
    return len(old_ids)

# --- Per-device admin rights ---
admin_login_limiter = admin.LoginLimiter()

# --- Admin password stored in the DB (scrypt hash); HOMEFLIX_ADMIN_PASSWORD, if set, wins ---
ADMIN_HASH_KEY = "admin_password_hash"

def _db_admin_hash() -> str | None:
    row = db_query_one("SELECT value FROM app_settings WHERE key = ?", (ADMIN_HASH_KEY,))
    return row[0] if row else None

def _db_set_admin_hash(value: str) -> None:
    db_execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (ADMIN_HASH_KEY, value))

def _admin_configured() -> bool:
    return admin.env_managed() or _db_admin_hash() is not None

def _log_admin_password(password: str, reason: str) -> None:
    bar = "=" * 64
    logger.warning(f"\n{bar}\n  {reason}\n  Mật khẩu admin: {password}\n"
                   f"  Đăng nhập trong Cài đặt > Quản trị, rồi đổi mật khẩu tại đó.\n{bar}")

def ensure_admin_password() -> str | None:
    """First start (no env password, nothing stored): generate one, store its hash and log it once.
    Returns the generated password, or None when one already exists."""
    if _admin_configured():
        return None
    password = admin.generate_password()
    _db_set_admin_hash(admin.hash_password(password))
    _log_admin_password(password, "Lần chạy đầu: đã tạo mật khẩu admin")
    return password

def reset_admin_password() -> str:
    """Forgotten password: replace the stored one with a fresh random one (CLI: python app.py reset-admin-password)."""
    password = admin.generate_password()
    _db_set_admin_hash(admin.hash_password(password))
    return password

def _db_is_admin(device_id: str) -> bool:
    row = db_query_one("SELECT is_admin FROM devices WHERE id = ?", (device_id,))
    return row is not None and bool(row[0])

def _db_set_admin(device_id: str, on: bool) -> None:
    db_execute("UPDATE devices SET is_admin = ? WHERE id = ?", (1 if on else 0, device_id))

def _db_admin_count() -> int:
    return db_query_one("SELECT COUNT(*) FROM devices WHERE is_admin = 1")[0]

def _now() -> float:
    return time.time()

# --- Admin requests via 6-digit code: the TV requests, an admin device approves or denies ---
def _db_create_admin_request(device_id: str, now: float) -> dict | None:
    """Create a new request (replacing this device's old one). None when MAX_PENDING unexpired requests already exist."""
    conn = get_db()
    with _db_lock:
        conn.execute("DELETE FROM admin_requests WHERE created_at < ?", (now - 86400,))
        conn.execute("DELETE FROM admin_requests WHERE device_id = ?", (device_id,))
        pending = conn.execute(
            "SELECT COUNT(*) FROM admin_requests WHERE status = 'pending' AND created_at + ? > ?",
            (admin.REQUEST_TTL, now)).fetchone()[0]
        if pending >= admin.MAX_PENDING:
            conn.commit()
            return None
        req = {"id": secrets.token_urlsafe(9), "code": admin.new_code()}
        conn.execute("INSERT INTO admin_requests (id, device_id, code, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                     (req["id"], device_id, req["code"], now))
        conn.commit()
    req["expires_at"] = now + admin.REQUEST_TTL
    return req

def _db_my_admin_request(device_id: str, now: float) -> dict:
    row = db_query_one(
        "SELECT code, status, created_at FROM admin_requests WHERE device_id = ? ORDER BY created_at DESC LIMIT 1",
        (device_id,))
    if row is None:
        return {"status": "none"}
    expires_at = row["created_at"] + admin.REQUEST_TTL
    if row["status"] == "pending":
        if expires_at <= now:
            return {"status": "expired"}
        return {"status": "pending", "code": row["code"], "expires_at": expires_at}
    return {"status": row["status"]}

def _db_pending_admin_requests(now: float) -> list[dict]:
    rows = db_query(
        "SELECT r.id, r.code, r.created_at, d.id AS device_id, d.name, d.kind "
        "FROM admin_requests r JOIN devices d ON d.id = r.device_id "
        "WHERE r.status = 'pending' AND r.created_at + ? > ? ORDER BY r.created_at",
        (admin.REQUEST_TTL, now))
    salt = get_household_salt()
    return [{"id": r["id"],
             "device": {"id": _cast_public_id(r["device_id"], salt), "name": r["name"], "kind": r["kind"]},
             "code": r["code"], "expires_at": r["created_at"] + admin.REQUEST_TTL} for r in rows]

def _db_decide_admin_request(request_id: str, approve: bool, now: float) -> bool:
    """Settle a request with one conditional UPDATE (only one side wins); approval grants admin in the same transaction."""
    conn = get_db()
    with _db_lock:
        try:
            cur = conn.execute(
                "UPDATE admin_requests SET status = ? WHERE id = ? AND status = 'pending' "
                "AND created_at + ? > ? AND EXISTS (SELECT 1 FROM devices WHERE id = admin_requests.device_id)",
                ("approved" if approve else "denied", request_id, admin.REQUEST_TTL, now))
            if cur.rowcount != 1:
                conn.rollback()
                return False
            if approve:
                conn.execute(
                    "UPDATE devices SET is_admin = 1 WHERE id = (SELECT device_id FROM admin_requests WHERE id = ?)",
                    (request_id,))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise

# --- Cast: control permissions and the TV's toggle ---
# SSE queues, presence, links and state live in memory (cast.py); only correct with a single worker.
cast_hub = CastHub()

def _cast_allow_key(device_id: str) -> str:
    return f"cast_allow_control:{device_id}"

def _db_cast_is_allowed(target_id: str, controller_id: str) -> bool:
    return db_query_one("SELECT 1 FROM cast_allowed WHERE target_id = ? AND controller_id = ?",
                        (target_id, controller_id)) is not None

def _db_cast_allow(target_id: str, controller_id: str) -> None:
    db_execute("INSERT OR IGNORE INTO cast_allowed (target_id, controller_id, created_at) VALUES (?, ?, ?)",
               (target_id, controller_id, time.time()))

def _db_cast_allow_control(device_id: str) -> bool:
    row = db_query_one("SELECT value FROM app_settings WHERE key = ?", (_cast_allow_key(device_id),))
    return row is None or row[0] != "0"

def _db_cast_set_allow_control(device_id: str, value: bool) -> None:
    db_execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (_cast_allow_key(device_id), "1" if value else "0"))

# --- Per-device movies (device_movies) ---
def _device_movie_dict(row) -> dict:
    m = dict(row)
    m["episodes"] = json.loads(m["episodes"]) if m["episodes"] else []
    m["episode_states"] = json.loads(m["episode_states"]) if m["episode_states"] else {}
    m["position"] = m.pop("position_sec")
    m["duration"] = m.pop("duration_sec")
    m["saved"] = bool(m["saved"])
    m.pop("device_id", None)
    return m

def _db_list_device_movies(device_id: str) -> list[dict]:
    """Movies saved or with watch progress on the device, most recently updated first."""
    rows = db_query(
        "SELECT * FROM device_movies WHERE device_id = ? AND (saved = 1 "
        "OR (last_watched_url IS NOT NULL AND last_watched_url != '') OR position_sec IS NOT NULL) "
        "ORDER BY updated_at DESC", (device_id,))
    return [_device_movie_dict(r) for r in rows]

def _db_get_device_movie(device_id: str, slug: str) -> dict | None:
    row = db_query_one("SELECT * FROM device_movies WHERE device_id = ? AND slug = ?", (device_id, slug))
    return _device_movie_dict(row) if row else None

def _db_upsert_device_movie(device_id: str, movie: dict) -> None:
    db_execute("""
        INSERT INTO device_movies (
            device_id, slug, name, poster_url, year, episode_current, total_episodes,
            last_watched_episode, last_watched_url, position_sec, duration_sec,
            episodes, episode_states, saved, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id, slug) DO UPDATE SET
            name=excluded.name,
            poster_url=excluded.poster_url,
            year=excluded.year,
            episode_current=excluded.episode_current,
            total_episodes=excluded.total_episodes,
            last_watched_episode=excluded.last_watched_episode,
            last_watched_url=excluded.last_watched_url,
            position_sec=COALESCE(excluded.position_sec, device_movies.position_sec),
            duration_sec=COALESCE(excluded.duration_sec, device_movies.duration_sec),
            episodes=excluded.episodes,
            episode_states=excluded.episode_states,
            saved=excluded.saved,
            updated_at=excluded.updated_at
    """, (
        device_id, movie.get("slug"), movie.get("name", ""), movie.get("poster_url", ""),
        str(movie.get("year", "")), movie.get("episode_current", ""), movie.get("total_episodes"),
        movie.get("last_watched_episode", ""), movie.get("last_watched_url", ""),
        movie.get("position"), movie.get("duration"),
        json.dumps(movie.get("episodes", []), ensure_ascii=False),
        json.dumps(movie.get("episode_states", {}), ensure_ascii=False),
        1 if movie.get("saved", True) else 0,
        datetime.now().isoformat(),
    ))

def _db_delete_device_movie(device_id: str, slug: str) -> bool:
    cursor = db_execute("DELETE FROM device_movies WHERE device_id = ? AND slug = ?", (device_id, slug))
    return cursor.rowcount > 0

def _db_other_devices_hold(device_id: str, slug: str) -> bool:
    """True if another device still holds this movie (downloaded files and cache are shared)."""
    return db_query_one("SELECT 1 FROM device_movies WHERE slug = ? AND device_id != ? LIMIT 1",
                        (slug, device_id)) is not None

def _db_all_device_movies() -> list[dict]:
    """One row per slug (the most recently updated), used by the download worker."""
    seen, result = set(), []
    for r in db_query("SELECT * FROM device_movies ORDER BY updated_at DESC"):
        if r["slug"] not in seen:
            seen.add(r["slug"])
            result.append(_device_movie_dict(r))
    return result

async def get_device_movies(device_id: str):
    return await asyncio.to_thread(_db_list_device_movies, device_id)

async def get_device_movie(device_id: str, slug: str):
    return await asyncio.to_thread(_db_get_device_movie, device_id, slug)

async def save_device_movie(device_id: str, movie: dict):
    await asyncio.to_thread(_db_upsert_device_movie, device_id, movie)

async def delete_device_movie(device_id: str, slug: str):
    return await asyncio.to_thread(_db_delete_device_movie, device_id, slug)

# Download MP4 via ffmpeg
@lru_cache(maxsize=512)
def clean_filename(name: str) -> str:
    # Strip Vietnamese diacritics to avoid filesystem/locale errors when passing to the ffmpeg subprocess
    nfkd_form = unicodedata.normalize('NFKD', name)
    no_accent = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    no_accent = no_accent.replace('đ', 'd').replace('Đ', 'D')
    
    s = re.sub(r'[\\/*?:"<>|]', "", no_accent)
    s = re.sub(r'\s+', '-', s).strip()
    if not s:
        s = hashlib.md5(name.encode()).hexdigest()
    return s

def _episode_file(movie_slug: str, ep_name: str) -> str:
    """Path of an episode's downloaded MP4."""
    return os.path.join(DOWNLOAD_DIR, movie_slug, f"{clean_filename(ep_name)}.mp4")

def _effective_status(movie_slug: str, ep_name: str, status: str) -> str:
    """'completed' whose file has since disappeared counts as 'not_started'."""
    if status == "completed" and not os.path.exists(_episode_file(movie_slug, ep_name)):
        return "not_started"
    return status

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
        res = {}
        for m_slug, ep_name, status in db_query("SELECT movie_slug, episode_name, status FROM episode_downloads"):
            res.setdefault(m_slug, {})[ep_name] = status
    return res

async def get_download_status(movie_slug: str, ep_name: str) -> str:
    info = await asyncio.to_thread(_db_get_download_status, movie_slug, ep_name)
    status = info.get("status", "not_started")
    effective = _effective_status(movie_slug, ep_name, status)
    if effective != status:
        await asyncio.to_thread(_db_update_download_status, movie_slug, ep_name, effective)
    return effective

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
                file_path = _episode_file(movie_slug, ep_name)
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

FFMPEG_TIMEOUT = 600.0

def _ffmpeg_cmd(src_url: str, out_path: str, codec_args: list[str]) -> list[str]:
    return ["ffmpeg", "-y", "-user_agent", BROWSER_UA, "-i", src_url, *codec_args, "-f", "mp4", out_path]

async def _run_ffmpeg(cmd: list[str], key: str) -> tuple[int, bytes]:
    """Run ffmpeg; return (exit code, stderr). Timeout -> kill, (-1, b"Timeout error").
    FileNotFoundError (ffmpeg missing) propagates to the caller."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=FFMPEG_TIMEOUT)
        return proc.returncode, stderr
    except asyncio.TimeoutError:
        logger.error(f"[Download Worker] Timeout tải {key}")
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()  # reap it so the retry does not race a still-writing ffmpeg
        return -1, b"Timeout error"

async def download_worker():
    await asyncio.sleep(2)
    try:
        queued = await asyncio.to_thread(_db_get_queued_downloads)
        movies = await asyncio.to_thread(_db_all_device_movies)
        movies_by_slug = {m["slug"]: m for m in movies}
        for movie_slug, ep_name in queued:
            if movie_slug in movies_by_slug:
                ep_url = next((ep.get("link_m3u8") for ep in movies_by_slug[movie_slug].get("episodes", [])
                               if ep.get("name") == ep_name), None)
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
            try:
                # Strategy 1: stream copy; strategy 2: copy video, re-encode audio to AAC
                exit_code, stderr = await _run_ffmpeg(_ffmpeg_cmd(ep_url, part_path, ["-c", "copy"]), key)
                if exit_code != 0:
                    logger.warning("[Download Worker] Thất bại -c copy, đang thử lại bằng re-encoding audio...")
                    exit_code, stderr = await _run_ffmpeg(
                        _ffmpeg_cmd(ep_url, part_path, ["-c:v", "copy", "-c:a", "aac"]), key)
            except FileNotFoundError:
                logger.error("[Download Worker] Lệnh ffmpeg không tồn tại! Hãy chắc chắn ffmpeg đã được cài đặt trên hệ thống.")
                await update_download_status(movie_slug, ep_name, "failed", "ffmpeg not installed")
                continue

            if exit_code == 0 and os.path.exists(part_path):
                os.rename(part_path, output_path)
                await update_download_status(movie_slug, ep_name, "completed")
                logger.info(f"[Download Worker] Hoàn thành tải {key}")
            else:
                err_msg = stderr.decode('utf-8', errors='ignore')[-200:] if stderr else "Unknown error"
                await update_download_status(movie_slug, ep_name, "failed", err_msg)
                logger.error(f"[Download Worker] Lỗi tải {key}: {err_msg}")
                _remove_quietly(part_path)

        except Exception as e:
            logger.error(f"[Download Worker] Lỗi hệ thống trong worker: {e}")
        finally:
            download_queue.task_done()


def _cleanup_disk():
    now = time.time()
    max_age = setting("cache.max_age_hours") * 3600  # hours -> seconds
    max_size = setting("cache.max_gb") * 1024 ** 3   # GB -> byte
    total_size = 0
    cleaned_sessions = 0
    all_files = []

    for item in os.listdir(CACHE_DIR):
        item_path = os.path.join(CACHE_DIR, item)
        if os.path.isdir(item_path):
            stats = os.stat(item_path)
            if now - stats.st_mtime > max_age:
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
                        elif now - fstats.st_mtime > 3600:
                            _remove_quietly(fpath)
        else:
            _remove_quietly(item_path)

    if cleaned_sessions > 0:
        logger.info(f"Đã xóa {cleaned_sessions} phiên làm việc (session) hết hạn.")

    if total_size > max_size:
        all_files.sort(key=lambda x: x[1])
        removed_size = 0
        for file_path, _, size in all_files:
            if total_size - removed_size <= max_size:
                break
            try:
                os.remove(file_path)
                removed_size += size
                logger.info(f"Đã xóa file lẻ cũ để giảm dung lượng: {file_path}")
            except Exception:
                pass

async def prune_cache():
    """Automatically clean up the cache without blocking the event loop"""
    while True:
        try:
            await asyncio.to_thread(_cleanup_disk)
        except Exception as e:
            logger.error(f"Lỗi dọn dẹp cache: {e}")
        try:
            removed = await asyncio.to_thread(_db_prune_inactive_devices, time.time())
            if removed:
                logger.info(f"[Devices] Đã xóa {removed} thiết bị không hoạt động")
        except Exception as e:
            logger.error(f"Lỗi dọn thiết bị không hoạt động: {e}")

        await asyncio.sleep(3600)  # Run once an hour

def _segment_paths(sid: str, url: str) -> tuple[str, str, str]:
    """(cache_path, part_path, lock_id) of a segment in session sid; creates the session directory."""
    session_dir = os.path.join(CACHE_DIR, sid)
    os.makedirs(session_dir, exist_ok=True)
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cache_path = os.path.join(session_dir, f"{url_hash}.ts")
    return cache_path, cache_path + ".part", f"{sid}_{url_hash}"

def _segment_media_type(size: int) -> str:
    # A 16-byte "segment" is an AES-128 key, not MPEG-TS
    return "application/octet-stream" if size == 16 else "video/MP2T"

async def _prefetch_one(target_url: str, sid: str):
    cache_path, part_path, lock_id = _segment_paths(sid, target_url)

    if os.path.exists(cache_path) or lock_id in download_locks:
        return

    async with get_prefetch_semaphore():
        # Re-check to avoid race condition
        if os.path.exists(cache_path) or lock_id in download_locks:
            return

        event = asyncio.Event()
        download_locks[lock_id] = event
        try:
            logger.info(f"[Prefetch] Tải: {target_url}")
            async with client.stream("GET", target_url, headers=UPSTREAM_HEADERS) as resp:
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
            _remove_quietly(part_path)
        finally:
            event.set()
            download_locks.pop(lock_id, None)

async def prefetch_episode(segment_urls: list[str], key_url: str | None, sid: str):
    if key_url:
        logger.info(f"[Prefetch] Bắt đầu tải Key giải mã trước: {key_url}")
        await _prefetch_one(key_url, sid)

    if segment_urls:
        logger.info(f"[Prefetch] Bắt đầu tải {len(segment_urls)} segment của tập phim...")
        tasks = [asyncio.create_task(_prefetch_one(url, sid)) for url in segment_urls]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[Prefetch] Hoàn thành toàn bộ prefetch cho tập phim: {sid}")

# HTTP client (initialized in lifespan; None until the app starts)
client: httpx.AsyncClient | None = None
# Separate client for HomeTV streams (no auto-redirects, ignores proxy env vars); created lazily
hometv_play_client: httpx.AsyncClient | None = None


def get_hometv_play_client() -> httpx.AsyncClient:
    global hometv_play_client
    if hometv_play_client is None:
        hometv_play_client = hometv.fetcher.make_client()
    return hometv_play_client


# --- Lifespan handler (replaces the deprecated @app.on_event) ---
# Manages startup + shutdown in a single context manager.
# Background tasks are tracked so they can be cancelled cleanly on shutdown.
_bg_tasks: list[asyncio.Task] = []

def _warn_if_admin_disabled() -> None:
    if not _admin_configured():
        logger.warning("HOMEFLIX_ADMIN_PASSWORD chưa đặt: tính năng admin đang tắt")


def _init_hometv() -> bool:
    # A broken HomeTV must not stop HomeFlix: just log the error
    try:
        hometv.init(os.path.join(DATA_DIR, "hometv.db"))
        return True
    except Exception:
        logger.exception("HomeTV: không mở được hometv.db, tắt HomeTV")
        return False


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, workers. Shutdown: close httpx client, cancel tasks."""
    global client
    # Initialize the SQLite database
    init_db()
    ensure_admin_password()   # first start: generate and log an admin password
    # Migrate downloads.json data if present
    migrate_downloads_json_to_db()
    hometv_ok = _init_hometv()
    # Reset downloads left stuck by a server restart
    await asyncio.to_thread(_db_reset_stuck_downloads)
    # Initialize the HTTP client (in lifespan so it closes cleanly on shutdown)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0, write=5.0, pool=10.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    # Start background workers
    _bg_tasks.append(asyncio.create_task(prune_cache()))
    _bg_tasks.append(asyncio.create_task(download_worker()))
    _bg_tasks.append(asyncio.create_task(delayed_cleanup_worker()))
    _bg_tasks.append(asyncio.create_task(home_cache_warmer()))
    if hometv_ok:  # HomeTV only runs when hometv.db opened successfully
        _bg_tasks.append(asyncio.create_task(hometv.scheduler.hometv_refresher()))
        _bg_tasks.append(asyncio.create_task(hometv.health_scheduler.hometv_health_checker()))
    _warn_if_admin_disabled()
    logger.info("[Startup] HomeFlix ready — DB, workers, httpx client initialized")
    yield
    # Shutdown: cancel background tasks + close the httpx client
    for t in _bg_tasks:
        t.cancel()
    await asyncio.gather(*_bg_tasks, return_exceptions=True)
    if client is not None:
        await client.aclose()
    if hometv_play_client is not None:
        await hometv_play_client.aclose()
    logger.info("[Shutdown] HomeFlix stopped — tasks cancelled, httpx client closed")

app = FastAPI(title="HomeFlix Proxy Player", lifespan=lifespan)

# --- Versioned static files ---
# Pages append ?v=<content hash> to JS/CSS URLs (asset_url). URLs with v change with content, so
# they are cached immutable for 7 days; URLs without v (logo, manifest) and sw.js always revalidate
# (ETag), so a new deploy is not shadowed by stale files in the browser.
STATIC_DIR = os.path.join(BASE_DIR, "static")
_asset_versions = {}


def asset_url(name: str) -> str:
    path = os.path.join(STATIC_DIR, name)
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    version = _asset_versions.get(key)
    if version is None:
        with open(path, "rb") as f:
            version = hashlib.md5(f.read()).hexdigest()[:10]
        _asset_versions[key] = version
    return f"/static/{name}?v={version}"


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            versioned = b"v=" in scope.get("query_string", b"") and path != "sw.js"
            response.headers["Cache-Control"] = "public, max-age=604800, immutable" if versioned else "no-cache"
        return response


app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")


# Initialize Jinja2 templates with an absolute path
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["asset"] = lambda name: asset_url(name)

# Allow CORS so the Player JS can access streams from anywhere
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # No credentials: if enabled, Starlette reflects any Origin with credentials=true,
    # defeating the CSRF protection of write routes (JSON-only) against same-site subdomains.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Browser cache cho API responses (GET) ---
# The /api/home/*, /api/search and /api/movie/* APIs already have a server-side cache
# (cached_fetch). Add a Cache-Control header so the browser caches briefly too,
# reducing RTT when the user navigates back. Short max-age (60s) + stale-while-
# revalidate keeps data fresh without waiting.
@app.middleware("http")
async def api_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.method == "GET" and response.status_code == 200:
        path = request.url.path
        # Short cache for homepage sections (data changes every 10-30 minutes)
        if path.startswith("/api/home/"):
            response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=600"
        # Shorter cache for search & movie detail (user-specific but stable)
        elif path.startswith("/api/search") or path.startswith("/api/movie/"):
            response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300"
        elif path.startswith("/proxy/image"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response

# Writes to /api/admin and /api/settings require application/json (blocks form CSRF)
@app.middleware("http")
async def json_only_middleware(request: Request, call_next):
    path = request.url.path
    if (request.method in ("POST", "PATCH", "DELETE")
            and path.startswith(("/api/admin", "/api/settings"))):
        media = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media != "application/json":
            return JSONResponse({"detail": "application/json required"}, status_code=415)
    return await call_next(request)

# Issue and read the hf_device cookie for "/" and per-device routes.
@app.middleware("http")
async def device_middleware(request: Request, call_next):
    path = request.url.path
    if not (path == "/" or path.startswith(DEVICE_COOKIE_PATHS)):
        return await call_next(request)
    device_id = _valid_device_cookie(request.cookies.get(DEVICE_COOKIE))
    issue_cookie = device_id is None
    if issue_cookie:
        device_id = str(uuid.uuid4())
    ip = client_ip(request.client.host if request.client else None,
                   request.headers.get("x-forwarded-for"), TRUSTED_PROXIES)
    row = await asyncio.to_thread(_register_request_device, device_id, ip,
                                  request.headers.get("user-agent", ""), path != "/")
    request.state.device_id = device_id
    request.state.household = row["household"]
    request.state.ip = ip
    response = await call_next(request)
    if issue_cookie:
        response.set_cookie(DEVICE_COOKIE, device_id, max_age=DEVICE_COOKIE_MAX_AGE, path="/",
                            httponly=True, samesite="lax", secure=request.url.scheme == "https")
    if path != "/":
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
    return response

# --- Admin: password login, logout, revoking other devices ---
def require_admin_enabled():
    if not _admin_configured():
        raise HTTPException(status_code=404)

def require_admin(request: Request):
    require_admin_enabled()
    if not _db_is_admin(request.state.device_id):
        raise HTTPException(status_code=403, detail="admin required")

# HomeTV admin routes (the router attaches require_admin to every route)
app.include_router(hometv.api.make_router(require_admin))
app.include_router(hometv.health_api.make_health_router(require_admin))
# Public HomeTV routes: channels, favorites, streams
app.include_router(hometv.public_api.make_public_router(lambda: get_hometv_play_client()))

# --- System settings ---
def _settings_payload(request: Request) -> dict:
    enabled = _admin_configured()
    is_admin = enabled and _db_is_admin(request.state.device_id)
    payload = {"admin_enabled": enabled, "is_admin": is_admin,
               "groups": settings_store.describe() if is_admin else []}
    if is_admin:
        payload["password_env"] = admin.env_managed()   # the UI hides "Đổi mật khẩu" when the env var sets it
    return payload

@app.get("/api/settings")
async def get_settings_api(request: Request):
    return await asyncio.to_thread(_settings_payload, request)

@app.patch("/api/settings", dependencies=[Depends(require_admin)])
async def patch_settings_api(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"errors": {"_": "JSON không hợp lệ"}}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"errors": {"_": "cần một đối tượng JSON"}}, status_code=422)
    try:
        await asyncio.to_thread(settings_store.update, body)
    except settings.SettingsError as e:
        return JSONResponse({"errors": e.errors}, status_code=422)
    return await asyncio.to_thread(_settings_payload, request)

# --- Admin: device management (public ids only, never expose the real id) ---
def _admin_device_view(row, salt: str, self_id: str) -> dict:
    return {"id": _cast_public_id(row["id"], salt), "name": row["name"], "kind": row["kind"],
            "household": row["household"][:8], "last_seen": row["last_seen"],
            "is_admin": bool(row["is_admin"]), "is_self": row["id"] == self_id}

def _resolve_public_device(public_id: str) -> dict | None:
    """Scan all devices, comparing public ids with compare_digest. None if not found."""
    salt = get_household_salt()
    for row in db_query("SELECT * FROM devices"):
        if hmac.compare_digest(_cast_public_id(row["id"], salt), str(public_id)):
            return dict(row)
    return None

def _db_admin_devices(self_id: str) -> list[dict]:
    salt = get_household_salt()
    out = [_admin_device_view(r, salt, self_id) for r in db_query("SELECT * FROM devices WHERE is_admin = 1")]
    out.sort(key=lambda d: d["last_seen"], reverse=True)
    out.sort(key=lambda d: d["household"])
    return out

def _db_admin_patch_device(public_id: str, name, is_admin, self_id: str) -> dict | None:
    """Edit name / admin rights. Counting admins and writing share one lock, so admins can never reach 0; revoking the last admin -> 409."""
    target = _resolve_public_device(public_id)  # resolve the real id before taking the lock (the lock is not reentrant)
    if target is None:
        return None
    salt = get_household_salt()
    conn = get_db()
    with _db_lock:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (target["id"],)).fetchone()
        if row is None:
            return None
        if is_admin is False and row["is_admin"]:
            if conn.execute("SELECT COUNT(*) FROM devices WHERE is_admin = 1").fetchone()[0] <= 1:
                raise HTTPException(status_code=409, detail="cannot revoke the last admin")
        if name is not None:
            conn.execute("UPDATE devices SET name = ? WHERE id = ?", (name, row["id"]))
        if is_admin is not None:
            conn.execute("UPDATE devices SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, row["id"]))
        conn.commit()
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (row["id"],)).fetchone()
    return _admin_device_view(row, salt, self_id)

def _db_admin_delete_device(public_id: str, self_id: str) -> bool:
    target = _resolve_public_device(public_id)
    if target is None:
        return False
    if target["id"] == self_id:
        raise HTTPException(status_code=409, detail="cannot delete your own device")
    conn = get_db()
    with _db_lock:
        _db_delete_device(conn, target["id"])
        conn.commit()
    return True

@app.get("/api/admin/devices", dependencies=[Depends(require_admin)])
async def admin_devices_api(request: Request):
    return await asyncio.to_thread(_db_admin_devices, request.state.device_id)

@app.patch("/api/admin/devices/{public_id}", dependencies=[Depends(require_admin)])
async def admin_patch_device_api(request: Request, public_id: str, body: dict):
    name = None
    if "name" in body:
        name = body["name"].strip() if isinstance(body["name"], str) else ""
        if not 1 <= len(name) <= 40:
            raise HTTPException(status_code=422, detail="name must be 1-40 characters")
    is_admin = body.get("is_admin")
    if "is_admin" in body and not isinstance(is_admin, bool):
        raise HTTPException(status_code=422, detail="is_admin must be a boolean")
    view = await asyncio.to_thread(_db_admin_patch_device, public_id, name, is_admin, request.state.device_id)
    if view is None:
        raise HTTPException(status_code=404)
    return view

@app.delete("/api/admin/devices/{public_id}", dependencies=[Depends(require_admin)])
async def admin_delete_device_api(request: Request, public_id: str):
    if not await asyncio.to_thread(_db_admin_delete_device, public_id, request.state.device_id):
        raise HTTPException(status_code=404)
    return Response(status_code=204)

def _lockout_ip_key(ip: str) -> str:
    """Lockout per IP: IPv6 is grouped to /64 so rotating addresses within a range does not help."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return f"ip:{ip}"
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            return f"ip:{addr.ipv4_mapped}"
        return f"ip:{ipaddress.ip_network(f'{addr}/64', strict=False).network_address}/64"
    return f"ip:{addr}"

@app.post("/api/admin/login", dependencies=[Depends(require_admin_enabled)])
async def admin_login_api(request: Request):
    # Read the body BEFORE checking the lockout; there is no await between retry_after and record_failure,
    # so many slow-body connections cannot slip through at once
    try:
        body = await request.json()
    except ValueError:
        body = None
    candidate = body.get("password") if isinstance(body, dict) else None
    keys = [f"d:{request.state.device_id}", _lockout_ip_key(request.state.ip)]
    wait = admin_login_limiter.retry_after(keys)  # check the lockout before comparing the password
    if wait > 0:
        return JSONResponse({"retry_after": wait}, status_code=429, headers={"Retry-After": str(wait)})
    # scrypt runs inline on purpose: an await here would let parallel guesses pass retry_after together
    if not admin.password_ok(candidate, _db_admin_hash()):
        admin_login_limiter.record_failure(keys)
        raise HTTPException(status_code=401, detail="wrong password")
    admin_login_limiter.reset(keys)
    await asyncio.to_thread(_db_set_admin, request.state.device_id, True)
    return Response(status_code=204)

@app.post("/api/admin/password", dependencies=[Depends(require_admin)])
async def admin_change_password_api(request: Request):
    """Change the stored admin password; needs the current one (wrong guesses count toward the login lockout)."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return JSONResponse({"errors": {"_": "cần một đối tượng JSON"}}, status_code=422)
    if admin.env_managed():
        raise HTTPException(status_code=409, detail="password is set by HOMEFLIX_ADMIN_PASSWORD")
    keys = [f"d:{request.state.device_id}", _lockout_ip_key(request.state.ip)]
    wait = admin_login_limiter.retry_after(keys)
    if wait > 0:
        return JSONResponse({"retry_after": wait}, status_code=429, headers={"Retry-After": str(wait)})
    error = admin.new_password_error(body.get("new"))
    if error:
        return JSONResponse({"errors": {"new": error}}, status_code=422)
    # inline scrypt, same reason as login: no await between retry_after and record_failure
    if not admin.password_ok(body.get("current"), _db_admin_hash()):
        admin_login_limiter.record_failure(keys)
        return JSONResponse({"errors": {"current": "Sai mật khẩu hiện tại"}}, status_code=401)
    admin_login_limiter.reset(keys)
    await asyncio.to_thread(_db_set_admin_hash, admin.hash_password(body["new"]))
    return Response(status_code=204)

@app.post("/api/admin/logout", dependencies=[Depends(require_admin)])
async def admin_logout_api(request: Request):
    await asyncio.to_thread(_db_set_admin, request.state.device_id, False)
    return Response(status_code=204)

@app.post("/api/admin/revoke-others", dependencies=[Depends(require_admin)])
async def admin_revoke_others_api(request: Request):
    cur = await asyncio.to_thread(
        db_execute, "UPDATE devices SET is_admin = 0 WHERE is_admin = 1 AND id != ?", (request.state.device_id,))
    return {"revoked": cur.rowcount}

@app.post("/api/admin/requests", dependencies=[Depends(require_admin_enabled)])
async def admin_request_create_api(request: Request):
    req = await asyncio.to_thread(_db_create_admin_request, request.state.device_id, _now())
    if req is None:
        raise HTTPException(status_code=429, detail="too many pending requests")
    return {"code": req["code"], "expires_at": req["expires_at"]}

@app.get("/api/admin/requests/mine", dependencies=[Depends(require_admin_enabled)])
async def admin_request_mine_api(request: Request):
    return await asyncio.to_thread(_db_my_admin_request, request.state.device_id, _now())

@app.get("/api/admin/requests", dependencies=[Depends(require_admin)])
async def admin_requests_list_api():
    return await asyncio.to_thread(_db_pending_admin_requests, _now())

async def _admin_decide(request_id: str, approve: bool):
    if not await asyncio.to_thread(_db_decide_admin_request, request_id, approve, _now()):
        raise HTTPException(status_code=409, detail="request not pending")
    return Response(status_code=204)

@app.post("/api/admin/requests/{request_id}/approve", dependencies=[Depends(require_admin)])
async def admin_request_approve_api(request_id: str):
    return await _admin_decide(request_id, True)

@app.post("/api/admin/requests/{request_id}/deny", dependencies=[Depends(require_admin)])
async def admin_request_deny_api(request_id: str):
    return await _admin_decide(request_id, False)

@app.get("/api/device")
async def get_device_api(request: Request):
    row = await asyncio.to_thread(
        lambda: dict(db_query_one("SELECT * FROM devices WHERE id = ?", (request.state.device_id,))))
    return _public_device(row)

@app.patch("/api/device")
async def patch_device_api(request: Request, body: dict):
    updates = {}
    if "name" in body:
        name = body["name"].strip() if isinstance(body["name"], str) else ""
        if not 1 <= len(name) <= 40:
            raise HTTPException(status_code=422, detail="name must be 1-40 characters")
        updates["name"] = name
    if "kind" in body:
        if body["kind"] not in DEVICE_KINDS:
            raise HTTPException(status_code=422, detail="kind must be one of " + ", ".join(DEVICE_KINDS))
        updates["kind"] = body["kind"]
    if updates:
        sets = ", ".join(f"{col} = ?" for col in updates)
        await asyncio.to_thread(db_execute, f"UPDATE devices SET {sets} WHERE id = ?",
                                (*updates.values(), request.state.device_id))
    return await get_device_api(request)

# --- Cast: presence and recent devices ---
# The device id is the login cookie, so NEVER send it to another device. All cast payloads and routes
# use the public id = HMAC(salt, device id); the server only resolves it back within the same home.
def _cast_public_id(device_id: str, salt: str | None = None) -> str:
    key = (salt or get_household_salt()).encode()
    return hmac.new(key, b"cast:" + device_id.encode(), hashlib.sha256).hexdigest()[:20]

async def _cast_resolve(request: Request, public_id: str, kinds: tuple | None = None) -> dict | None:
    """The same-home devices row whose public id matches (and of the right kind if `kinds` is given), or None."""
    rows = await asyncio.to_thread(db_query, "SELECT * FROM devices WHERE household = ?", (request.state.household,))
    salt = await asyncio.to_thread(get_household_salt)
    for row in rows:
        if hmac.compare_digest(_cast_public_id(row["id"], salt), str(public_id)):
            return dict(row) if kinds is None or row["kind"] in kinds else None
    return None

def _sweep_stale_controllers(tv_id: str) -> None:
    """When a controller stops heartbeating (Safari closed, network lost), unlink it and tell the TV to drop the label."""
    salt = get_household_salt()
    for controller_id in cast_hub.controllers_of(tv_id):
        if not cast_hub.is_online(controller_id):
            cast_hub.unlink(controller_id)
            cast_hub.publish(tv_id, "controller_left", {"controller_id": _cast_public_id(controller_id, salt)})

@app.post("/api/devices/heartbeat")
async def devices_heartbeat_api(request: Request, body: dict | None = None):
    device_id = request.state.device_id
    body = body or {}
    kind = body.get("kind")
    if kind is not None:
        if kind not in DEVICE_KINDS:
            raise HTTPException(status_code=422, detail="kind must be one of " + ", ".join(DEVICE_KINDS))
        await asyncio.to_thread(db_execute, "UPDATE devices SET kind = ? WHERE id = ?", (kind, device_id))
    else:
        row = await asyncio.to_thread(db_query_one, "SELECT kind FROM devices WHERE id = ?", (device_id,))
        kind = row["kind"] if row else "desktop"
    cast_hub.heartbeat(device_id, kind)
    if kind in ("tv", "desktop"):
        await asyncio.to_thread(_sweep_stale_controllers, device_id)
    return {"status": "ok"}

@app.get("/api/devices/nearby")
async def devices_nearby_api(request: Request):
    rows = await asyncio.to_thread(
        db_query,
        "SELECT id, name, kind FROM devices WHERE household = ? AND id != ? AND kind IN ('tv', 'desktop')",
        (request.state.household, request.state.device_id))
    salt = await asyncio.to_thread(get_household_salt)
    return [{"id": _cast_public_id(r["id"], salt), "name": r["name"], "kind": r["kind"], "online": True}
            for r in rows if cast_hub.is_online(r["id"])]

@app.get("/api/cast/settings")
async def cast_settings_api(request: Request):
    return {"allow_control": await asyncio.to_thread(_db_cast_allow_control, request.state.device_id)}

@app.patch("/api/cast/settings")
async def patch_cast_settings_api(request: Request, body: dict):
    value = body.get("allow_control")
    if not isinstance(value, bool):
        raise HTTPException(status_code=422, detail="allow_control must be boolean")
    device_id = request.state.device_id
    await asyncio.to_thread(_db_cast_set_allow_control, device_id, value)
    if not value:
        salt = await asyncio.to_thread(get_household_salt)
        tv_public = _cast_public_id(device_id, salt)
        for controller_id in cast_hub.controllers_of(device_id):
            cast_hub.unlink(controller_id)
            cast_hub.publish(device_id, "controller_left", {"controller_id": _cast_public_id(controller_id, salt)})
            cast_hub.publish(controller_id, "unlinked", {"target_id": tv_public})
        cast_hub.clear_pending(device_id)   # pending requests must not be approved after control is turned off
    return {"allow_control": value}

CAST_PING_SECONDS = 15

def _db_device_names(ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["id"]: r["name"] for r in db_query(f"SELECT id, name FROM devices WHERE id IN ({marks})", tuple(ids))}

@app.get("/api/cast/events")
async def cast_events_api(request: Request):
    """SSE stream for a device: commands, connection requests, state. One ': ping' line every CAST_PING_SECONDS."""
    device_id = request.state.device_id

    async def stream():
        # Register inside the generator: if the client disconnects before the stream starts, there is nothing to clean up
        sub = cast_hub.subscribe(device_id)
        try:
            salt = await asyncio.to_thread(get_household_salt)
            controllers = cast_hub.controllers_of(device_id)
            pending = cast_hub.pending_for(device_id)
            names = await asyncio.to_thread(_db_device_names, controllers + pending)
            yield format_sse("hello", {
                "device_id": _cast_public_id(device_id, salt),
                "controllers": [{"id": _cast_public_id(i, salt), "name": names.get(i, "")} for i in controllers],
                "pending": [{"controller_id": _cast_public_id(i, salt), "controller_name": names.get(i, "")} for i in pending],
            })
            while not sub.closed:
                events = await sub.next(CAST_PING_SECONDS)
                if events:
                    for name, data in events:
                        yield format_sse(name, data)
                elif not sub.closed:
                    yield ": ping\n\n"
        finally:
            cast_hub.unsubscribe(sub)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "private, no-store"})

async def _cast_target(request: Request, public_id: str) -> dict:
    """The target TV's devices row. 403 if missing, in another home, not tv/desktop, or the caller itself
    (cases are not distinguished, so ids cannot be probed)."""
    row = await _cast_resolve(request, public_id, kinds=("tv", "desktop"))
    if row is None or row["id"] == request.state.device_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return row

async def _device_name(device_id: str) -> str:
    row = await asyncio.to_thread(db_query_one, "SELECT name FROM devices WHERE id = ?", (device_id,))
    return row["name"] if row else ""

@app.post("/api/cast/{target_id}/connect")
async def cast_connect_api(request: Request, target_id: str):
    target = await _cast_target(request, target_id)
    tv_id = target["id"]
    if not await asyncio.to_thread(_db_cast_allow_control, tv_id):
        raise HTTPException(status_code=403, detail="target does not accept control")
    controller_id = request.state.device_id
    controller_public = await asyncio.to_thread(_cast_public_id, controller_id)
    name = await _device_name(controller_id)
    if await asyncio.to_thread(_db_cast_is_allowed, tv_id, controller_id):
        cast_hub.link(controller_id, tv_id)
        cast_hub.publish(tv_id, "controller_joined", {"controller_id": controller_public, "name": name})
        return {"status": "connected"}
    if not cast_hub.has_subscribers(tv_id):
        raise HTTPException(status_code=409, detail="target offline")
    if cast_hub.add_pending(tv_id, controller_id):
        cast_hub.publish(tv_id, "connect_request", {"controller_id": controller_public, "controller_name": name},
                         newest_only=True)
    return JSONResponse({"status": "pending"}, status_code=202)

@app.post("/api/cast/requests/{controller_id}")
async def cast_answer_request_api(request: Request, controller_id: str, body: dict):
    """The TV answers a controller's connection request (public id in the path)."""
    tv_id = request.state.device_id
    allow = body.get("allow")
    if not isinstance(allow, bool):
        raise HTTPException(status_code=422, detail="allow must be boolean")
    controller = await _cast_resolve(request, controller_id)
    if controller is None:
        raise HTTPException(status_code=403, detail="forbidden")
    real_controller = controller["id"]
    if not cast_hub.pop_pending(tv_id, real_controller):
        raise HTTPException(status_code=403, detail="no pending request")
    salt = await asyncio.to_thread(get_household_salt)
    if allow:
        await asyncio.to_thread(_db_cast_allow, tv_id, real_controller)
        cast_hub.link(real_controller, tv_id)
        cast_hub.publish(tv_id, "controller_joined", {"controller_id": controller_id, "name": controller["name"]})
    cast_hub.publish(real_controller, "connect_result", {"target_id": _cast_public_id(tv_id, salt), "allowed": allow})
    return {"status": "ok"}

@app.delete("/api/cast/link")
async def cast_unlink_api(request: Request):
    controller_id = request.state.device_id
    target_id = cast_hub.unlink(controller_id)
    if target_id:
        cast_hub.publish(target_id, "controller_left",
                         {"controller_id": await asyncio.to_thread(_cast_public_id, controller_id)})
    return {"status": "ok"}

async def _cast_authorize(request: Request, public_id: str) -> dict:
    """The controller may control the target: same home, the TV's toggle is on, and the TV has allowed this controller."""
    target = await _cast_target(request, public_id)
    if not await asyncio.to_thread(_db_cast_allow_control, target["id"]):
        raise HTTPException(status_code=403, detail="target does not accept control")
    if not await asyncio.to_thread(_db_cast_is_allowed, target["id"], request.state.device_id):
        raise HTTPException(status_code=403, detail="controller not allowed")
    return target

@app.post("/api/cast/{target_id}/command")
async def cast_command_api(request: Request, target_id: str, body: dict):
    target = await _cast_authorize(request, target_id)
    tv_id = target["id"]
    try:
        command = validate_command(body, _valid_slug)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not cast_hub.has_subscribers(tv_id):
        raise HTTPException(status_code=409, detail="target offline")
    controller_id = request.state.device_id
    if cast_hub.linked_target(controller_id) != tv_id:
        # The in-memory link was lost (server restarted): re-establish it and notify the TV so it reports its state
        cast_hub.link(controller_id, tv_id)
        cast_hub.publish(tv_id, "controller_joined", {
            "controller_id": await asyncio.to_thread(_cast_public_id, controller_id),
            "name": await _device_name(controller_id)})
    cast_hub.publish(tv_id, "command", command, newest_only=True)
    return {"status": "sent"}

@app.post("/api/cast/state")
async def cast_report_state_api(request: Request, body: dict):
    """The TV reports playback state; forward it to linked controllers."""
    tv_id = request.state.device_id
    row = await asyncio.to_thread(db_query_one, "SELECT kind FROM devices WHERE id = ?", (tv_id,))
    if row is None or row["kind"] not in ("tv", "desktop"):
        raise HTTPException(status_code=403, detail="only a TV reports state")
    try:
        state = validate_state(body, _valid_slug)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await asyncio.to_thread(_sweep_stale_controllers, tv_id)
    state["target_id"] = await asyncio.to_thread(_cast_public_id, tv_id)
    cast_hub.set_state(tv_id, state)
    for controller_id in cast_hub.controllers_of(tv_id):
        cast_hub.publish(controller_id, "state", state)
    return {"status": "ok"}

@app.get("/api/cast/state/{target_id}")
async def cast_get_state_api(request: Request, target_id: str):
    target = await _cast_authorize(request, target_id)
    return cast_hub.get_state(target["id"]) or {}

# GZip-compress HTTP responses (JSON, HTML), cutting bandwidth ~70% for text responses
app.add_middleware(GZipMiddleware, minimum_size=1024)

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
_IMAGE_TYPES = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg"}

@app.get("/proxy/image")
async def proxy_image(url: str):
    """Proxy and disk-cache movie posters/images to speed up loading on the client & PWA"""
    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid image URL")
    
    url_hash = hashlib.md5(url.encode()).hexdigest()
    lowered = url.lower()
    ext = ".png" if ".png" in lowered else ".webp" if ".webp" in lowered else ".jpg"
    file_path = os.path.join(POSTER_CACHE_DIR, f"{url_hash}{ext}")

    if os.path.exists(file_path):
        return FileResponse(file_path, media_type=_IMAGE_TYPES[ext],
                            headers={"X-Cache": "HIT", "Cache-Control": IMMUTABLE_CACHE})

    try:
        resp = await client.get(url, headers=UPSTREAM_HEADERS, follow_redirects=True, timeout=15.0)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Unable to fetch remote image")

        content = resp.content
        part_path = f"{file_path}.part"
        async with aiofiles.open(part_path, "wb") as f:
            await f.write(content)
        os.replace(part_path, file_path)

        content_type = resp.headers.get("content-type", "image/jpeg")
        return Response(content=content, media_type=content_type,
                        headers={"X-Cache": "MISS", "Cache-Control": IMMUTABLE_CACHE})
    except Exception as e:
        logger.warning(f"[ImageProxy] Lỗi tải ảnh {url}: {e}")
        # Redirect to the original url on failure
        return Response(status_code=307, headers={"Location": url})

def make_proxy_url(request: Request, path: str, target_url: str, sid: str = None) -> str:
    """Build a URL that goes through our proxy"""
    encoded_target = urllib.parse.quote(target_url, safe="")
    res = f"{path}?url={encoded_target}"
    if sid:
        res += f"&sid={sid}"
    return res

@app.get("/")
async def root(request: Request):
    """Home page - Player UI"""
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/proxy/m3u8")
async def proxy_m3u8(request: Request, url: str, sid: str = None):
    """Proxy that parses m3u8 and rewrites the URLs inside"""
    if not sid:
        sid = hashlib.md5(url.encode()).hexdigest()

    try:
        response = await client.get(url, headers=UPSTREAM_HEADERS, follow_redirects=True)
        response.raise_for_status()

        # Parse the M3U8 content with the m3u8 library
        playlist = m3u8.loads(response.text, uri=url)

        # Branch 1: MASTER playlist — variants, I-frame playlists and renditions all go back through /proxy/m3u8
        if playlist.is_variant:
            for item in (*playlist.playlists, *playlist.iframe_playlists, *playlist.media):
                if item.uri:
                    item.uri = make_proxy_url(request, "/proxy/m3u8", item.absolute_uri, sid)

        # Branch 2: MEDIA playlist
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
            
            # Start prefetching the whole episode in the background
            asyncio.create_task(prefetch_episode(original_segment_urls, original_key_url, sid))
                    
        return PlainTextResponse(
            playlist.dumps(), 
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache"}
        )
        
    except httpx.HTTPStatusError as e:
        # The source returned an error (usually the CDN blocking non-Vietnam IPs): return 502 with a reason so the client plays directly
        status = e.response.status_code
        headers = {"X-Upstream-Status": str(status), "Cache-Control": "no-store"}
        if "mạng trong nước" in e.response.text:
            headers["X-Upstream-Block"] = "geo"
        logger.error(f"Upstream {status} for proxy m3u8 '{url}' (block={headers.get('X-Upstream-Block', '-')})")
        return PlainTextResponse(f"Nguồn phim trả lỗi {status}", status_code=502, headers=headers)
    except httpx.HTTPError as e:
        logger.error(f"Cannot reach upstream for proxy m3u8 '{url}': {e}")
        return PlainTextResponse("Không kết nối được nguồn phim", status_code=502, headers={"Cache-Control": "no-store"})
    except Exception as e:
        logger.error(f"Error fetching proxy m3u8 '{url}': {e}")
        return PlainTextResponse(f"Proxy Error: {str(e)}", status_code=500)

async def fetch_and_cache_full(url: str, cache_path: str, part_path: str, event: asyncio.Event) -> bytes:
    """Download a segment to part_path then rename it to cache_path; event is always set when done.
    Any failure removes the part file and surfaces as HTTP 502."""
    try:
        async with client.stream("GET", url, headers=UPSTREAM_HEADERS) as resp:
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Origin trả status code {resp.status_code} cho segment: {url}"
                )
            chunks = []
            async with aiofiles.open(part_path, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    await f.write(chunk)
                    chunks.append(chunk)
        os.replace(part_path, cache_path)
        return b"".join(chunks)
    except (HTTPException, asyncio.CancelledError):
        _remove_quietly(part_path)
        raise
    except Exception as e:
        logger.error(f"Lỗi tải segment: {e}")
        _remove_quietly(part_path)
        raise HTTPException(status_code=502, detail=f"Lỗi tải segment từ nguồn: {str(e)}")
    finally:
        event.set()

def _cached_segment_response(cache_path: str, cache_tag: str) -> FileResponse:
    return FileResponse(cache_path, media_type=_segment_media_type(os.path.getsize(cache_path)),
                        headers={"X-Cache": cache_tag})

@app.get("/proxy/ts")
async def proxy_ts(request: Request, url: str, sid: str = "default"):
    """Proxy and cache .ts with Pass-through Stream and per-Session Part-files"""
    cache_path, part_path, lock_id = _segment_paths(sid, url)

    # 1. Check the on-disk cache
    if os.path.exists(cache_path):
        return _cached_segment_response(cache_path, "HIT")

    # 2. Single Flight: check whether someone is already fetching this segment
    if lock_id in download_locks:
        await download_locks[lock_id].wait()
        if os.path.exists(cache_path):
            return _cached_segment_response(cache_path, "HIT-QUEUED")

    # 3. Create a Lock and download
    event = asyncio.Event()
    download_locks[lock_id] = event
    try:
        data = await fetch_and_cache_full(url, cache_path, part_path, event)
    finally:
        download_locks.pop(lock_id, None)

    return Response(
        content=data,
        media_type=_segment_media_type(len(data)),
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
    """Get current cache size info (without blocking the Event Loop)"""
    try:
        total_size = await asyncio.to_thread(_calculate_cache_size)
        max_gb = setting("cache.max_gb")
        percent = (total_size / (max_gb * 1024 ** 3)) * 100
        total_gb = total_size / (1024 * 1024 * 1024)
        
        return {
            "size_gb": round(total_gb, 2),
            "percent": round(percent, 1),
            "max_gb": max_gb
        }
    except Exception as e:
        return {"error": str(e)}

def _clear_all_cache():
    for item in os.listdir(CACHE_DIR):
        item_path = os.path.join(CACHE_DIR, item)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path, ignore_errors=True)
        else:
            _remove_quietly(item_path)

@app.post("/api/admin/cache/clear", dependencies=[Depends(require_admin)])
async def clear_cache_endpoint():
    """Clear the cache immediately (without blocking the Event Loop)"""
    try:
        await asyncio.to_thread(_clear_all_cache)
        return {"status": "success", "message": "Đã dọn sạch cache."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/search")
async def search_movies(q: str):
    """Proxy movie search to PhimAPI (cached 30 minutes, stale-fallback)"""
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

def _names(value) -> list[str]:
    """Display names from a phimapi [{"name", "slug"}] list; unexpected data yields []."""
    if not isinstance(value, list):
        return []
    return [i["name"] for i in value if isinstance(i, dict) and isinstance(i.get("name"), str) and i["name"]]

@app.get("/api/movie/{slug}")
async def get_movie_detail(slug: str):
    """Proxy and normalize movie details for client performance (cached 60 minutes, stale-fallback)"""
    async def _fetch():
        url = f"https://phimapi.com/phim/{slug}"
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("status"):
            return data
            
        movie_raw = data.get("movie", {})
        # Keep only the needed movie fields
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
            "content": movie_raw.get("content"),
            "category": _names(movie_raw.get("category")),
            "country": _names(movie_raw.get("country"))
        }
        
        # Trim the episode list (only the first server that has data)
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
async def get_saved_movies_api(request: Request):
    return await get_device_movies(request.state.device_id)

@app.post("/api/saved")
async def save_movie_api(request: Request, movie: dict):
    slug = movie.get("slug")
    if not slug:
        return {"status": "error", "message": "Missing slug"}
    if not _valid_slug(slug):
        return {"status": "error", "message": "Invalid slug"}
    _clean_client_movie(movie)

    device_id = request.state.device_id
    existing = await get_device_movie(device_id, slug)
    if existing:
        existing.update(movie)
        movie = existing
    else:
        movie.setdefault("last_watched_episode", "")
        movie.setdefault("last_watched_url", "")
        movie.setdefault("episode_states", {})
    movie["saved"] = True

    # Fix total_episodes based on the length of episodes
    if "episodes" in movie and isinstance(movie["episodes"], list):
        movie["total_episodes"] = len(movie["episodes"])

    await save_device_movie(device_id, movie)
    return {"status": "success", "movie": movie}

def _valid_slug(slug) -> bool:
    """Slug must be a single path segment: non-empty, no '.', '..', '/', '\\' or NUL."""
    return (isinstance(slug, str) and 0 < len(slug) <= 200 and slug not in (".", "..")
            and not any(c in slug for c in "/\\\x00"))

def _safe_rmtree_under(base: str, target: str) -> bool:
    """rmtree target only if it really lies under base (after resolving symlinks and '..')."""
    real_base = os.path.realpath(base)
    real_target = os.path.realpath(target)
    if real_target == real_base or not real_target.startswith(real_base + os.sep):
        logger.warning(f"Từ chối xóa ngoài {base}: {target}")
        return False
    if not os.path.exists(real_target):
        return False
    shutil.rmtree(real_target)
    return True

_SCALAR_MOVIE_FIELDS = ("name", "poster_url", "year", "episode_current", "total_episodes",
                        "last_watched_episode", "last_watched_url")

def _clean_client_movie(movie: dict) -> dict:
    """Drop server-managed fields and non-number/string fields before saving."""
    for key in ("position", "duration", "saved"):
        movie.pop(key, None)
    for key in _SCALAR_MOVIE_FIELDS:
        if key in movie and not isinstance(movie[key], (str, int, float, type(None))):
            del movie[key]
    return movie

def _clean_position(position, duration):
    """Normalize (position, duration) in seconds. Returns None if position is invalid.

    position None = client sent no position. Negatives clamp to 0; values above duration clamp to duration.
    A non-finite or <= 0 duration is ignored (None).
    """
    def _number(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        return float(v)

    clean_duration = _number(duration)
    if clean_duration is not None and clean_duration <= 0:
        clean_duration = None
    if position is None:
        return None, clean_duration
    clean_position = _number(position)
    if clean_position is None:
        return None
    clean_position = max(0.0, clean_position)
    if clean_duration is not None:
        clean_position = min(clean_position, clean_duration)
    return clean_position, clean_duration

@app.post("/api/saved/progress")
async def save_movie_progress_api(request: Request, progress: dict):
    slug = progress.get("slug")
    last_ep = progress.get("last_watched_episode")
    last_url = progress.get("last_watched_url")
    if not slug:
        return {"status": "error", "message": "Missing slug"}
    if not _valid_slug(slug):
        return {"status": "error", "message": "Invalid slug"}
    if any(v is not None and not isinstance(v, str) for v in (last_ep, last_url)):
        return {"status": "error", "message": "Invalid episode"}

    cleaned = _clean_position(progress.get("position"), progress.get("duration"))
    if cleaned is None:
        return {"status": "error", "message": "Invalid position"}
    position, duration = cleaned

    device_id = request.state.device_id
    movie = await get_device_movie(device_id, slug)
    if not movie:
        # Unsaved movie: only create a history row when the client sends movie info
        if not progress.get("name"):
            return {"status": "error", "message": "Movie not in saved list"}
        episodes = progress.get("episodes") if isinstance(progress.get("episodes"), list) else []
        movie = {
            "slug": slug, "name": progress["name"], "poster_url": progress.get("poster_url", ""),
            "year": progress.get("year", ""), "episodes": episodes, "total_episodes": len(episodes),
            "episode_states": {}, "last_watched_episode": "", "last_watched_url": "", "saved": False,
        }

    # A late position report for an already-finished episode (e.g. the previous episode's pause event
    # arriving after the next one started): ignore it, do not roll progress back to the old episode.
    if (position is not None and last_url and last_url != movie.get("last_watched_url")
            and movie.get("episode_states", {}).get(last_url) == "watched"):
        return {"status": "success", "movie": movie}

    # Same episode in progress: only update the position, no status change or file-deletion scheduling.
    if not last_url or last_url == movie.get("last_watched_url"):
        if position is not None:
            movie["position"] = position
        if duration is not None:
            movie["duration"] = duration
        await save_device_movie(device_id, movie)
        return {"status": "success", "movie": movie}

    movie["last_watched_episode"] = last_ep
    movie["last_watched_url"] = last_url
    movie["position"] = position if position is not None else 0.0
    if duration is not None:
        movie["duration"] = duration
    movie.setdefault("episode_states", {})

    previously_watching_urls = []
    for ep_url, state in list(movie["episode_states"].items()):
        if state == "watching":
            movie["episode_states"][ep_url] = "watched"
            previously_watching_urls.append(ep_url)
    movie["episode_states"][last_url] = "watching"

    # The episode about to be watched must not be scheduled for deletion (e.g. rewatching a finished one)
    for ep in movie.get("episodes", []):
        if ep.get("link_m3u8") == last_url:
            st = await asyncio.to_thread(_db_get_download_status, slug, ep.get("name"))
            if st["delete_at"] is not None:
                await update_download_status(slug, ep.get("name"), st["status"], delete_at=None)

    # Delete watched episode files after 3 hours, unless another device still holds the movie (shared files)
    if previously_watching_urls and not await asyncio.to_thread(_db_other_devices_hold, device_id, slug):
        for ep in movie.get("episodes", []):
            if ep.get("link_m3u8") in previously_watching_urls:
                file_path = _episode_file(slug, ep.get("name"))
                if os.path.exists(file_path):
                    try:
                        await update_download_status(slug, ep.get("name"), "completed", delete_at=time.time() + 10800)
                        logger.info(f"Đã lên lịch xóa file tập đã xem xong sau 3 giờ: {file_path}")
                    except Exception as e:
                        logger.warning(f"Không thể đặt lịch xóa file {file_path}: {e}")

    await save_device_movie(device_id, movie)
    return {"status": "success", "movie": movie}

async def _forget_device_movie(device_id: str, movie: dict) -> None:
    slug = movie["slug"]
    await delete_device_movie(device_id, slug)
    # Downloaded files, cache and download records are shared: only clean up when
    # no other device still holds this movie.
    if await asyncio.to_thread(_db_other_devices_hold, device_id, slug):
        return

    # 1. Delete the movie's download records from the DB
    await asyncio.to_thread(db_execute, "DELETE FROM episode_downloads WHERE movie_slug = ?", (slug,))
    
    # 2. Delete segment cache directories for each episode
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
                    
    # 3. Delete the movie's download directory
    movie_dir = os.path.join(DOWNLOAD_DIR, slug)
    if os.path.exists(movie_dir):
        try:
            if _safe_rmtree_under(DOWNLOAD_DIR, movie_dir):
                logger.info(f"Đã xóa thư mục tải xuống: {movie_dir}")
        except Exception as e:
            logger.warning(f"Lỗi khi xóa thư mục {movie_dir}: {e}")

@app.delete("/api/saved/{slug}")
async def delete_saved_movie_api(request: Request, slug: str):
    if not _valid_slug(slug):
        return {"status": "error", "message": "Invalid slug"}
    device_id = request.state.device_id
    movie = await get_device_movie(device_id, slug)
    if movie:
        await _forget_device_movie(device_id, movie)
        return {"status": "success"}
    return {"status": "error", "message": "Movie not found"}

def _db_clear_device_progress(device_id: str, slug: str, episode_states: dict) -> None:
    # Write NULL directly: the upsert keeps the old position when position is None (COALESCE)
    db_execute("UPDATE device_movies SET last_watched_episode = '', last_watched_url = '', position_sec = NULL, "
               "duration_sec = NULL, episode_states = ?, updated_at = ? WHERE device_id = ? AND slug = ?",
               (json.dumps(episode_states, ensure_ascii=False), datetime.now().isoformat(), device_id, slug))

@app.delete("/api/saved/{slug}/progress")
async def remove_from_continue_api(request: Request, slug: str):
    """Remove a movie from "Xem tiếp" (Continue Watching). Saved movies only lose progress; history-only movies are deleted."""
    if not _valid_slug(slug):
        return {"status": "error", "message": "Invalid slug"}
    device_id = request.state.device_id
    movie = await get_device_movie(device_id, slug)
    if not movie:
        return {"status": "error", "message": "Movie not found"}
    if not movie["saved"]:
        await _forget_device_movie(device_id, movie)
        return {"status": "success", "movie": None}
    # Clear "watching" on the in-progress episode so a later rewatch is not treated as just-finished and scheduled for file deletion
    states = {u: s for u, s in movie.get("episode_states", {}).items() if s != "watching"}
    await asyncio.to_thread(_db_clear_device_progress, device_id, slug, states)
    return {"status": "success", "movie": await get_device_movie(device_id, slug)}

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
    return {slug: {ep: _effective_status(slug, ep, st) for ep, st in eps.items()}
            for slug, eps in raw_status.items()}

@app.get("/api/download/status/{movie_slug}")
async def get_movie_downloads_status_api(movie_slug: str):
    raw_status = await asyncio.to_thread(_db_get_all_downloads_status, movie_slug)
    return {ep: _effective_status(movie_slug, ep, st) for ep, st in raw_status.items()}

@app.get("/api/download/status/{movie_slug}/{episode_name}")
async def get_episode_download_status_api(movie_slug: str, episode_name: str):
    status = await get_download_status(movie_slug, episode_name)
    return {"status": status}

@app.get("/media/{movie_slug}/{episode_name}.mp4")
async def get_media_file_api(movie_slug: str, episode_name: str):
    logger.info(f"[Media API] Yêu cầu: movie_slug={movie_slug}, episode_name={episode_name}")
    file_path = _episode_file(movie_slug, episode_name)
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
    """Cache key for a list + filters. Empty = no filter."""
    return f"home:{section}:{page}:{category}:{country}:{year}"


async def _fetch_danh_sach(type_slug: str, page: int, category: str = "", country: str = "", year: str = ""):
    """Fetch + normalize a movie list from phimapi with filters (AND)."""
    filters = {"category": category, "country": country, "year": year}
    resp = await client.get(f"https://phimapi.com/v1/api/danh-sach/{type_slug}",
                            params={"page": page, **{k: v for k, v in filters.items() if v}})
    resp.raise_for_status()
    return _list_payload(resp.json())

def _list_payload(data: dict) -> dict:
    """{items, pagination} from a phimapi list response (v1 nests them under "data", older ones do not)."""
    inner = data.get("data")
    if isinstance(inner, dict):
        items, pagination = inner.get("items", []), inner.get("params", {}).get("pagination", {})
    else:
        items, pagination = data.get("items", []), data.get("pagination", {})
    return {"items": _normalize_movie_list(items), "pagination": pagination}

HOME_LATEST_TTL = 1800
DANH_SACH_TTL = 3600

async def _fetch_home_latest(page: int) -> dict:
    resp = await client.get(f"https://phimapi.com/v1/api/home?page={page}")
    resp.raise_for_status()
    return _list_payload(resp.json())

# --- Smart Homepage sections ---

@app.get("/api/home/latest")
async def home_latest(page: int = 1):
    """Recently updated movies (KKPhim home API, cached 30 minutes)"""
    try:
        return await cached_fetch(f"home:latest:{page}", HOME_LATEST_TTL, lambda: _fetch_home_latest(page))
    except Exception as e:
        logger.error(f"Error fetching home/latest: {e}")
        return {"items": [], "pagination": {}, "error": str(e)}

# Phim Lẻ (single), Phim Chiếu Rạp (theatrical), Phim Bộ (series), TV Shows
DANH_SACH_SECTIONS = ("phim-le", "phim-chieu-rap", "phim-bo", "tv-shows")

def _make_danh_sach_route(section: str):
    async def route(page: int = 1, category: str = "", country: str = "", year: str = ""):
        """Movie list + genre/country/year filters (cached 1 hour per filter combination)"""
        try:
            return await cached_fetch(
                _danh_sach_cache_key(section, page, category, country, year), DANH_SACH_TTL,
                lambda: _fetch_danh_sach(section, page, category, country, year))
        except Exception as e:
            logger.error(f"Error fetching home/{section}: {e}")
            return {"items": [], "pagination": {}, "error": str(e)}
    route.__name__ = f"home_{section.replace('-', '_')}"
    return route

for _section in DANH_SACH_SECTIONS:
    app.get(f"/api/home/{_section}")(_make_danh_sach_route(_section))

async def _fetch_filter_list(api_path: str):
    """Fetch filter lists (genres/countries) from phimapi."""
    resp = await client.get(f"https://phimapi.com/v1/api/{api_path}")
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data", {}).get("items", [])
    return {"items": [{"name": it.get("name", ""), "slug": it.get("slug", "")} for it in items if it.get("slug")]}


@app.get("/api/home/categories")
async def home_categories():
    """Genre list for filters (cached 24 hours)"""
    try:
        return await cached_fetch("home:categories", 86400, lambda: _fetch_filter_list("the-loai"))
    except Exception as e:
        logger.error(f"Error fetching home/categories: {e}")
        return {"items": [], "error": str(e)}


@app.get("/api/home/countries")
async def home_countries():
    """Country list for filters (cached 24 hours)"""
    try:
        return await cached_fetch("home:countries", 86400, lambda: _fetch_filter_list("quoc-gia"))
    except Exception as e:
        logger.error(f"Error fetching home/countries: {e}")
        return {"items": [], "error": str(e)}

# --- Background cache warmer cho Homepage ---
async def _warm(section: str, page: int, cache_key: str, ttl: int, fetch_fn):
    try:
        await cached_fetch(cache_key, ttl, fetch_fn)
        logger.info(f"[CacheWarmer] OK section={section} page={page}")
    except Exception as e:
        logger.warning(f"[CacheWarmer] FAIL section={section} page={page}: {e}")

async def _warm_section(page: int):
    """Warm the "latest" section (same cache key as /api/home/latest)."""
    await _warm("latest", page, f"home:latest:{page}", HOME_LATEST_TTL, lambda: _fetch_home_latest(page))

async def _warm_danh_sach(section: str, page: int):
    """Warm the cache for a list (unfiltered), same key as the route without filters."""
    await _warm(section, page, _danh_sach_cache_key(section, page), DANH_SACH_TTL,
                lambda: _fetch_danh_sach(section, page))

async def home_cache_warmer():
    """Periodically refresh the homepage cache so users always get data fast."""
    while True:
        logger.info("[CacheWarmer] Bắt đầu làm mới cache homepage...")
        # Page 1 for all 5 sections (more pages can be added later)
        await _warm_section(1)
        for section in DANH_SACH_SECTIONS:
            await _warm_danh_sach(section, 1)
        interval = setting("home.refresh_minutes") * 60  # re-read before each sleep
        logger.info(f"[CacheWarmer] Đợi {interval}s cho lần refresh tiếp theo...")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["reset-admin-password"]:
        init_db()
        if admin.env_managed():
            print("HOMEFLIX_ADMIN_PASSWORD đang được đặt: mật khẩu admin lấy từ biến môi trường, bỏ qua.")
        else:
            print(f"Mật khẩu admin mới: {reset_admin_password()}")
    else:
        print("Dùng: python app.py reset-admin-password")
