"""Cast: trung gian trong bộ nhớ giữa iPhone (controller) và TV (receiver).

Chỉ đúng khi server chạy một tiến trình: hàng đợi, hiện diện, liên kết và trạng thái nằm trong bộ nhớ.
Không import app.py; phần cần DB và HTTP nằm ở app.py."""
import asyncio
import json
import math
import threading
import time
from collections import deque

PRESENCE_TTL = 40
MAX_SUBSCRIBERS_PER_DEVICE = 4
QUEUE_LIMIT = 100
PENDING_RETRY_SECONDS = 10   # yêu cầu kết nối lặp lại chỉ phát lại cho TV sau chừng này giây
PENDING_MAX_AGE = 600        # yêu cầu chờ quá lâu bị bỏ


class Subscriber:
    """Một kết nối SSE của một thiết bị. put() an toàn đa luồng; chờ được cả đồng bộ lẫn asyncio."""

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.closed = False
        self._q = deque()
        self._lock = threading.Lock()
        self._sync = threading.Event()
        self._waiters = []  # (loop, asyncio.Event)

    def _wake(self):
        self._sync.set()
        with self._lock:
            waiters = list(self._waiters)
        for loop, ev in waiters:
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:  # loop đã đóng
                pass

    def put(self, event: str, data: dict) -> None:
        with self._lock:
            if self.closed:
                return
            self._q.append((event, data))
            while len(self._q) > QUEUE_LIMIT:
                self._q.popleft()
        self._wake()

    def _take(self) -> list:
        with self._lock:
            items = list(self._q)
            self._q.clear()
            self._sync.clear()
        return items

    def drain(self, timeout: float = 0) -> list:
        items = self._take()
        if items or timeout <= 0:
            return items
        self._sync.wait(timeout)
        return self._take()

    async def next(self, timeout: float) -> list:
        loop = asyncio.get_running_loop()
        ev = asyncio.Event()
        with self._lock:
            self._waiters.append((loop, ev))
        try:
            items = self._take()  # kiểm tra lại sau khi đăng ký để không lỡ put xen giữa
            if items or self.closed:
                return items
            try:
                await asyncio.wait_for(ev.wait(), timeout)
            except asyncio.TimeoutError:
                pass
            return self._take()
        finally:
            with self._lock:
                self._waiters.remove((loop, ev))

    def close(self) -> None:
        self.closed = True
        self._wake()


class CastHub:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._subs: dict[str, list[Subscriber]] = {}
        self._presence: dict[str, tuple[float, str]] = {}
        self._links: dict[str, str] = {}
        self._pending: dict[tuple[str, str], float] = {}
        self._state: dict[str, dict] = {}

    # --- kết nối SSE ---
    def subscribe(self, device_id: str) -> Subscriber:
        sub = Subscriber(device_id)
        with self._lock:
            subs = self._subs.setdefault(device_id, [])
            subs.append(sub)
            evicted = subs.pop(0) if len(subs) > MAX_SUBSCRIBERS_PER_DEVICE else None
        if evicted:
            evicted.close()
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            subs = self._subs.get(sub.device_id, [])
            if sub in subs:
                subs.remove(sub)
            if not subs:
                self._subs.pop(sub.device_id, None)
        sub.close()

    def has_subscribers(self, device_id: str) -> bool:
        with self._lock:
            return bool(self._subs.get(device_id))

    def publish(self, device_id: str, event: str, data: dict, newest_only: bool = False) -> int:
        """Gửi tới mọi kết nối của thiết bị, hoặc chỉ kết nối mới nhất (lệnh và yêu cầu: một thiết bị
        mở nhiều tab thì chỉ tab mới nhất hành động)."""
        with self._lock:
            subs = list(self._subs.get(device_id, []))
            if newest_only:
                subs = subs[-1:]
        for sub in subs:
            sub.put(event, data)
        return len(subs)

    # --- hiện diện ---
    def heartbeat(self, device_id: str, kind: str) -> None:
        with self._lock:
            now = self._clock()
            self._presence[device_id] = (now, kind)
            if len(self._presence) > 500:   # dọn thiết bị đã biến mất từ lâu
                cutoff = now - PRESENCE_TTL * 10
                self._presence = {d: v for d, v in self._presence.items() if v[0] >= cutoff}

    def is_online(self, device_id: str) -> bool:
        with self._lock:
            seen = self._presence.get(device_id)
        return seen is not None and self._clock() - seen[0] <= PRESENCE_TTL

    # --- liên kết controller -> TV ---
    def link(self, controller_id: str, target_id: str) -> None:
        with self._lock:
            self._links[controller_id] = target_id

    def unlink(self, controller_id: str) -> str | None:
        with self._lock:
            return self._links.pop(controller_id, None)

    def linked_target(self, controller_id: str) -> str | None:
        with self._lock:
            return self._links.get(controller_id)

    def controllers_of(self, target_id: str) -> list[str]:
        with self._lock:
            return [c for c, t in self._links.items() if t == target_id]

    def add_pending(self, target_id: str, controller_id: str) -> bool:
        """True nếu nên (phát lại) yêu cầu tới TV: yêu cầu mới, hoặc đã quá PENDING_RETRY_SECONDS."""
        with self._lock:
            now = self._clock()
            last = self._pending.get((target_id, controller_id))
            if last is not None and now - last < PENDING_RETRY_SECONDS:
                return False
            self._pending[(target_id, controller_id)] = now
            return True

    def pop_pending(self, target_id: str, controller_id: str) -> bool:
        with self._lock:
            return self._pending.pop((target_id, controller_id), None) is not None

    def pending_for(self, target_id: str) -> list[str]:
        with self._lock:
            now = self._clock()
            self._pending = {k: t for k, t in self._pending.items() if now - t < PENDING_MAX_AGE}
            return [c for (t, c) in self._pending if t == target_id]

    def clear_pending(self, target_id: str) -> None:
        with self._lock:
            self._pending = {k: t for k, t in self._pending.items() if k[0] != target_id}

    # --- trạng thái phát ---
    def set_state(self, target_id: str, state: dict) -> None:
        with self._lock:
            self._state[target_id] = state

    def get_state(self, target_id: str) -> dict | None:
        with self._lock:
            return self._state.get(target_id)

    def forget(self, device_id: str) -> None:
        with self._lock:
            subs = self._subs.pop(device_id, [])
            self._presence.pop(device_id, None)
            self._state.pop(device_id, None)
            self._links = {c: t for c, t in self._links.items() if c != device_id and t != device_id}
            self._pending = {k: t for k, t in self._pending.items() if device_id not in k}
        for sub in subs:
            sub.close()


# --- kiểm tra dữ liệu từ thiết bị khác ---
def _number(value, lo=None, hi=None):
    """Số hữu hạn (bool không tính) trong [lo, hi]; ném ValueError nếu không."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("not a finite number")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError("number out of range")
    return float(value)


def _short_text(value, limit=200):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("bad text")
    return value


def validate_command(body, slug_ok) -> dict:
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    kind = body.get("type")
    if kind == "play":
        if not slug_ok(body.get("slug")):
            raise ValueError("invalid slug")
        url = body.get("episode_url")
        if url is not None and not (isinstance(url, str) and len(url) <= 2048 and url.startswith(("http://", "https://"))):
            raise ValueError("invalid episode_url")
        position = body.get("position")
        return {"type": "play", "slug": body["slug"], "episode_url": url,
                "position": None if position is None else _number(position, 0, 86400)}
    if kind in ("pause", "resume", "stop"):
        return {"type": kind}
    if kind == "seek":
        if ("delta" in body) + ("position" in body) != 1:
            raise ValueError("seek needs exactly one of delta or position")
        if "delta" in body:
            return {"type": "seek", "delta": _number(body["delta"], -3600, 3600)}
        return {"type": "seek", "position": _number(body["position"], 0, 86400)}
    if kind == "episode":
        delta = body.get("delta")
        if isinstance(delta, bool) or delta not in (-1, 1) or not isinstance(delta, int):
            raise ValueError("episode delta must be -1 or 1")
        return {"type": "episode", "delta": delta}
    if kind == "options":
        out = {}
        for key in ("autoNext", "autoIntro"):
            if key in body:
                if not isinstance(body[key], bool):
                    raise ValueError(f"{key} must be boolean")
                out[key] = body[key]
        if not out:
            raise ValueError("options needs autoNext or autoIntro")
        return {"type": "options", **out}
    raise ValueError("unknown command type")


def validate_state(body, slug_ok) -> dict:
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    if not isinstance(body.get("playing"), bool):
        raise ValueError("playing must be boolean")
    slug = body.get("slug")
    if slug is not None and not slug_ok(slug):
        raise ValueError("invalid slug")
    def opt_number(key):
        return None if body.get(key) is None else _number(body[key], 0)
    return {"slug": slug, "title": _short_text(body.get("title")), "episode": _short_text(body.get("episode")),
            "position": opt_number("position"), "duration": opt_number("duration"), "playing": body["playing"]}


def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
