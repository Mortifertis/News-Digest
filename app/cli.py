import argparse
import json
import time
from itertools import combinations
from pathlib import Path

from rapidfuzz import fuzz
from sqlalchemy import select

from app.db.models import AppSetting, FeedSubscription, FetchRun
from app.db.session import SessionLocal
from app.services.cluster_service import cluster_articles
from app.services.demo_loader import load_demo_articles, reset_article_data
from app.services.normalizer import normalize_article_fields
from app.services.rss_fetcher import (
    FeedFetchStats,
    fetch_enabled_feeds,
    test_feed_by_id,
)
from app.services.seed_sources import seed, seed_all_candidates
from app.services.stats_service import collect_stats

ALLOWED_INTERVALS = {0, 60, 180, 360, 720, 1440}


def print_fetch_result(result: FeedFetchStats) -> None:
    print("-" * 72)
    print(f"Source: {result.source_name}")
    print(f"Feed: {result.feed_title}")
    print(f"URL: {result.feed_url}")
    print(f"HTTP status: {result.http_status or 'n/a'}")
    print(f"Parsed entries: {result.entries_count}")
    print(f"New articles saved: {result.new_articles_count}")
    print(f"Skipped existing articles: {result.skipped_articles_count}")
    print(f"Status: {result.status}")
    if result.error:
        print(f"Error: {result.error}")


def print_run_summary(run: FetchRun) -> None:
    print(f"Fetch run #{run.id} finished: {run.status}")
    print(f"Enabled feeds: {run.total_feeds}")
    print(f"Successful: {run.successful_feeds}")
    print(f"Failed: {run.failed_feeds}")
    print(f"New articles: {run.total_new_articles}")
    print(f"Skipped existing: {run.total_skipped_articles}")
    print(f"Total entries parsed: {run.total_entries}")
    failed = [item for item in run.feed_results if item.status == "failed"]
    if failed:
        print("\nFailed feeds:")
        for item in failed:
            print(f"- {item.feed_title}: {item.error}")


def print_stats(stats: dict) -> None:
    print(f"Total sources: {stats['total_sources']}")
    print(f"Total feeds: {stats['total_feeds']}")
    print(f"Enabled feeds: {stats['enabled_feeds']}")
    print(f"Successful feeds: {stats['successful_feeds']}")
    print(f"Failed feeds: {stats['failed_feeds']}")
    print(f"Total articles: {stats['total_articles']}")
    print(f"Total clusters: {stats['total_clusters']}")


def _feed_by_title(session, title: str) -> FeedSubscription:
    feed = session.scalar(
        select(FeedSubscription).where(FeedSubscription.title == title)
    )
    if feed is None:
        raise SystemExit(f"Feed not found: {title}")
    return feed


def run_fetch(mode: str = "cli") -> int:
    with SessionLocal() as session:
        run = fetch_enabled_feeds(session, mode=mode)
        print_run_summary(run)
        return run.total_new_articles


def run_cluster() -> int:
    with SessionLocal() as session:
        return cluster_articles(session)


def run_stats() -> None:
    with SessionLocal() as session:
        print_stats(collect_stats(session))


def run_demo_scores() -> None:
    fixture_path = Path("fixtures/demo_articles.json")
    articles = json.loads(fixture_path.read_text())
    by_language: dict[str, list[tuple[str, str]]] = {}
    for item in articles:
        fields = normalize_article_fields(
            item["title"], item["summary"], item["url"]
        )
        text = (
            f"{fields['normalized_title']} {fields['normalized_summary']}"
        ).strip()
        by_language.setdefault(item["language"], []).append(
            (item["external_id"], text)
        )
    for language, language_articles in sorted(by_language.items()):
        print(f"Language: {language}")
        for left, right in combinations(language_articles, 2):
            score = fuzz.token_set_ratio(left[1], right[1])
            print(f"  {left[0]} <> {right[0]}: {score:.1f}")


def set_fetch_interval(value: int) -> None:
    if value not in ALLOWED_INTERVALS:
        raise SystemExit(
            "Invalid interval. Allowed: 0, 60, 180, 360, 720, 1440"
        )
    with SessionLocal() as session:
        setting = session.get(AppSetting, "fetch_interval_minutes")
        if setting is None:
            setting = AppSetting(
                key="fetch_interval_minutes", value=str(value)
            )
            session.add(setting)
        else:
            setting.value = str(value)
        session.commit()
    print(f"fetch_interval_minutes set to {value}")


def get_fetch_interval() -> int:
    with SessionLocal() as session:
        setting = session.get(AppSetting, "fetch_interval_minutes")
        return int(setting.value) if setting else 0


def run_scheduler() -> None:
    interval = get_fetch_interval()
    if interval == 0:
        print("Scheduler disabled: manual only")
        return
    print(f"Scheduler enabled: every {interval} minutes")
    try:
        while True:
            try:
                run_fetch(mode="scheduled")
                count = run_cluster()
                print(f"Clustered {count} articles.")
            except Exception as exc:
                print(f"Scheduler iteration failed: {exc}")
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        print("Scheduler stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="morti-news-digest")
    parser.add_argument("command")
    parser.add_argument("value", nargs="?")
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not reset article/cluster data before load-demo.",
    )
    args = parser.parse_args()
    if args.command == "fetch":
        run_fetch()
    elif args.command == "cluster":
        count = run_cluster()
        print(f"Clustered {count} articles.")
        print(f"Clustered {run_cluster()} articles.")
    elif args.command == "stats":
        run_stats()
    elif args.command == "reset-data":
        with SessionLocal() as session:
            reset_article_data(session)
        print("Deleted articles, story_clusters, and cluster_articles.")
    elif args.command == "refetch":
        run_fetch()
        print(f"Clustered {run_cluster()} articles.")
        run_stats()
    elif args.command == "load-demo":
        with SessionLocal() as session:
            count = load_demo_articles(session, reset=not args.no_reset)
        print(f"Loaded {count} demo articles.")
        run_stats()
    elif args.command == "demo-scores":
        run_demo_scores()
    elif args.command == "seed-all-candidates":
        with SessionLocal() as session:
            count = seed_all_candidates(session)
        print(f"Seeded all candidate feeds ({count} new).")
    elif args.command == "seed-accessible-sources":
        with SessionLocal() as session:
            count = seed(session)
        print(f"Seeded accessible source defaults ({count} new).")
    elif args.command == "enable-feed":
        with SessionLocal() as session:
            feed = _feed_by_title(session, args.value or "")
            feed.is_enabled = True
            session.commit()
        print(f"Enabled feed: {args.value}")
    elif args.command == "disable-feed":
        with SessionLocal() as session:
            feed = _feed_by_title(session, args.value or "")
            feed.is_enabled = False
            session.commit()
        print(f"Disabled feed: {args.value}")
    elif args.command == "test-feed":
        with SessionLocal() as session:
            feed = _feed_by_title(session, args.value or "")
            print_fetch_result(test_feed_by_id(session, feed.id))
    elif args.command == "set-fetch-interval":
        set_fetch_interval(int(args.value or ""))
    elif args.command == "scheduler":
        run_scheduler()
    else:
        raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
