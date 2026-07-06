import argparse

from app.db.session import SessionLocal
from app.services.cluster_service import cluster_articles
from app.services.rss_fetcher import fetch_enabled_feeds


def main() -> None:
    parser = argparse.ArgumentParser(prog="morti-news-digest")
    parser.add_argument("command", choices=("fetch", "cluster"))
    args = parser.parse_args()
    with SessionLocal() as session:
        if args.command == "fetch":
            count = fetch_enabled_feeds(session)
            print(f"Saved {count} new articles.")
        if args.command == "cluster":
            count = cluster_articles(session)
            print(f"Clustered {count} articles.")


if __name__ == "__main__":
    main()
