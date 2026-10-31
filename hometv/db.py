"""SQLite store của HomeTV: mở DB, schema, transaction. Thời gian là epoch giây."""
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA_VERSION = 3

# Khóa ghi: mọi transaction giữ khóa này
lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    enabled INTEGER NOT NULL DEFAULT 1,
    rights_status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (rights_status IN ('pending_review', 'approved', 'rejected')),
    refresh_interval INTEGER NOT NULL DEFAULT 21600,
    timeout INTEGER NOT NULL DEFAULT 30,
    max_size_mb INTEGER NOT NULL DEFAULT 20,
    last_fetch_at REAL,
    last_success_at REAL,
    last_error TEXT,
    content_hash TEXT,
    channel_count INTEGER NOT NULL DEFAULT 0,
    stream_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_fetch_at REAL,
    created_at REAL,
    updated_at REAL,
    next_check_at REAL,
    last_check_at REAL
);
CREATE TABLE IF NOT EXISTS source_versions (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    content_hash TEXT,
    fetched_at REAL NOT NULL,
    entry_count INTEGER,
    body BLOB
);
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    tvg_id TEXT,
    normalized_name TEXT,
    broadcaster TEXT,
    display_name TEXT,
    category TEXT,
    category_source TEXT NOT NULL DEFAULT 'auto'
        CHECK (category_source IN ('auto', 'manual')),
    logo_url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS streams (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    protocol TEXT,
    http_headers TEXT,
    display_name TEXT,
    quality_hint TEXT,
    drm_hint INTEGER NOT NULL DEFAULT 0,
    first_seen_at REAL,
    last_seen_at REAL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    job_type TEXT,
    source_id TEXT,
    status TEXT CHECK (status IN ('running', 'ok', 'error')),
    started_at REAL,
    completed_at REAL,
    duration_ms INTEGER,
    records_processed INTEGER,
    skipped_lines INTEGER,
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS stream_health (
    stream_id INTEGER PRIMARY KEY REFERENCES streams(id) ON DELETE CASCADE,
    checked_at REAL,
    ok INTEGER NOT NULL,
    error TEXT,
    latency_ms INTEGER,
    throughput_kbps INTEGER,
    width INTEGER,
    height INTEGER,
    vcodec TEXT,
    bitrate_kbps INTEGER,
    fail_streak INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 50
);
CREATE TABLE IF NOT EXISTS favorites (
    channel_id INTEGER PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS recents (
    channel_id INTEGER PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
    viewed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_channels_tvg_id ON channels(tvg_id);
CREATE INDEX IF NOT EXISTS idx_channels_normalized_name ON channels(normalized_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_streams_source_hash ON streams(source_id, url_hash);
CREATE INDEX IF NOT EXISTS idx_streams_channel ON streams(channel_id);
CREATE INDEX IF NOT EXISTS idx_jobs_source_status ON jobs(source_id, status);
CREATE INDEX IF NOT EXISTS idx_source_versions_source_fetched ON source_versions(source_id, fetched_at);
"""


def open(path: str) -> None:
    """Mở DB (đóng kết nối cũ nếu có), bật WAL + FK, tạo schema nếu chưa có."""
    global _conn
    close()
    c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    try:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(_SCHEMA)
        # Bảng sources có từ trước: CREATE TABLE IF NOT EXISTS không thêm cột mới
        have = {r[1] for r in c.execute("PRAGMA table_info(sources)")}
        for col in ("next_check_at", "last_check_at"):
            if col not in have:
                c.execute(f"ALTER TABLE sources ADD COLUMN {col} REAL")
        if c.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            c.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        else:
            c.execute("UPDATE schema_version SET version = ? WHERE version < ?", (SCHEMA_VERSION, SCHEMA_VERSION))
    except Exception:
        c.close()
        raise
    _conn = c


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("hometv.db chưa mở")
    return _conn


@contextmanager
def tx():
    """Transaction ghi: giữ khóa, BEGIN IMMEDIATE, commit khi êm, rollback khi lỗi.
    Không reentrant: không lồng tx trong tx."""
    with lock:
        c = conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")
