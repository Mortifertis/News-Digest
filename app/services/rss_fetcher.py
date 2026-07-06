from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import feedparser
import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    Article,
    FeedFetchResult,
    FeedSubscription,
    FetchRun,
    StoryCluster,
)
from app.services.normalizer import normalize_article_fields


@dataclass
class FeedFetchStats:
    feed_id: int
    source_name: str
    feed_title: str
    feed_url: str
    language: str
    http_status: int | None = None
    entries_count: int = 0
    new_articles_count: int = 0
    skipped_articles_count: int = 0
    elapsed_ms: int = 0
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
    feed: FeedSubscription, result: FeedFetchStats, fetched_at: datetime
) -> None:
    feed.last_fetch_status = result.status
    feed.last_fetch_error = result.error
    feed.last_http_status = result.http_status
    feed.last_entries_count = result.entries_count
    feed.last_new_articles_count = result.new_articles_count
    feed.last_skipped_articles_count = result.skipped_articles_count
    feed.last_fetched_at = fetched_at
    if result.status == "success":
        feed.last_successful_fetch_at = fetched_at


def fetch_feed(
    session: Session, client: httpx.Client, feed: FeedSubscription
) -> FeedFetchStats:
    result = FeedFetchStats(
        feed_id=feed.id,
        source_name=feed.source.name,
        feed_title=feed.title,
        feed_url=feed.feed_url,
        language=feed.language,
    )
    started = time.perf_counter()
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
    result.elapsed_ms = round((time.perf_counter() - started) * 1000)
    update_feed_status(feed, result, datetime.now(UTC))
    session.commit()
    return result


def _status(successful: int, failed: int, total: int) -> str:
    if total == 0:
        return "no_enabled_feeds"
    if successful == total:
        return "success"
    if failed == total:
        return "failed"
    return "partial"


def _save_result(
    session: Session, run: FetchRun, result: FeedFetchStats
) -> None:
    session.add(
        FeedFetchResult(
            fetch_run_id=run.id,
            feed_id=result.feed_id,
            source_name=result.source_name,
            feed_title=result.feed_title,
            feed_url=result.feed_url,
            language=result.language,
            status=result.status,
            http_status=result.http_status,
            entries_count=result.entries_count,
            new_articles_count=result.new_articles_count,
            skipped_articles_count=result.skipped_articles_count,
            elapsed_ms=result.elapsed_ms,
            error=result.error,
            fetched_at=datetime.now(UTC),
        )
    )


def fetch_enabled_feeds(session: Session, *, mode: str = "cli") -> FetchRun:
    clusters_before = session.scalar(select(func.count(StoryCluster.id))) or 0
    run = FetchRun(
        started_at=datetime.now(UTC),
        finished_at=None,
        mode=mode,
        status="failed",
        total_clusters_before=clusters_before,
        total_clusters_after=clusters_before,
    )
    session.add(run)
    session.commit()
    feeds = session.scalars(
        select(FeedSubscription)
        .options(joinedload(FeedSubscription.source))
        .where(FeedSubscription.is_enabled.is_(True))
        .order_by(FeedSubscription.id)
    ).all()
    run.total_feeds = len(feeds)
    session.commit()
    if not feeds:
        run.status = "no_enabled_feeds"
        run.finished_at = datetime.now(UTC)
        session.commit()
        return run
    with httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0, read=20.0),
        follow_redirects=True,
    ) as client:
        for feed in feeds:
            result = fetch_feed(session, client, feed)
            run = session.get(FetchRun, run.id)
            _save_result(session, run, result)
            if result.status == "success":
                run.successful_feeds += 1
            else:
                run.failed_feeds += 1
            run.total_entries += result.entries_count
            run.total_new_articles += result.new_articles_count
            run.total_skipped_articles += result.skipped_articles_count
            session.commit()
    run.status = _status(
        run.successful_feeds, run.failed_feeds, run.total_feeds
    )
    run.finished_at = datetime.now(UTC)
    run.total_clusters_after = (
        session.scalar(select(func.count(StoryCluster.id))) or 0
    )
    failures = [item for item in run.feed_results if item.status == "failed"]
    run.error_summary = (
        "\n".join(f"{item.feed_title}: {item.error}" for item in failures)
        or None
    )
    session.commit()
    return run


def test_feed_by_id(session: Session, feed_id: int) -> FeedFetchStats:
    feed = session.scalar(
        select(FeedSubscription)
        .options(joinedload(FeedSubscription.source))
        .where(FeedSubscription.id == feed_id)
    )
    if feed is None:
        raise ValueError("Feed not found")
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        return fetch_feed(session, client, feed)
