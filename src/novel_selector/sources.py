from __future__ import annotations

import json

from .db import Database
from .http import HttpClient
from .logger import source_event


def sync_sources(db: Database, http: HttpClient, url: str) -> int:
    source_event("sync_sources_start", url=url)
    fetched = http.get(url)
    data = json.loads(fetched.text)
    if not isinstance(data, list):
        source_event("sync_sources_failed", "ERROR", url=url, reason="not_json_array")
        raise ValueError("Legado source URL did not return a JSON array")
    sources = [item for item in data if isinstance(item, dict)]
    count = db.replace_sources(sources)
    source_event("sync_sources_end", url=url, source_count=count, raw_items=len(data))
    return count
