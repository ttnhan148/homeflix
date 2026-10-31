"""HomeTV: thu thập nguồn IPTV vào hometv.db."""
import time

from hometv import db, sources


def init(path: str) -> None:
    """Mở hometv.db và tạo schema và nguồn mặc định lần đầu."""
    db.open(path)
    sources.seed_defaults(time.time())


__all__ = ["db", "init"]
