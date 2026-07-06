from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Article, FeedSubscription
from app.services.normalizer import normalize_article_fields


@dataclass
class FeedFetchResult:
    source_name: str
    feed_title: str
    feed_url: str
    http_status: int | None = None
    entries_count: int = 0
    new_articles_count: int = 0
    skipped_articles_count: int = 0
    status: str = "never"
    error: str | None = None


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


def _save_entry(session: Session, feed: FeedSubscription, entry: dict) -> bool:
    fields = normalize_article_fields(
        entry.get("title"),
        entry.get("summary") or entry.get("description"),
        entry.get("link"),
    )
    if not fields["title"] or not fields["canonical_url"]:
        return False
    external_id = entry_id(entry, fields["canonical_url"])
    exists = session.scalar(
        select(Article.id).where(
            Article.feed_id == feed.id,
            Article.external_id == external_id,
        )
    )
    if exists is not None:
        return False
    article = Article(
        source_id=feed.source_id,
        feed_id=feed.id,
        external_id=external_id,
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
    session.flush()
    return True


def update_feed_status(
    feed: FeedSubscription, result: FeedFetchResult
) -> None:
    feed.last_fetch_status = result.status
    feed.last_fetch_error = result.error
    feed.last_http_status = result.http_status
    feed.last_entries_count = result.entries_count
    feed.last_new_articles_count = result.new_articles_count
    feed.last_skipped_articles_count = result.skipped_articles_count
    feed.last_fetched_at = datetime.now(UTC)


def fetch_feed(
    session: Session, client: httpx.Client, feed: FeedSubscription
) -> FeedFetchResult:
    result = FeedFetchResult(
        source_name=feed.source.name,
        feed_title=feed.title,
        feed_url=feed.feed_url,
    )
    try:
        response = client.get(feed.feed_url)
        result.http_status = response.status_code
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        result.entries_count = len(parsed.entries)
        if parsed.bozo:
            result.error = f"Feed parse warning: {parsed.bozo_exception}"
        for entry in parsed.entries:
            if _save_entry(session, feed, entry):
                result.new_articles_count += 1
            else:
                result.skipped_articles_count += 1
        result.status = "success"
    except Exception as exc:
        session.rollback()
        result.status = "failed"
        result.error = str(exc)
    update_feed_status(feed, result)
    session.commit()
    return result


def fetch_enabled_feeds(session: Session) -> list[FeedFetchResult]:
    feeds = session.scalars(
        select(FeedSubscription)
        .options(joinedload(FeedSubscription.source))
        .where(FeedSubscription.is_enabled.is_(True))
        .order_by(FeedSubscription.id)
    ).all()
    results = []
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for feed in feeds:
            results.append(fetch_feed(session, client, feed))
    return results
