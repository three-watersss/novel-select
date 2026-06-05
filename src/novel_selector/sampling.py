from __future__ import annotations

import json

from .db import Database
from .legado import LegadoClient
from .models import NovelCandidate


class SamplingService:
    def __init__(self, db: Database, legado: LegadoClient):
        self.db = db
        self.legado = legado

    def sample(self, limit: int, chapters: int = 10) -> tuple[int, int]:
        rows = self.db.pending_samples(limit * 10)
        saved = 0
        failed = 0
        sources_by_id = {self._sid(s): s for s in self.db.list_sources()}
        for row in rows:
            if saved >= limit:
                break
            source = sources_by_id.get(row["source_id"])
            if not source:
                failed += 1
                continue
            candidate = NovelCandidate(
                title=row["title"],
                author=row["author"],
                source_id=row["source_id"],
                source_name=row["source_name"],
                book_url=row["book_url"],
                intro=row["intro"] or "",
                kind=row["kind"] or "",
                latest_chapter=row["latest_chapter"] or "",
                completed=bool(row["completed"]),
            )
            try:
                chapter_list = self.legado.chapters(source, candidate, limit=chapters)
                texts: list[str] = []
                for chapter in chapter_list:
                    chapter.content = self.legado.chapter_content(source, chapter)
                    if chapter.content:
                        texts.append(f"# {chapter.title}\n\n{chapter.content}")
                if not texts:
                    failed += 1
                    self.db.update_source_health(row["source_id"], "error", "empty_sample")
                    continue
                self.db.save_sample(row["id"], row["source_id"], len(texts), "\n\n".join(texts))
                self.db.update_source_health(row["source_id"], "ok")
                saved += 1
            except Exception as exc:
                self.db.update_source_health(row["source_id"], "error", str(exc)[:500])
                failed += 1
        return saved, failed

    @staticmethod
    def _sid(source: dict) -> str:
        from .db import source_id

        return source_id(source)
