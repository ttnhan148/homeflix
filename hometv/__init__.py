"""HomeTV: collect IPTV sources into hometv.db."""
import time

from hometv import (api, catalog, db, fetcher, geo, health, health_api, health_scheduler, health_store, play,
                    public_api, scheduler, scoring, sources)


def init(path: str) -> None:
    """Open hometv.db and create the schema and default sources on first run."""
    db.open(path)
    sources.seed_defaults(time.time())


__all__ = ["api", "catalog", "db", "fetcher", "geo", "health", "health_api", "health_scheduler", "health_store", "init", "play",
           "public_api", "scheduler", "scoring"]
