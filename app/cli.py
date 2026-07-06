import argparse
import json
from itertools import combinations
from pathlib import Path

from rapidfuzz import fuzz

from app.db.session import SessionLocal
from app.services.cluster_service import cluster_articles
from app.services.demo_loader import load_demo_articles, reset_article_data
from app.services.normalizer import normalize_article_fields
from app.services.rss_fetcher import FeedFetchResult, fetch_enabled_feeds
from app.services.stats_service import collect_stats


def print_fetch_result(result: FeedFetchResult) -> None:
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


def print_stats(stats: dict) -> None:
    print(f"Total sources: {stats['total_sources']}")
    print(f"Total feeds: {stats['total_feeds']}")
    print(f"Enabled feeds: {stats['enabled_feeds']}")
    print(f"Successful feeds: {stats['successful_feeds']}")
    print(f"Failed feeds: {stats['failed_feeds']}")
    print(f"Total articles: {stats['total_articles']}")
    print(f"Total clusters: {stats['total_clusters']}")
    print("Articles per source:")
    for name, count in stats["articles_per_source"]:
        print(f"  {name}: {count}")
    print("Articles per language:")
    for language, count in stats["articles_per_language"]:
        print(f"  {language}: {count}")
    print("Clusters per language:")
    for language, count in stats["clusters_per_language"]:
        print(f"  {language}: {count}")
    print(f"Singleton clusters count: {stats['singleton_clusters']}")
    print(f"Multi-article clusters count: {stats['multi_article_clusters']}")
    print(f"Multi-source clusters count: {stats['multi_source_clusters']}")
    print(
        "Average articles per cluster: "
        f"{stats['average_articles_per_cluster']:.2f}"
    )
    print("Top 10 largest clusters:")
    for cluster_id, title, language, articles, sources in stats[
        "top_clusters"
    ]:
        print(
            f"  #{cluster_id} [{language}] {articles} articles, "
            f"{sources} sources — {title}"
        )


def run_fetch() -> int:
    with SessionLocal() as session:
        results = fetch_enabled_feeds(session)
    for result in results:
        print_fetch_result(result)
    return sum(result.new_articles_count for result in results)


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
            left_id, left_text = left
            right_id, right_text = right
            score = fuzz.token_set_ratio(left_text, right_text)
            print(f"  {left_id} <> {right_id}: {score:.1f}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="morti-news-digest")
    parser.add_argument(
        "command",
        choices=(
            "fetch",
            "cluster",
            "stats",
            "reset-data",
            "refetch",
            "load-demo",
            "demo-scores",
        ),
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not reset article/cluster data before load-demo.",
    )
    args = parser.parse_args()
    if args.command == "fetch":
        count = run_fetch()
        print(f"Saved {count} new articles.")
    elif args.command == "cluster":
        count = run_cluster()
        print(f"Clustered {count} articles.")
    elif args.command == "stats":
        run_stats()
    elif args.command == "reset-data":
        with SessionLocal() as session:
            reset_article_data(session)
        print("Deleted articles, story_clusters, and cluster_articles.")
    elif args.command == "refetch":
        run_fetch()
        count = run_cluster()
        print(f"Clustered {count} articles.")
        run_stats()
    elif args.command == "load-demo":
        with SessionLocal() as session:
            count = load_demo_articles(session, reset=not args.no_reset)
        print(f"Loaded {count} demo articles.")
        run_stats()
    elif args.command == "demo-scores":
        run_demo_scores()


if __name__ == "__main__":
    main()
