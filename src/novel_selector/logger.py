from __future__ import annotations

import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


LOGGER_NAMES = {
    "workflow": "novel_selector.workflow",
    "source": "novel_selector.source",
    "llm": "novel_selector.llm",
}

SECRET_KEYS = ("api_key", "apikey", "authorization", "token", "secret", "password")
DEFAULT_PROMPT_SUMMARY_CHARS = 500


def configure_logging(
    log_dir: Path,
    level: str = "INFO",
    max_file_bytes: int = 5 * 1024 * 1024,
    backups: int = 3,
    max_dir_bytes: int = 50 * 1024 * 1024,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    numeric_level = _level_value(level)
    for category, logger_name in LOGGER_NAMES.items():
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.setLevel(numeric_level)
        logger.propagate = False

        handler = RotatingFileHandler(
            log_dir / f"{category}.log",
            maxBytes=max_file_bytes,
            backupCount=backups,
            encoding="utf-8",
        )
        handler.setLevel(numeric_level)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    cleanup_logs(log_dir, max_dir_bytes)


def cleanup_logs(log_dir: Path, max_dir_bytes: int) -> None:
    if max_dir_bytes <= 0 or not log_dir.exists():
        return
    files = [path for path in log_dir.glob("*.log*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    if total <= max_dir_bytes:
        return
    for path in sorted(files, key=lambda item: item.stat().st_mtime):
        try:
            size = path.stat().st_size
            path.unlink()
            total -= size
        except OSError:
            continue
        if total <= max_dir_bytes:
            break


def workflow_event(event: str, level: str = "INFO", **fields: Any) -> None:
    log_event("workflow", event, level, **fields)


def source_event(event: str, level: str = "INFO", **fields: Any) -> None:
    log_event("source", event, level, **fields)


def llm_event(event: str, level: str = "INFO", **fields: Any) -> None:
    log_event("llm", event, level, **fields)


def log_event(category: str, event: str, level: str = "INFO", **fields: Any) -> None:
    logger = logging.getLogger(LOGGER_NAMES[category])
    if not logger.handlers:
        return
    payload = {"event": event, **_sanitize(fields)}
    logger.log(_level_value(level), json.dumps(payload, ensure_ascii=False, default=str))


def prompt_summary(prompt: str, limit: int = DEFAULT_PROMPT_SUMMARY_CHARS) -> dict[str, Any]:
    normalized = " ".join(prompt.split())
    return {
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "length": len(prompt),
        "preview": normalized[:limit],
    }


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = _sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item) for item in value)
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(secret in lowered for secret in SECRET_KEYS)


def _level_value(level: str) -> int:
    return getattr(logging, level.upper(), logging.INFO)
