"""HomeTV: thu thập nguồn IPTV vào hometv.db."""
from hometv import db


def init(path: str) -> None:
    """Mở hometv.db và tạo schema (Task 6 thêm nguồn mặc định ở đây)."""
    db.open(path)


__all__ = ["db", "init"]
