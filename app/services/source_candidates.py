from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser
from time import perf_counter
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Article, FeedSubscription, NewsSource
from app.services.source_candidate_schema import (
    FIND_RSS,
    ProbeResult,
    SourceCandidate,
    validate_candidate_catalog,
)
from app.services.source_catalog import build_candidate_catalog

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 20.0
TOTAL_TIMEOUT = 30.0

VERIFIED_FEEDS = validate_candidate_catalog(build_candidate_catalog())


def probe_candidate(
    candidate: SourceCandidate, client: httpx.Client
) -> ProbeResult:
    start = perf_counter()
    if not candidate.feed_url:
        return ProbeResult(candidate, "failed", None, 0.0, 0, FIND_RSS)
    http_status = None
    entries_count = 0
    error = None
    status = "failed"
    try:
        response = client.get(candidate.feed_url)
        http_status = response.status_code
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        entries_count = len(parsed.entries)
        if http_status == 200 and entries_count > 0:
            status = "success"
        else:
            error = "Feed returned zero parsed entries."
        if parsed.bozo and error is None:
            error = f"Feed parse warning: {parsed.bozo_exception}"
    except Exception as exc:
        error = str(exc)
    return ProbeResult(
        candidate,
        status,
        http_status,
        perf_counter() - start,
        entries_count,
        error,
    )


def probe_candidates(
    candidates: list[SourceCandidate] | None = None,
) -> list[ProbeResult]:
    timeout = httpx.Timeout(
        TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT, read=READ_TIMEOUT
    )
    results = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for candidate in candidates or VERIFIED_FEEDS:
            results.append(probe_candidate(candidate, client))
    return results


def _apply_source(source: NewsSource, c: SourceCandidate) -> None:
    primary_language = c.language_primary or c.language
    source.language = primary_language
    source.display_name = c.display_name or c.source_name
    source.language_primary = primary_language
    source.languages_available = ",".join(
        c.languages_available or [primary_language]
    )
    source.country = c.country
    source.region = c.region
    source.homepage_url = c.homepage_url
    source.outlet_type = c.outlet_type
    source.ownership_type = c.ownership_type
    source.paywall_level = c.paywall_level
    source.editorial_reliability_score = c.editorial_reliability_score
    source.bias_profile = c.bias_profile
    source.rating_confidence = c.rating_confidence
    source.rating_notes = c.rating_notes
    source.default_priority = c.default_priority
    source.tags = ",".join(c.tags)


def upsert_candidate(session: Session, c: SourceCandidate) -> bool:
    source = session.scalar(
        select(NewsSource).where(NewsSource.name == c.source_name)
    )
    if source is None:
        source = NewsSource(
            name=c.source_name,
            language=c.language_primary or c.language,
            country=c.country,
            homepage_url=c.homepage_url,
        )
        session.add(source)
        session.flush()
    _apply_source(source, c)
    stmt = select(FeedSubscription).where(
        FeedSubscription.title == c.feed_title
    )
    if c.feed_url:
        stmt = select(FeedSubscription).where(
            FeedSubscription.feed_url == c.feed_url
        )
    feed = session.scalar(stmt)
    created = feed is None
    if feed is None:
        feed = FeedSubscription(
            source_id=source.id,
            title=c.feed_title,
            feed_url=c.feed_url,
            category=c.category,
            language=c.language,
        )
        session.add(feed)
    feed.source_id = source.id
    feed.title = c.feed_title
    feed.feed_url = c.feed_url
    feed.category = c.category
    feed.language = c.language
    feed.tags = ",".join(c.feed_tags)
    feed.is_official_url = c.is_official_url
    feed.url_confidence = c.url_confidence
    feed.enabled_by_default = c.enabled_by_default
    feed.notes = c.notes
    feed.rss_url_status = c.rss_url_status
    feed.rss_url_checked_at = c.rss_url_checked_at
    feed.rss_url_source_note = c.rss_url_source_note
    feed.terms_note = c.terms_note
    feed.fetchable = c.fetchable and bool(c.feed_url)
    feed.is_enabled = c.enabled_by_default and bool(c.feed_url)
    return created


class FeedLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "link":
            return
        attr = {k.lower(): v for k, v in attrs if v is not None}
        rel = attr.get("rel", "").lower()
        typ = attr.get("type", "").lower()
        href = attr.get("href")
        if (
            href
            and "alternate" in rel
            and typ in {"application/rss+xml", "application/atom+xml"}
        ):
            self.links.append(urljoin(self.base_url, href))


def discover_feed_urls_from_html(html: str, base_url: str) -> list[str]:
    parser = FeedLinkParser(base_url)
    parser.feed(html)
    return parser.links


def discover_feed_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return []
    try:
        response = httpx.get(url, timeout=TOTAL_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    return discover_feed_urls_from_html(response.text, str(response.url))


def save_discovered_feed_url(
    session: Session, feed_id: int, feed_url: str
) -> None:
    feed = session.get(FeedSubscription, feed_id)
    if feed is None:
        raise ValueError(f"Feed not found: {feed_id}")
    feed.feed_url = feed_url.strip()
    feed.rss_url_status = "candidate_pattern"
    feed.rss_url_source_note = "Saved from RSS autodiscovery."
    feed.fetchable = bool(feed.feed_url)
    session.commit()


def probe_feed_urls(session: Session) -> dict[str, int]:
    feeds = session.scalars(select(FeedSubscription)).all()
    summary = {
        "checked": 0,
        "success": 0,
        "failed": 0,
        "needs_url": 0,
        "api_or_licensed_only": 0,
    }
    for feed in feeds:
        if feed.rss_url_status == "api_or_licensed_only":
            summary["api_or_licensed_only"] += 1
            feed.fetchable = False
            continue
        if not feed.feed_url:
            summary["needs_url"] += 1
            feed.fetchable = False
            if feed.rss_url_status not in {
                "unavailable",
                "api_or_licensed_only",
            }:
                feed.rss_url_status = "needs_verification"
            continue
        summary["checked"] += 1
        feed.rss_url_checked_at = datetime.now(UTC)
        with httpx.Client(
            timeout=httpx.Timeout(
                TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT, read=READ_TIMEOUT
            ),
            follow_redirects=True,
        ) as client:
            result = probe_candidate(
                SourceCandidate(
                    source_name=feed.source.name,
                    language=feed.language,
                    country=feed.source.country,
                    homepage_url=feed.source.homepage_url,
                    feed_title=feed.title,
                    feed_url=feed.feed_url,
                    category=feed.category,
                ),
                client,
            )
        feed.last_fetch_status = result.status
        feed.last_http_status = result.http_status
        feed.last_entries_count = result.entries_count
        feed.last_fetch_error = result.error
        if result.is_success:
            summary["success"] += 1
            if feed.rss_url_status in {
                "candidate_pattern",
                "verified_official",
            }:
                feed.rss_url_status = "verified_official"
            feed.fetchable = True
        else:
            summary["failed"] += 1
            feed.fetchable = False
    session.commit()
    return summary


PLACEHOLDER_FILTER = or_(
    FeedSubscription.feed_url.is_(None),
    FeedSubscription.feed_url == "",
    FeedSubscription.feed_url.ilike("%example.com%"),
    NewsSource.homepage_url.ilike("%example.com%"),
)


def report_placeholder_sources(session: Session) -> dict[str, object]:
    empty_feeds = session.scalars(
        select(FeedSubscription)
        .join(NewsSource)
        .where(
            or_(
                FeedSubscription.feed_url.is_(None),
                FeedSubscription.feed_url == "",
            )
        )
    ).all()
    example_sources = session.scalars(
        select(NewsSource).where(
            NewsSource.homepage_url.ilike("%example.com%")
        )
    ).all()
    api_feeds = session.scalars(
        select(FeedSubscription).where(
            FeedSubscription.rss_url_status == "api_or_licensed_only"
        )
    ).all()
    bad_feed_ids = {
        feed.id
        for feed in session.scalars(
            select(FeedSubscription).join(NewsSource).where(PLACEHOLDER_FILTER)
        ).all()
    }
    bad_feed_ids.update(feed.id for feed in api_feeds)
    return {
        "empty_feed_titles": [feed.title for feed in empty_feeds],
        "example_source_names": [source.name for source in example_sources],
        "api_or_licensed_only_titles": [feed.title for feed in api_feeds],
        "total_non_operational_feeds": len(bad_feed_ids),
    }


def cleanup_placeholder_sources(
    session: Session, *, force: bool = False
) -> dict[str, int]:
    feeds = session.scalars(
        select(FeedSubscription).join(NewsSource).where(PLACEHOLDER_FILTER)
    ).all()
    feeds_deleted = 0
    skipped = 0
    for feed in feeds:
        article_count = session.scalar(
            select(func.count(Article.id)).where(Article.feed_id == feed.id)
        )
        if article_count and not force:
            skipped += 1
            continue
        session.delete(feed)
        feeds_deleted += 1
    session.flush()
    sources = session.scalars(select(NewsSource)).all()
    sources_deleted = 0
    for source in sources:
        feed_count = session.scalar(
            select(func.count(FeedSubscription.id)).where(
                FeedSubscription.source_id == source.id
            )
        )
        if feed_count == 0:
            session.delete(source)
            sources_deleted += 1
    session.commit()
    return {
        "feeds_deleted": feeds_deleted,
        "sources_deleted": sources_deleted,
        "skipped_due_to_existing_articles": skipped,
    }


def seed_all_candidate_sources(session: Session) -> int:
    count = 0
    for candidate in validate_candidate_catalog(VERIFIED_FEEDS):
        if upsert_candidate(session, candidate):
            count += 1
    session.commit()
    return count


def seed_verified_feeds(session: Session) -> list[ProbeResult]:
    results = probe_candidates(VERIFIED_FEEDS)
    for result in results:
        if not result.is_success:
            continue
        upsert_candidate(session, result.candidate)
        feed = session.scalar(
            select(FeedSubscription).where(
                FeedSubscription.feed_url == result.candidate.feed_url
            )
        )
        if feed is not None:
            feed.last_fetch_status = result.status
            feed.last_http_status = result.http_status
            feed.last_entries_count = result.entries_count
            feed.last_fetch_error = result.error
            feed.fetchable = True
    session.commit()
    return results


def seed_accessible_sources(session: Session) -> list[ProbeResult]:
    results = probe_candidates()
    for result in results:
        if result.candidate.feed_url:
            upsert_candidate(session, result.candidate)
        if result.candidate.feed_url:
            feed = session.scalar(
                select(FeedSubscription).where(
                    FeedSubscription.feed_url == result.candidate.feed_url
                )
            )
        else:
            feed = session.scalar(
                select(FeedSubscription).where(
                    FeedSubscription.title == result.candidate.feed_title
                )
            )
        if feed is not None:
            feed.is_enabled = result.is_success
            feed.last_fetch_status = result.status
            feed.last_http_status = result.http_status
            feed.last_entries_count = result.entries_count
            feed.last_new_articles_count = 0
            feed.last_skipped_articles_count = 0
            feed.last_fetch_error = result.error
    session.commit()
    return results
