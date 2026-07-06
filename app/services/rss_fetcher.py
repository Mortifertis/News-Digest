from __future__ import annotations

import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Article, FeedSubscription
from app.services.normalizer import normalize_article_fields


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def entry_id(entry: dict, canonical_url: str) -> str:
    return str(entry.get("id") or entry.get("guid") or canonical_url)


def fetch_enabled_feeds(session: Session) -> int:
    feeds = session.scalars(
        select(FeedSubscription).where(FeedSubscription.is_enabled.is_(True))
    ).all()
    created = 0
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for feed in feeds:
            try:
                response = client.get(feed.feed_url)
                response.raise_for_status()
                parsed = feedparser.parse(response.content)
                if parsed.bozo:
                    print(f"Feed warning for {feed.feed_url}: {parsed.bozo_exception}")
                for entry in parsed.entries:
                    fields = normalize_article_fields(
                        entry.get("title"),
                        entry.get("summary") or entry.get("description"),
                        entry.get("link"),
                    )
                    if not fields["title"] or not fields["canonical_url"]:
                        continue
                    article = Article(
                        source_id=feed.source_id,
                        feed_id=feed.id,
                        external_id=entry_id(entry, fields["canonical_url"]),
                        title=fields["title"],
                        summary=fields["summary"],
                        canonical_url=fields["canonical_url"],
                        published_at=parse_datetime(entry.get("published")),
                        language=feed.language,
                        normalized_title=fields["normalized_title"],
                        normalized_summary=fields["normalized_summary"],
                        text_hash=fields["text_hash"],
                        raw_payload_json=json.dumps(dict(entry), default=str),
                    )
                    session.add(article)
                    try:
                        session.flush()
                    except IntegrityError:
                        session.rollback()
                    else:
                        created += 1
                feed.last_fetched_at = datetime.now(UTC)
                session.commit()
            except Exception as exc:
                session.rollback()
                print(f"Failed to fetch {feed.feed_url}: {exc}")
    return created
