"""HomeTV: thu thập nguồn IPTV vào hometv.db."""
import time

from hometv import (api, catalog, db, fetcher, geo, health, health_scheduler, health_store, play, public_api,
                    scheduler, scoring, sources)


def init(path: str) -> None:
    """Mở hometv.db và tạo schema và nguồn mặc định lần đầu."""
    db.open(path)
    sources.seed_defaults(time.time())


__all__ = ["api", "catalog", "db", "fetcher", "geo", "health", "health_scheduler", "health_store", "init", "play",
           "public_api", "scheduler", "scoring"]
