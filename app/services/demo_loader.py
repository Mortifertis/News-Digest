from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Article, FeedSubscription, NewsSource
from app.services.cluster_service import cluster_articles
from app.services.normalizer import normalize_article_fields

FIXTURE_PATH = Path("fixtures/demo_articles.json")


def reset_article_data(session: Session) -> None:
    from app.db.models import ClusterArticle, StoryCluster

    session.query(ClusterArticle).delete()
    session.query(StoryCluster).delete()
    session.query(Article).delete()
    session.commit()


def _source_and_feed(
    session: Session, name: str, language: str
) -> tuple[int, int]:
    source = session.scalar(select(NewsSource).where(NewsSource.name == name))
    if source is None:
        source = NewsSource(
            name=name,
            language=language,
            country="Demo",
            homepage_url="https://demo.local",
        )
        session.add(source)
        session.flush()
    feed_url = f"https://demo.local/{name.lower().replace(' ', '-')}.xml"
    feed = session.scalar(
        select(FeedSubscription).where(FeedSubscription.feed_url == feed_url)
    )
    if feed is None:
        feed = FeedSubscription(
            source_id=source.id,
            title=f"{name} Demo",
            feed_url=feed_url,
            category="demo",
            language=language,
            is_enabled=False,
        )
        session.add(feed)
        session.flush()
    else:
        feed.is_enabled = False
    return source.id, feed.id


def load_demo_articles(session: Session, *, reset: bool = True) -> int:
    if reset:
        reset_article_data(session)
    data = json.loads(FIXTURE_PATH.read_text())
    created = 0
    for item in data:
        source_id, feed_id = _source_and_feed(
            session, item["source_name"], item["language"]
        )
        fields = normalize_article_fields(
            item["title"], item["summary"], item["url"]
        )
        exists = session.scalar(
            select(Article.id).where(
                Article.feed_id == feed_id,
                Article.external_id == item["external_id"],
            )
        )
        if exists is not None:
            continue
        session.add(
            Article(
                source_id=source_id,
                feed_id=feed_id,
                external_id=item["external_id"],
                title=fields["title"],
                summary=fields["summary"],
                canonical_url=fields["canonical_url"],
                published_at=None,
                language=item["language"],
                normalized_title=fields["normalized_title"],
                normalized_summary=fields["normalized_summary"],
                text_hash=fields["text_hash"],
                raw_payload_json=json.dumps(item),
            )
        )
        created += 1
    session.commit()
    cluster_articles(session)
    return created
