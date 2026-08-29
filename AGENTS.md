# HomeFlix — Agent Guide

## Dev commands
- Start: `uvicorn app:app --reload --host 0.0.0.0 --port 6969`
- Install deps: `pip install -r requirements.txt`
- Deploy (Linux): `sudo ./install.sh`
- Update (Linux): `sudo ./update.sh`
- Logs: `sudo journalctl -u homeflix -f`
- No tests, no linter, no typechecker, no CI.

## Architecture
- **Single file** `app.py` (1135 lines) — FastAPI async app. All routes, cache, DB, background workers in one file.
- **Frontend** `templates/index.html` (2348 lines) — SPA with HLS.js, 10-foot TV UI, PWA service worker.
- **DB** SQLite `homeflix.db` (WAL mode), tables: `saved_movies`, `episode_downloads`, `api_cache`.
- **Port** always 6969.

## Key patterns (non-obvious)
- **Single Flight**: `asyncio.Event` in `download_locks` dict dedup concurrent `.ts` fetches. Multiple requests for same segment share one download.
- **Pass-through streaming**: writes to `.ts.part` file while streaming to client. Renames to `.ts` on completion. Stale `.part` >1hr cleaned hourly.
- **Stale-fallback cache**: if `phimapi.com` fails, return expired cache instead of error. API routes use this.
- **Cache dir**: `cache/{session_id}/` — auto-pruned >6hr TTL, 10GB cap.
- **ffmpeg dual-strategy**: first tries `-c copy` (stream copy), falls back to `-c:v copy -c:a aac` (audio re-encode).

## Important gotchas
- **Requires `phimapi.com`** (Vietnamese API). Search/movie features break if it's down. No fallback except stale cache.
- **Service named `homeflix`**, was previously `m3u8player`. Install/update scripts handle migration.
- **Service Worker** bypasses all `/proxy/`, `/media/`, `/api/` paths (only caches shell assets). Don't expect offline video.
- **Watched file auto-delete**: MP4 downloads deleted 3hr after marking "watched" (delayed cleanup worker).
- **No modular structure** — all backend changes go in `app.py`. No packages, no blueprints.
- `.gitignore` excludes `cache/` and `*.db` — local dev won't commit cache or database.

## Background workers (startup)
1. `prune_cache()` — hourly: purge expired sessions, enforce 10GB limit, clean `.part` files.
2. `download_worker()` — async queue: ffmpeg downloads with dual-strategy retry.
3. `delayed_cleanup_worker()` — delete watched episode files after 3hr delay.
