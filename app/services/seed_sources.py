from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FeedSubscription, NewsSource
from app.db.session import SessionLocal

SEED_DATA = [
    {
        "name": "BBC News",
        "language": "en",
        "country": "United Kingdom",
        "homepage_url": "https://www.bbc.com/news",
        "feeds": [
            (
                "BBC World News",
                "https://feeds.bbci.co.uk/news/world/rss.xml",
                "world",
                "en",
            ),
            (
                "BBC Technology",
                "https://feeds.bbci.co.uk/news/technology/rss.xml",
                "technology",
                "en",
            ),
        ],
    },
    {
        "name": "The Guardian",
        "language": "en",
        "country": "United Kingdom",
        "homepage_url": "https://www.theguardian.com/international",
        "feeds": [
            (
                "The Guardian World",
                "https://www.theguardian.com/world/rss",
                "world",
                "en",
            ),
            (
                "The Guardian Technology",
                "https://www.theguardian.com/technology/rss",
                "technology",
                "en",
            ),
        ],
    },
    {
        "name": "Le Monde",
        "language": "fr",
        "country": "France",
        "homepage_url": "https://www.lemonde.fr",
        "feeds": [
            (
                "Le Monde International",
                "https://www.lemonde.fr/international/rss_full.xml",
                "world",
                "fr",
            ),
            (
                "Le Monde Économie",
                "https://www.lemonde.fr/economie/rss_full.xml",
                "economy",
                "fr",
            ),
        ],
    },
    {
        "name": "France 24",
        "language": "fr",
        "country": "France",
        "homepage_url": "https://www.france24.com/fr/",
        "feeds": [
            (
                "France 24 Actualités",
                "https://www.france24.com/fr/rss",
                "world",
                "fr",
            ),
        ],
    },
    {
        "name": "RFI",
        "language": "fr",
        "country": "France",
        "homepage_url": "https://www.rfi.fr/fr/",
        "feeds": [
            ("RFI Actualités", "https://www.rfi.fr/fr/rss", "world", "fr"),
        ],
    },
]


def seed(session: Session, *, default_enabled: bool = True) -> int:
    count = 0
    for item in SEED_DATA:
        source = session.scalar(
            select(NewsSource).where(NewsSource.name == item["name"])
        )
        if source is None:
            source = NewsSource(
                name=item["name"],
                language=item["language"],
                country=item["country"],
                homepage_url=item["homepage_url"],
            )
            session.add(source)
            session.flush()
        for title, url, category, language in item["feeds"]:
            feed = session.scalar(
                select(FeedSubscription).where(
                    FeedSubscription.feed_url == url
                )
            )
            if feed is None:
                feed = FeedSubscription(
                    source_id=source.id,
                    title=title,
                    feed_url=url,
                    category=category,
                    language=language,
                    is_enabled=default_enabled,
                )
                session.add(feed)
                count += 1
            else:
                feed.source_id = source.id
                feed.title = title
                feed.category = category
                feed.language = language
    session.commit()
    return count


def seed_all_candidates(session: Session) -> int:
    return seed(session, default_enabled=False)


def main() -> None:
    with SessionLocal() as session:
        count = seed(session)
    print(f"Seeded initial RSS sources and feed subscriptions ({count} new).")


if __name__ == "__main__":
    main()
