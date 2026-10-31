"""Quản trị: kiểm tra mật khẩu admin, khóa đăng nhập khi sai nhiều lần, mã duyệt 6 chữ số."""
import hashlib
import hmac
import math
import os
import secrets
import threading
import time
from typing import Callable, Iterable

PASSWORD_ENV = "HOMEFLIX_ADMIN_PASSWORD"
REQUEST_TTL = 600   # giây một yêu cầu duyệt còn hiệu lực
MAX_PENDING = 20    # số yêu cầu chờ duyệt tối đa


def _password() -> str:
    # Đọc lại mỗi lần gọi, không cache lúc import
    return os.environ.get(PASSWORD_ENV, "")


def configured() -> bool:
    return bool(_password())


def password_ok(candidate) -> bool:
    """Chưa cấu hình hoặc candidate không phải str thì luôn False."""
    pw = _password()
    if not pw or not isinstance(candidate, str):
        return False
    a = hashlib.sha256(pw.encode("utf-8")).digest()
    b = hashlib.sha256(candidate.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


class LoginLimiter:
    """Đếm sai riêng từng key; đủ max_failures trong window giây thì khóa key lockout giây."""

    def __init__(self, max_failures: int = 5, window: float = 900, lockout: float = 900,
                 clock: Callable[[], float] = time.time):
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._clock = clock
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, keys: Iterable[str]) -> int:
        now = self._clock()
        with self._lock:
            left = 0.0
            for k in keys:
                until = self._locked.get(k)
                if until is None:
                    continue
                if until <= now:
                    del self._locked[k]
                else:
                    left = max(left, until - now)
            return math.ceil(left)

    def record_failure(self, keys: Iterable[str]) -> None:
        now = self._clock()
        with self._lock:
            for k in keys:
                hits = [t for t in self._fails.get(k, []) if now - t < self.window]
                hits.append(now)
                if len(hits) >= self.max_failures:
                    self._locked[k] = now + self.lockout
                    hits = []
                if hits:
                    self._fails[k] = hits
                else:
                    self._fails.pop(k, None)

    def reset(self, keys: Iterable[str]) -> None:
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)
                self._locked.pop(k, None)


def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"
