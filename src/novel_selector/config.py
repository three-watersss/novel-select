from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SOURCE_URL = "https://legado.aoaostar.com/sources/b778fe6b.json"


@dataclass(frozen=True)
class Settings:
    db_path: Path
    source_url: str
    openai_api_key: str | None
    openai_base_url: str | None
    openai_model: str
    request_timeout: float
    user_agent: str


def load_settings() -> Settings:
    load_dotenv(Path(".env"))
    return Settings(
        db_path=Path(os.getenv("NOVEL_SELECTOR_DB", "data/novel-selector.sqlite3")),
        source_url=os.getenv("NOVEL_SELECTOR_SOURCE_URL", DEFAULT_SOURCE_URL),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        request_timeout=float(os.getenv("NOVEL_SELECTOR_TIMEOUT", "8")),
        user_agent=os.getenv(
            "NOVEL_SELECTOR_USER_AGENT",
            "novel-selector/0.1 (+https://github.com/aoaostar/legado-compatible)",
        ),
    )


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
