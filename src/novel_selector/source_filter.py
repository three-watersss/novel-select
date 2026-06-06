from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

from .db import Database, source_id
from .legado import LegadoClient, UnsupportedSourceError, source_support_status
from .logger import source_event, workflow_event
from .models import NovelCandidate


@dataclass
class SourceCapabilityResult:
    source_id: str
    source_name: str
    supports_search: bool = False
    returns_metadata: bool = False
    detects_completion: bool = False
    supports_toc: bool = False
    supports_content: bool = False
    requires_webview: bool = False
    unstable: bool = False
    passed: bool = False
    success_rate: float = 0.0
    last_error_type: str | None = None
    last_error: str | None = None

    def to_record(self) -> dict:
        return asdict(self)


class SourceFilterService:
    def __init__(self, db: Database, legado: LegadoClient):
        self.db = db
        self.legado = legado

    def filter_sources(
        self,
        seed: str,
        limit: int | None = None,
        min_success_rate: float = 1.0,
        include_unstable: bool = False,
    ) -> tuple[list[SourceCapabilityResult], Counter]:
        sources = self.db.list_sources(limit=limit)
        workflow_event(
            "filter_sources_start",
            seed=seed,
            source_count=len(sources),
            min_success_rate=min_success_rate,
            include_unstable=include_unstable,
        )
        results: list[SourceCapabilityResult] = []
        stats: Counter = Counter()
        for source in sources:
            result = self.evaluate_source(source, seed, min_success_rate, include_unstable)
            self.db.save_source_capability(result.source_id, result.to_record())
            self.db.update_source_health(
                result.source_id,
                "ok" if result.passed else ("unsupported" if result.requires_webview else "error"),
                result.last_error,
            )
            results.append(result)
            stats["passed" if result.passed else "failed"] += 1
            if result.supports_search and not result.supports_content:
                stats["search_available_not_sampleable"] += 1
            if result.supports_search and not result.detects_completion:
                stats["completion_unknown"] += 1
            if result.last_error_type:
                stats[f"error:{result.last_error_type}"] += 1
        workflow_event("filter_sources_end", stats=dict(stats))
        return results, stats

    def evaluate_source(
        self,
        source: dict,
        seed: str,
        min_success_rate: float,
        include_unstable: bool,
    ) -> SourceCapabilityResult:
        sid = source_id(source)
        name = source.get("bookSourceName") or sid
        result = SourceCapabilityResult(source_id=sid, source_name=name)
        supported, reason = source_support_status(source)
        if not supported:
            result.requires_webview = "js" in reason or "webview" in reason or "captcha" in reason
            result.last_error_type = "unsupported"
            result.last_error = reason
            source_event("source_filter_unsupported", "WARNING", source_id=sid, source_name=name, reason=reason)
            return result

        stages = 5
        passed_stages = 0
        try:
            candidates = self.legado.search(source, seed)
            result.supports_search = bool(candidates)
            if not candidates:
                raise SourceFilterError("empty_search_results")
            passed_stages += 1

            candidate = self._best_candidate(source, candidates)
            result.returns_metadata = bool(candidate.title and candidate.author and (candidate.intro or candidate.kind))
            if result.returns_metadata:
                passed_stages += 1
            result.detects_completion = bool(candidate.completion_evidence)
            if result.detects_completion:
                passed_stages += 1

            chapters = self.legado.chapters(source, candidate, limit=1)
            result.supports_toc = bool(chapters)
            if not chapters:
                raise SourceFilterError("empty_toc")
            passed_stages += 1

            content = self.legado.chapter_content(source, chapters[0])
            result.supports_content = bool(content)
            if not content:
                raise SourceFilterError("empty_content")
            passed_stages += 1

        except UnsupportedSourceError as exc:
            result.requires_webview = True
            result.last_error_type = "unsupported"
            result.last_error = str(exc)
        except Exception as exc:
            result.last_error_type = normalize_error_type(exc)
            result.last_error = str(exc)[:500]

        result.success_rate = passed_stages / stages
        result.unstable = 0 < result.success_rate < min_success_rate
        result.passed = (
            result.supports_search
            and result.returns_metadata
            and result.detects_completion
            and result.supports_toc
            and result.supports_content
            and result.success_rate >= min_success_rate
            and (include_unstable or not result.unstable)
        )
        source_event(
            "source_filter_result",
            source_id=sid,
            source_name=name,
            passed=result.passed,
            success_rate=round(result.success_rate, 3),
            error_type=result.last_error_type,
        )
        return result

    def _best_candidate(self, source: dict, candidates: list[NovelCandidate]) -> NovelCandidate:
        for candidate in candidates:
            try:
                enriched = self.legado.enrich_book_info(source, candidate)
            except Exception:
                enriched = candidate
            if enriched.completed or enriched.completion_evidence:
                return enriched
        return candidates[0]


class SourceFilterError(RuntimeError):
    pass


def normalize_error_type(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc).lower()
    if "timeout" in name.lower() or "timeout" in text:
        return "timeout"
    if "403" in text or "forbidden" in text:
        return "forbidden"
    if "empty_search" in text:
        return "empty_search"
    if "empty_toc" in text:
        return "empty_toc"
    if "empty_content" in text:
        return "empty_content"
    return name
