from __future__ import annotations

import argparse
import sys
import time

from .config import Settings, load_settings
from .db import Database
from .discovery import DiscoveryService
from .http import HttpClient
from .initial_profile import InitialProfileError, InitialProfileService, MissingNovelsError, ensure_minimum_novels
from .legado import LegadoClient
from .llm import LLMClient
from .logger import configure_logging, workflow_event
from .recommender import RecommendationService
from .sampling import SamplingService
from .sources import sync_sources


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    log_level = args.log_level or settings.log_level
    configure_logging(
        settings.log_dir,
        level=log_level,
        max_file_bytes=settings.log_max_file_bytes,
        backups=settings.log_backups,
        max_dir_bytes=settings.log_max_dir_bytes,
    )
    started = time.monotonic()
    workflow_event("command_start", command=args.command, args=vars(args), log_level=log_level)

    db = Database(settings.db_path)
    http = HttpClient(settings.request_timeout, settings.user_agent)
    legado = LegadoClient(http)
    llm = LLMClient(settings)

    try:
        if args.command not in {None, "clear"}:
            ensure_minimum_novels(settings.novels_dir)
        code = run_command(args, parser, settings, db, http, legado, llm)
        workflow_event(
            "command_end",
            command=args.command,
            exit_code=code,
            duration_seconds=round(time.monotonic() - started, 3),
        )
        return code
    except MissingNovelsError as exc:
        workflow_event(
            "command_blocked",
            "WARNING",
            command=args.command,
            reason="missing_local_novels",
            error=str(exc),
        )
        print(exc)
        return 1
    except Exception as exc:
        workflow_event(
            "command_failed",
            "ERROR",
            command=args.command,
            error_type=type(exc).__name__,
            error=str(exc),
            duration_seconds=round(time.monotonic() - started, 3),
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel-selector")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override NOVEL_SELECTOR_LOG_LEVEL for this run.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize the database and build the initial preference profile.")

    clear = sub.add_parser("clear", help="Delete local database state and reset this app.")
    clear.add_argument("--yes", action="store_true", help="Skip confirmation.")

    sync = sub.add_parser("sync-sources", help="Download and cache Legado sources.")
    sync.add_argument("--url", help="Override the Legado source JSON URL.")

    discover = sub.add_parser("discover", help="Explore completed novel candidates.")
    discover.add_argument("--limit", type=int, default=100, help="Number of completed candidates to collect.")
    discover.add_argument("--source-limit", type=int, help="Limit number of sources for quick testing.")
    discover.add_argument("--max-searches", type=int, default=80, help="Maximum source/seed searches per run.")
    discover.add_argument("--seed", action="append", help="Manual search seed. Can be passed multiple times.")

    sample = sub.add_parser("sample", help="Fetch the first chapters for completed candidates.")
    sample.add_argument("--limit", type=int, default=30, help="Number of novels to sample.")
    sample.add_argument("--chapters", type=int, default=10, help="Number of first chapters to fetch.")

    recommend = sub.add_parser("recommend", help="Ask the LLM to recommend sampled novels.")
    recommend.add_argument("--k", type=int, default=5, help="Number of recommendations to show.")
    recommend.add_argument("--pool-size", type=int, default=30, help="Number of sampled novels to score.")

    sub.add_parser("feedback", help="Record feedback for the latest recommendation run.")
    return parser


def run_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    settings: Settings,
    db: Database,
    http: HttpClient,
    legado: LegadoClient,
    llm: LLMClient,
) -> int:
    if args.command == "clear":
        return clear_database(settings, args.yes)

    if args.command == "init":
        db.init()
        workflow_event("database_initialized", db_path=settings.db_path)
        if db.has_preference_events():
            print("已经初始化过偏好画像；如需重建请先执行 clear。")
            workflow_event("initial_profile_skipped", reason="already_initialized")
            return 0
        try:
            profile = InitialProfileService(db, llm, settings.novels_dir, settings.context_window).initialize()
        except InitialProfileError as exc:
            print(exc)
            workflow_event("initial_profile_failed", "WARNING", reason=str(exc))
            return 1
        print(f"Initialized database: {settings.db_path}")
        print(f"Initial preference profile created. chars={len(profile)}")
        return 0

    db.init()
    if args.command == "sync-sources":
        count = sync_sources(db, http, args.url or settings.source_url)
        workflow_event("sync_sources_summary", source_count=count, db_path=settings.db_path)
        print(f"Synced {count} sources into {settings.db_path}")
        return 0

    if args.command == "discover":
        found, candidates, stats = DiscoveryService(db, legado, llm).discover(
            args.limit,
            args.source_limit,
            args.max_searches,
            args.seed,
        )
        workflow_event("discover_summary", found=found, completed_candidates=candidates, stats=dict(stats))
        print(f"Discovery finished: found={found}, completed_candidates={candidates}")
        for key, value in stats.most_common():
            print(f"  {key}: {value}")
        return 0

    if args.command == "sample":
        saved, failed = SamplingService(db, legado).sample(args.limit, args.chapters)
        workflow_event("sample_summary", saved=saved, failed=failed)
        print(f"Sampling finished: saved={saved}, failed={failed}")
        return 0

    if args.command == "recommend":
        had_pool = bool(db.recommendable_samples(args.pool_size))
        run_id, recs = RecommendationService(db, llm).recommend(args.k, args.pool_size)
        workflow_event("recommend_summary", run_id=run_id, recommendation_count=len(recs), had_pool=had_pool)
        if not recs:
            if had_pool:
                print("Sampled candidates exist, but the LLM returned no recommendations.")
            else:
                print("No sampled candidates available. Run discover and sample first.")
            return 1
        print(f"Recommendation run #{run_id}")
        rows = {row["novel_id"]: row for row in db.latest_recommendations()}
        for rank, rec in enumerate(recs, start=1):
            row = rows.get(rec.novel_id)
            title = row["title"] if row else f"novel:{rec.novel_id}"
            author = row["author"] if row else ""
            print(f"\n{rank}. {title} - {author} [{rec.score:.1f}]")
            print(f"推荐理由：{rec.reason}")
            print(f"风险点：{rec.risks}")
            print(f"文风：{rec.style}")
            print(f"节奏：{rec.pacing}")
            print(f"结论：{rec.verdict}")
        return 0

    if args.command == "feedback":
        return feedback(db)

    parser.print_help()
    return 1


def feedback(db: Database) -> int:
    rows = db.latest_recommendations()
    if not rows:
        print("No recommendation run found.")
        return 1
    print("Latest recommendations:")
    for row in rows:
        print(f"{row['rank']}. [{row['novel_id']}] {row['title']} - {row['author']}")
    raw = input("输入选中的 novel_id，多个用逗号分隔；如果都不选，直接回车：").strip()
    selected_ids = {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
    saved = 0
    for row in rows:
        selected = int(row["novel_id"]) in selected_ids
        prompt = "选择理由" if selected else "跳过理由（可空）"
        reason = input(f"{prompt} - {row['title']}：").strip()
        if selected or reason:
            db.add_feedback(int(row["novel_id"]), selected, reason)
            saved += 1
    workflow_event("feedback_summary", selected_count=len(selected_ids), feedback_saved=saved)
    print("Feedback saved.")
    return 0


def clear_database(settings: Settings, assume_yes: bool) -> int:
    db_path = settings.db_path
    if not assume_yes:
        answer = input(f"确认删除本地数据库 {db_path}？这会清空偏好画像和推荐记录，但不会删除 novels/ 或 logs/。（输入 yes 确认）：")
        if answer.strip().lower() != "yes":
            print("已取消。")
            return 1
    removed = []
    for path in [db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")]:
        if path.exists():
            path.unlink()
            removed.append(str(path))
    workflow_event("database_cleared", db_path=db_path, removed=removed)
    print("数据库已清理，当前状态已重置为未初始化。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
