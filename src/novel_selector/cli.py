from __future__ import annotations

import argparse
import sys

from .config import load_settings
from .db import Database
from .discovery import DiscoveryService
from .http import HttpClient
from .legado import LegadoClient
from .llm import LLMClient
from .recommender import RecommendationService
from .sampling import SamplingService
from .sources import sync_sources


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    db = Database(settings.db_path)
    http = HttpClient(settings.request_timeout, settings.user_agent)
    legado = LegadoClient(http)
    llm = LLMClient(settings)

    if args.command == "init":
        db.init()
        print(f"Initialized database: {settings.db_path}")
        return 0

    db.init()
    if args.command == "sync-sources":
        count = sync_sources(db, http, args.url or settings.source_url)
        print(f"Synced {count} sources into {settings.db_path}")
        return 0

    if args.command == "discover":
        found, candidates, stats = DiscoveryService(db, legado, llm).discover(
            args.limit,
            args.source_limit,
            args.max_searches,
            args.seed,
        )
        print(f"Discovery finished: found={found}, completed_candidates={candidates}")
        for key, value in stats.most_common():
            print(f"  {key}: {value}")
        return 0

    if args.command == "sample":
        saved, failed = SamplingService(db, legado).sample(args.limit, args.chapters)
        print(f"Sampling finished: saved={saved}, failed={failed}")
        return 0

    if args.command == "recommend":
        had_pool = bool(db.recommendable_samples(args.pool_size))
        run_id, recs = RecommendationService(db, llm).recommend(args.k, args.pool_size)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel-selector")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize the local SQLite database.")

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
    for row in rows:
        selected = int(row["novel_id"]) in selected_ids
        prompt = "选择理由" if selected else "跳过理由（可空）"
        reason = input(f"{prompt} - {row['title']}：").strip()
        if selected or reason:
            db.add_feedback(int(row["novel_id"]), selected, reason)
    print("Feedback saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
