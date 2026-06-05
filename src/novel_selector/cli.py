from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

from .config import Settings, load_settings
from .db import Database
from .diagnostics import doctor_has_failures, doctor_text, profile_text, status_text
from .discovery import DiscoveryService
from .http import HttpClient
from .initial_profile import InitialProfileError, InitialProfileService, MissingNovelsError, ensure_minimum_novels
from .interactive import InteractiveCommand, run_interactive_shell
from .legado import LegadoClient
from .llm import LLMClient
from .logger import configure_logging, workflow_event
from .recommender import RecommendationService
from .sampling import SamplingService
from .sources import sync_sources


BYPASS_NOVEL_CHECK = {"clear", "status", "show-profile", "doctor"}


@dataclass(frozen=True)
class CommandInfo:
    name: str
    description: str


COMMANDS = [
    CommandInfo("init", "初始化数据库并构建初始画像"),
    CommandInfo("clear", "清理数据库，重置为未初始化状态"),
    CommandInfo("sync-sources", "同步 Legado/阅读书源"),
    CommandInfo("discover", "发现明确完本候选小说"),
    CommandInfo("sample", "抓取候选小说试读"),
    CommandInfo("recommend", "让 LLM 推荐已采样候选"),
    CommandInfo("feedback", "记录推荐反馈"),
    CommandInfo("status", "查看数据库、书源、候选、采样、画像状态"),
    CommandInfo("show-profile", "查看当前偏好画像"),
    CommandInfo("doctor", "检查 .env、LLM、novels、数据库和日志目录"),
]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    settings = load_settings()
    log_level = _extract_log_level(argv) or settings.log_level
    configure_logging(
        settings.log_dir,
        level=log_level,
        max_file_bytes=settings.log_max_file_bytes,
        backups=settings.log_backups,
        max_dir_bytes=settings.log_max_dir_bytes,
    )

    if not argv:
        return start_interactive(settings, parser, log_level)

    args = parser.parse_args(argv)
    if args.command is None:
        return start_interactive(settings, parser, log_level)
    return execute_command(args, parser, settings, log_level, interactive=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel-selector")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override NOVEL_SELECTOR_LOG_LEVEL for this run.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help=_description("init"))

    clear = sub.add_parser("clear", help=_description("clear"))
    clear.add_argument("--yes", action="store_true", help="Skip confirmation.")

    sync = sub.add_parser("sync-sources", help=_description("sync-sources"))
    sync.add_argument("--url", help="Override the Legado source JSON URL.")

    discover = sub.add_parser("discover", help=_description("discover"))
    discover.add_argument("--limit", type=int, default=100, help="Number of completed candidates to collect.")
    discover.add_argument("--source-limit", type=int, help="Limit number of sources for quick testing.")
    discover.add_argument("--max-searches", type=int, default=80, help="Maximum source/seed searches per run.")
    discover.add_argument("--seed", action="append", help="Manual search seed. Can be passed multiple times.")

    sample = sub.add_parser("sample", help=_description("sample"))
    sample.add_argument("--limit", type=int, default=30, help="Number of novels to sample.")
    sample.add_argument("--chapters", type=int, default=10, help="Number of first chapters to fetch.")

    recommend = sub.add_parser("recommend", help=_description("recommend"))
    recommend.add_argument("--k", type=int, default=5, help="Number of recommendations to show.")
    recommend.add_argument("--pool-size", type=int, default=30, help="Number of sampled novels to score.")

    sub.add_parser("feedback", help=_description("feedback"))
    sub.add_parser("status", help=_description("status"))
    sub.add_parser("show-profile", help=_description("show-profile"))
    sub.add_parser("doctor", help=_description("doctor"))
    return parser


def start_interactive(settings: Settings, parser: argparse.ArgumentParser, log_level: str) -> int:
    return run_interactive_shell(
        settings,
        parser,
        [InteractiveCommand(item.name, item.description) for item in COMMANDS]
        + [InteractiveCommand("help", "查看命令说明"), InteractiveCommand("exit", "退出交互模式")],
        lambda args: execute_command(args, parser, settings, log_level, interactive=True),
    )


def execute_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    settings: Settings,
    log_level: str,
    interactive: bool,
) -> int:
    started = time.monotonic()
    workflow_event("command_start", command=args.command, args=vars(args), log_level=log_level)
    db = Database(settings.db_path)
    http = HttpClient(settings.request_timeout, settings.user_agent)
    legado = LegadoClient(http)
    llm = LLMClient(settings)
    try:
        if args.command not in {None, *BYPASS_NOVEL_CHECK}:
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
        workflow_event("command_blocked", "WARNING", command=args.command, reason="missing_local_novels", error=str(exc))
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
        if interactive:
            print(f"Command failed: {exc}")
            return 1
        raise


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

    if args.command == "status":
        print(status_text(settings, db))
        return 0

    if args.command == "show-profile":
        profile = profile_text(db)
        if not profile:
            print("尚未生成偏好画像，请先执行 `novel-selector init`。")
            return 1
        print(profile)
        return 0

    if args.command == "doctor":
        print(doctor_text(settings, db))
        return 1 if doctor_has_failures(settings, db) else 0

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
        answer = input(
            f"确认删除本地数据库 {db_path}？这会清空偏好画像和推荐记录，但不会删除 novels/ 或 logs/。（输入 yes 确认）："
        )
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


def _description(command: str) -> str:
    return next(item.description for item in COMMANDS if item.name == command)


def _extract_log_level(argv: list[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--log-level" and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith("--log-level="):
            return value.split("=", 1)[1]
    return None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
