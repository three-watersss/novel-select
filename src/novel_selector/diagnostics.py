from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .db import Database
from .initial_profile import MIN_PROFILE_NOVELS, list_local_novels


@dataclass(frozen=True)
class Check:
    level: str
    label: str
    message: str


def status_text(settings: Settings, db: Database) -> str:
    stats = db.stats()
    novel_count = len(list_local_novels(settings.novels_dir))
    health = stats["source_health"]
    health_text = ", ".join(f"{key}={value}" for key, value in sorted(health.items())) or "none"
    lines = [
        "Novel Selector Status",
        f"- Database: {settings.db_path} ({'initialized' if stats['initialized'] else 'not initialized'})",
        f"- Local favorite novels: {novel_count}/{MIN_PROFILE_NOVELS} txt files in {settings.novels_dir}",
        f"- Preference profile: {'ready' if stats['has_initial_profile'] else 'missing'}",
        f"- Sources: {stats['sources']} (health: {health_text})",
        f"- Novels discovered: {stats['novels']} total, {stats['completed_novels']} completed candidates",
        f"- Samples: {stats['samples']} saved, {stats['pending_samples']} pending",
        f"- Recommendation runs: {stats['recommendation_runs']}"
        + (f" (latest: {stats['latest_recommendation_at']})" if stats["latest_recommendation_at"] else ""),
        f"- Feedback events: {stats['feedback_events']}",
    ]
    return "\n".join(lines)


def profile_text(db: Database) -> str | None:
    if not db.is_initialized() or not db.has_preference_events():
        return None
    return db.preference_profile()


def doctor_checks(settings: Settings, db: Database) -> list[Check]:
    stats = db.stats()
    novel_count = len(list_local_novels(settings.novels_dir))
    checks = [
        _check(Path(".env").exists(), ".env", ".env exists", ".env not found"),
        _check(bool(settings.openai_api_key), "LLM API key", "configured", "missing OPENAI_API_KEY"),
        Check("OK" if settings.openai_model else "FAIL", "LLM model", settings.openai_model or "missing OPENAI_MODEL"),
        Check(
            "OK" if settings.openai_base_url else "WARN",
            "LLM base URL",
            settings.openai_base_url or "not set; OpenAI default will be used",
        ),
        _check(settings.novels_dir.exists(), "novels dir", f"{settings.novels_dir} exists", f"{settings.novels_dir} not found"),
        _check(
            novel_count >= MIN_PROFILE_NOVELS,
            "favorite novels",
            f"{novel_count}/{MIN_PROFILE_NOVELS} txt files",
            f"only {novel_count}/{MIN_PROFILE_NOVELS} txt files",
        ),
        _check(settings.db_path.exists(), "database file", f"{settings.db_path} exists", f"{settings.db_path} not found"),
        _check(stats["initialized"], "database schema", "initialized", "not initialized"),
        _check(stats["has_initial_profile"], "initial profile", "ready", "missing; run novel-selector init"),
        _check(settings.log_dir.exists(), "logs dir", f"{settings.log_dir} exists", f"{settings.log_dir} not found", warn=True),
    ]
    for name in ("workflow.log", "source.log", "llm.log"):
        path = settings.log_dir / name
        checks.append(_check(path.exists(), name, "exists", "not found", warn=True))
    return checks


def doctor_text(settings: Settings, db: Database) -> str:
    lines = ["Novel Selector Doctor"]
    lines.extend(f"[{check.level}] {check.label}: {check.message}" for check in doctor_checks(settings, db))
    return "\n".join(lines)


def doctor_has_failures(settings: Settings, db: Database) -> bool:
    return any(check.level == "FAIL" for check in doctor_checks(settings, db))


def _check(ok: bool, label: str, ok_message: str, fail_message: str, warn: bool = False) -> Check:
    if ok:
        return Check("OK", label, ok_message)
    return Check("WARN" if warn else "FAIL", label, fail_message)
