from __future__ import annotations

from collections import Counter

from .db import Database, source_id
from .legado import LegadoClient, UnsupportedSourceError, source_support_status
from .llm import LLMClient
from .models import NovelCandidate, SearchSeed
from .text import novel_fingerprint


class DiscoveryService:
    def __init__(self, db: Database, legado: LegadoClient, llm: LLMClient):
        self.db = db
        self.legado = legado
        self.llm = llm

    def discover(
        self,
        limit: int,
        source_limit: int | None = None,
        max_searches: int = 80,
        seed_values: list[str] | None = None,
    ) -> tuple[int, int, Counter]:
        profile = self.db.preference_profile()
        seeds = (
            [SearchSeed(value=value, strategy="manual") for value in seed_values]
            if seed_values
            else self.llm.generate_search_seeds(profile)
        )
        run_id = self.db.create_discovery_run(limit, seeds)
        found = 0
        candidates = 0
        stats: Counter = Counter()
        sources = prioritize_sources(self.db.list_sources())
        if source_limit:
            sources = sources[:source_limit]
        searches = 0
        for seed in seeds:
            for source in sources:
                if searches >= max_searches:
                    self.db.finish_discovery_run(run_id, found, candidates)
                    stats["max_searches_reached"] += 1
                    return found, candidates, stats
                if candidates >= limit:
                    self.db.finish_discovery_run(run_id, found, candidates)
                    return found, candidates, stats
                sid = source_id(source)
                try:
                    results = self.legado.search(source, seed.value)
                except UnsupportedSourceError as exc:
                    self.db.update_source_health(sid, "unsupported", str(exc))
                    stats["unsupported_sources"] += 1
                    continue
                except Exception as exc:
                    self.db.update_source_health(sid, "error", str(exc)[:500])
                    stats["source_errors"] += 1
                    continue
                searches += 1
                self.db.update_source_health(sid, "ok")
                stats["searches"] += 1
                for item in results:
                    found += 1
                    fp = novel_fingerprint(item.title, item.author)
                    if self.db.has_discovered(fp):
                        stats["already_discovered"] += 1
                        continue
                    enriched = self._enrich_if_needed(source, item)
                    novel_id, created = self.db.upsert_discovered(enriched, run_id)
                    if created:
                        stats["new_discoveries"] += 1
                    if enriched.completed:
                        candidates += 1
                        stats["completed_candidates"] += 1
                    else:
                        stats["not_completed"] += 1
        self.db.finish_discovery_run(run_id, found, candidates)
        return found, candidates, stats

    def _enrich_if_needed(self, source: dict, candidate: NovelCandidate) -> NovelCandidate:
        if candidate.completed:
            return candidate
        try:
            return self.legado.enrich_book_info(source, candidate)
        except Exception:
            return candidate


def prioritize_sources(sources: list[dict]) -> list[dict]:
    supported = [source for source in sources if source_support_status(source)[0]]
    return sorted(supported, key=source_priority)


def source_priority(source: dict) -> tuple[int, str]:
    search_url = str(source.get("searchUrl") or "")
    rule = source.get("ruleSearch") or {}
    book_list = str(rule.get("bookList") or "")
    score = 0
    if search_url.lstrip().startswith(("http://", "https://", "/")):
        score -= 4
    if "," not in search_url:
        score -= 2
    if book_list.startswith("$"):
        score -= 5
    if book_list.startswith((".", "#", "class.", "tag.")):
        score -= 3
    if source.get("ruleToc"):
        score -= 1
    if source.get("ruleContent"):
        score -= 1
    return score, source.get("bookSourceName") or ""
