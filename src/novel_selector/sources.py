from __future__ import annotations

import json

from .db import Database
from .http import HttpClient


def sync_sources(db: Database, http: HttpClient, url: str) -> int:
    fetched = http.get(url)
    data = json.loads(fetched.text)
    if not isinstance(data, list):
        raise ValueError("Legado source URL did not return a JSON array")
    return db.replace_sources([item for item in data if isinstance(item, dict)])

