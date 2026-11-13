"""Admin: admin password check, login lockout after repeated failures, 6-digit approval codes."""
import hashlib
import hmac
import math
import os
import secrets
import threading
import time
from typing import Callable, Iterable

PASSWORD_ENV = "HOMEFLIX_ADMIN_PASSWORD"
REQUEST_TTL = 600   # seconds an approval request stays valid
MAX_PENDING = 20    # maximum pending approval requests


def _password() -> str:
    # Re-read on every call, not cached at import
    return os.environ.get(PASSWORD_ENV, "")


def env_managed() -> bool:
    """The password comes from HOMEFLIX_ADMIN_PASSWORD (wins over the stored one, cannot be changed in the UI)."""
    return bool(_password())


def configured(stored_hash: str | None = None) -> bool:
    return env_managed() or bool(stored_hash)


def _utf8(text) -> bytes | None:
    if not isinstance(text, str):
        return None
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        return None  # lone surrogate (JSON "\ud800") cannot be UTF-8 encoded


def password_ok(candidate, stored_hash: str | None = None) -> bool:
    """Check against the env password if set, otherwise against the stored scrypt hash.
    Always False if not configured, candidate is not a str, or it cannot be UTF-8 encoded."""
    pw = _password()
    if not pw:
        return verify_password(candidate, stored_hash)
    b = _utf8(candidate)
    if b is None:
        return False
    return hmac.compare_digest(hashlib.sha256(pw.encode("utf-8")).digest(), hashlib.sha256(b).digest())


# --- Stored password: stdlib scrypt, "scrypt$n$r$p$salt_hex$hash_hex" ---
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1   # ~16 MB, tens of ms per check


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(candidate, stored_hash) -> bool:
    b = _utf8(candidate)
    if not b or not isinstance(stored_hash, str):
        return False
    parts = stored_hash.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = (int(x) for x in parts[1:4])
        salt, expected = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
        digest = hashlib.scrypt(b, salt=salt, n=n, r=r, p=p, dklen=len(expected))
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


def new_password_error(value) -> str | None:
    """Why a new password is unacceptable (Vietnamese message), or None if it is fine."""
    if not isinstance(value, str) or _utf8(value) is None:
        return "Mật khẩu không hợp lệ"
    if not MIN_PASSWORD_LEN <= len(value) <= MAX_PASSWORD_LEN:
        return f"Mật khẩu phải dài {MIN_PASSWORD_LEN}–{MAX_PASSWORD_LEN} ký tự"
    return None


def generate_password() -> str:
    """Random first-start password: 18 URL-safe ASCII chars (~108 bits)."""
    return secrets.token_urlsafe(14)[:18]


class LoginLimiter:
    """Count failures per key; max_failures within window seconds locks the key for lockout seconds."""

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
