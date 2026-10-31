"""Cài đặt hệ thống: khai báo, kiểm tra và lưu trong bảng app_settings (khóa 'setting:<key>')."""
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

_PREFIX = "setting:"


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    type: type
    default: int | bool
    min: int | None
    max: int | None
    step: int
    unit: str
    group: str


SETTINGS: tuple[Setting, ...] = (
    Setting("cache.max_gb", "Dung lượng tối đa", int, 10, 1, 500, 1, "GB", "system"),
    Setting("cache.max_age_hours", "Giữ cache", int, 6, 1, 168, 1, "giờ", "system"),
    Setting("prefetch.concurrency", "Luồng tải trước", int, 4, 1, 16, 1, "", "system"),
    Setting("home.refresh_minutes", "Làm mới trang chủ", int, 10, 5, 120, 5, "phút", "system"),
)
GROUP_LABELS = {"system": "Hệ thống"}
_BY_KEY = {s.key: s for s in SETTINGS}


class SettingsError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__(str(errors))
        self.errors = errors


def check(key: str, value) -> int | bool:
    """Trả giá trị đã hợp lệ, hoặc ném SettingsError({key: lý do})."""
    s = _BY_KEY.get(key)
    if s is None:
        raise SettingsError({key: "không có cài đặt này"})
    if s.type is bool:
        if not isinstance(value, bool):
            raise SettingsError({key: "phải là true hoặc false"})
        return value
    # bool là con của int nên loại trước
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError({key: "phải là số nguyên"})
    if (s.min is not None and value < s.min) or (s.max is not None and value > s.max):
        raise SettingsError({key: f"phải từ {s.min} đến {s.max}"})
    return value


class SettingsStore:
    def __init__(self, connect: Callable[[], sqlite3.Connection], lock: threading.Lock):
        self._connect = connect
        self._lock = lock
        self._raw: dict[str, str] | None = None
        self._warned: set[str] = set()

    def invalidate(self) -> None:
        self._raw = None

    def _load(self) -> dict[str, str]:
        raw = self._raw
        if raw is None:
            with self._lock:
                rows = self._connect().execute(
                    "SELECT key, value FROM app_settings WHERE key LIKE 'setting:%'"
                ).fetchall()
            raw = self._raw = {k[len(_PREFIX):]: v for k, v in rows}
        return raw

    def get(self, key: str) -> int | bool:
        s = _BY_KEY[key]
        text = self._load().get(key)
        if text is None:
            return s.default
        try:
            return check(key, json.loads(text))
        except (ValueError, SettingsError):
            if key not in self._warned:
                self._warned.add(key)
                log.warning("cài đặt %s hỏng (%r), dùng mặc định", key, text)
            return s.default

    def update(self, values: dict) -> None:
        clean, errors = {}, {}
        for key, value in values.items():
            try:
                clean[key] = check(key, value)
            except SettingsError as e:
                errors.update(e.errors)
        if errors:
            raise SettingsError(errors)
        with self._lock:
            conn = self._connect()
            with conn:
                for key, value in clean.items():
                    conn.execute(
                        "INSERT INTO app_settings(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (_PREFIX + key, json.dumps(value)),
                    )
        self.invalidate()

    def describe(self) -> list[dict]:
        groups = []
        for gid, label in GROUP_LABELS.items():
            items = [
                {"key": s.key, "label": s.label, "type": s.type.__name__, "value": self.get(s.key),
                 "default": s.default, "min": s.min, "max": s.max, "step": s.step, "unit": s.unit}
                for s in SETTINGS if s.group == gid
            ]
            groups.append({"id": gid, "label": label, "items": items})
        return groups
