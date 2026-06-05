from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import NovelCandidate, Recommendation, SearchSeed
from .text import novel_fingerprint


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    source_type INTEGER,
                    raw_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_health (
                    source_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES sources(id)
                );

                CREATE TABLE IF NOT EXISTS discovery_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    limit_requested INTEGER NOT NULL,
                    seeds_json TEXT NOT NULL,
                    found_count INTEGER NOT NULL DEFAULT 0,
                    candidate_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS search_seeds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    value TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    used_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS novels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL,
                    intro TEXT,
                    kind TEXT,
                    latest_chapter TEXT,
                    word_count TEXT,
                    cover_url TEXT,
                    completed INTEGER NOT NULL DEFAULT 0,
                    completion_evidence TEXT,
                    first_discovered_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS novel_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    novel_id INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    book_url TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    discovered_run_id INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE(novel_id, source_id, book_url),
                    FOREIGN KEY(novel_id) REFERENCES novels(id)
                );

                CREATE TABLE IF NOT EXISTS samples (
                    novel_id INTEGER PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    chapter_count INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    sampled_at TEXT NOT NULL,
                    FOREIGN KEY(novel_id) REFERENCES novels(id)
                );

                CREATE TABLE IF NOT EXISTS recommendation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    k INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    profile_snapshot TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recommendation_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    novel_id INTEGER NOT NULL,
                    rank INTEGER NOT NULL,
                    score REAL NOT NULL,
                    reason TEXT NOT NULL,
                    risks TEXT NOT NULL,
                    style TEXT NOT NULL,
                    pacing TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, novel_id),
                    FOREIGN KEY(run_id) REFERENCES recommendation_runs(id),
                    FOREIGN KEY(novel_id) REFERENCES novels(id)
                );

                CREATE TABLE IF NOT EXISTS feedback_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    novel_id INTEGER NOT NULL,
                    selected INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(novel_id) REFERENCES novels(id)
                );

                CREATE TABLE IF NOT EXISTS preference_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def replace_sources(self, sources: list[dict]) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.execute("DELETE FROM sources")
            for source in sources:
                sid = source_id(source)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sources(id, name, url, enabled, source_type, raw_json, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        source.get("bookSourceName") or sid,
                        source.get("bookSourceUrl") or "",
                        1 if source.get("enabled", True) else 0,
                        source.get("bookSourceType"),
                        json.dumps(source, ensure_ascii=False),
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO source_health(source_id, status, updated_at)
                    VALUES (?, 'unknown', ?)
                    ON CONFLICT(source_id) DO UPDATE SET updated_at=excluded.updated_at
                    """,
                    (sid, now),
                )
        return len(sources)

    def list_sources(self, limit: int | None = None) -> list[dict]:
        sql = "SELECT raw_json FROM sources WHERE enabled = 1 AND source_type = 0 ORDER BY name"
        params: tuple = ()
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            return [json.loads(row["raw_json"]) for row in conn.execute(sql, params)]

    def create_discovery_run(self, limit: int, seeds: list[SearchSeed]) -> int:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO discovery_runs(started_at, limit_requested, seeds_json)
                VALUES (?, ?, ?)
                """,
                (now, limit, json.dumps([s.__dict__ for s in seeds], ensure_ascii=False)),
            )
            for seed in seeds:
                conn.execute(
                    "INSERT INTO search_seeds(value, strategy, used_at) VALUES (?, ?, ?)",
                    (seed.value, seed.strategy, now),
                )
            return int(cur.lastrowid)

    def finish_discovery_run(self, run_id: int, found: int, candidates: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE discovery_runs SET found_count = ?, candidate_count = ? WHERE id = ?",
                (found, candidates, run_id),
            )

    def has_discovered(self, fingerprint: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM novels WHERE fingerprint = ?", (fingerprint,)).fetchone()
            return row is not None

    def upsert_discovered(self, candidate: NovelCandidate, run_id: int) -> tuple[int, bool]:
        fp = novel_fingerprint(candidate.title, candidate.author)
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM novels WHERE fingerprint = ?", (fp,)).fetchone()
            created = row is None
            if row is None:
                cur = conn.execute(
                    """
                    INSERT INTO novels(
                        fingerprint, title, author, intro, kind, latest_chapter, word_count,
                        cover_url, completed, completion_evidence, first_discovered_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fp,
                        candidate.title,
                        candidate.author,
                        candidate.intro,
                        candidate.kind,
                        candidate.latest_chapter,
                        candidate.word_count,
                        candidate.cover_url,
                        1 if candidate.completed else 0,
                        candidate.completion_evidence,
                        now,
                        now,
                    ),
                )
                novel_id = int(cur.lastrowid)
            else:
                novel_id = int(row["id"])
                conn.execute(
                    """
                    UPDATE novels
                    SET last_seen_at = ?, completed = max(completed, ?),
                        completion_evidence = COALESCE(NULLIF(completion_evidence, ''), ?)
                    WHERE id = ?
                    """,
                    (now, 1 if candidate.completed else 0, candidate.completion_evidence, novel_id),
                )

            conn.execute(
                """
                INSERT OR IGNORE INTO novel_sources(
                    novel_id, source_id, source_name, book_url, raw_json, discovered_run_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    novel_id,
                    candidate.source_id,
                    candidate.source_name,
                    candidate.book_url,
                    json.dumps(candidate.raw, ensure_ascii=False),
                    run_id,
                    now,
                ),
            )
        return novel_id, created

    def update_source_health(self, source_id: str, status: str, error: str | None = None) -> None:
        now = utc_now()
        success_inc = 1 if status == "ok" else 0
        failure_inc = 1 if status != "ok" else 0
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO source_health(source_id, status, success_count, failure_count, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    status = excluded.status,
                    success_count = source_health.success_count + ?,
                    failure_count = source_health.failure_count + ?,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (source_id, status, success_inc, failure_inc, error, now, success_inc, failure_inc),
            )

    def pending_samples(self, limit: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT n.*, ns.source_id, ns.source_name, ns.book_url
                    FROM novels n
                    JOIN novel_sources ns ON ns.novel_id = n.id
                    LEFT JOIN samples s ON s.novel_id = n.id
                    WHERE n.completed = 1 AND s.novel_id IS NULL
                    GROUP BY n.id
                    ORDER BY n.first_discovered_at
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def save_sample(self, novel_id: int, source_id: str, chapter_count: int, text: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO samples(novel_id, source_id, chapter_count, text, sampled_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(novel_id) DO UPDATE SET
                    source_id=excluded.source_id,
                    chapter_count=excluded.chapter_count,
                    text=excluded.text,
                    sampled_at=excluded.sampled_at
                """,
                (novel_id, source_id, chapter_count, text, utc_now()),
            )

    def preference_profile(self) -> str:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT event_type, content, created_at FROM preference_events ORDER BY id DESC LIMIT 20"
            ).fetchall()
        if not rows:
            return "尚未记录明确偏好。请根据反馈理由逐步学习。"
        return "\n".join(f"- [{r['created_at']}] {r['event_type']}: {r['content']}" for r in rows)

    def has_preference_events(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM preference_events LIMIT 1").fetchone()
            return row is not None

    def add_preference_event(self, event_type: str, content: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO preference_events(event_type, content, created_at)
                VALUES (?, ?, ?)
                """,
                (event_type, content, utc_now()),
            )

    def recommendable_samples(self, limit: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT n.*, s.text AS sample_text, s.chapter_count
                    FROM novels n
                    JOIN samples s ON s.novel_id = n.id
                    WHERE NOT EXISTS (
                        SELECT 1 FROM recommendation_items ri WHERE ri.novel_id = n.id
                    )
                    ORDER BY s.sampled_at
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def save_recommendations(self, k: int, model: str, profile: str, recs: list[Recommendation]) -> int:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO recommendation_runs(created_at, k, model, profile_snapshot)
                VALUES (?, ?, ?, ?)
                """,
                (now, k, model, profile),
            )
            run_id = int(cur.lastrowid)
            for rank, rec in enumerate(recs, start=1):
                conn.execute(
                    """
                    INSERT INTO recommendation_items(
                        run_id, novel_id, rank, score, reason, risks, style, pacing, verdict, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        rec.novel_id,
                        rank,
                        rec.score,
                        rec.reason,
                        rec.risks,
                        rec.style,
                        rec.pacing,
                        rec.verdict,
                        now,
                    ),
                )
            return run_id

    def latest_recommendations(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM recommendation_runs ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                return []
            return list(
                conn.execute(
                    """
                    SELECT ri.*, n.title, n.author
                    FROM recommendation_items ri
                    JOIN novels n ON n.id = ri.novel_id
                    WHERE ri.run_id = ?
                    ORDER BY ri.rank
                    """,
                    (row["id"],),
                )
            )

    def add_feedback(self, novel_id: int, selected: bool, reason: str) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO feedback_events(novel_id, selected, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (novel_id, 1 if selected else 0, reason, now),
            )
            event_type = "selected" if selected else "skipped"
            conn.execute(
                """
                INSERT INTO preference_events(event_type, content, created_at)
                VALUES (?, ?, ?)
                """,
                (event_type, reason, now),
            )


def source_id(source: dict) -> str:
    raw = f"{source.get('bookSourceName', '')}::{source.get('bookSourceUrl', '')}"
    return novel_fingerprint(raw, str(source.get("customOrder", "")))[:16]
