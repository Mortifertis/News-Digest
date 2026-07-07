import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import Base, FeedSubscription
from app.services.source_candidates import (
    VERIFIED_FEEDS,
    ProbeResult,
    SourceCandidate,
    cleanup_placeholder_sources,
    probe_candidate,
    report_placeholder_sources,
    seed_accessible_sources,
    seed_all_candidate_sources,
    validate_candidate_catalog,
)

RSS_BODY = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<title>Test</title><item><title>One</title><link>https://example.test/1</link>
<description>Summary</description></item></channel></rss>"""
EMPTY_RSS_BODY = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<title>Test</title></channel></rss>"""


def candidate(url="https://example.test/feed.xml"):
    return SourceCandidate(
        source_name="Example News",
        language="en",
        country="Test",
        homepage_url="https://example.test",
        feed_title="Example Feed",
        feed_url=url,
        category="test",
        priority=1,
        notes="test",
    )


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_successful_feed_is_detected_as_success():
    def handler(request):
        return httpx.Response(200, content=RSS_BODY)

    with client_for(handler) as client:
        result = probe_candidate(candidate(), client)

    assert result.status == "success"
    assert result.http_status == 200
    assert result.entries_count == 1


def test_failed_timeout_is_captured_as_failed():
    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    with client_for(handler) as client:
        result = probe_candidate(candidate(), client)

    assert result.status == "failed"
    assert result.http_status is None
    assert "timed out" in result.error


def test_feed_with_zero_entries_is_not_successful():
    def handler(request):
        return httpx.Response(200, content=EMPTY_RSS_BODY)

    with client_for(handler) as client:
        result = probe_candidate(candidate(), client)

    assert result.status == "failed"
    assert result.http_status == 200
    assert result.entries_count == 0


def test_seed_accessible_sources_enables_only_successes(monkeypatch):
    good = candidate("https://example.test/good.xml")
    bad = candidate("https://example.test/bad.xml")

    def fake_probe_candidates():
        return [
            ProbeResult(good, "success", 200, 0.01, 1),
            ProbeResult(bad, "failed", None, 0.01, 0, "timed out"),
        ]

    monkeypatch.setattr(
        "app.services.source_candidates.probe_candidates",
        fake_probe_candidates,
    )
    with session_factory() as session:
        seed_accessible_sources(session)
        feeds = session.scalars(select(FeedSubscription)).all()

    enabled = {feed.feed_url: feed.is_enabled for feed in feeds}
    assert enabled[good.feed_url] is True
    assert enabled[bad.feed_url] is False


def test_candidate_catalog_contains_only_real_urls():
    validate_candidate_catalog(VERIFIED_FEEDS)
    assert all(candidate.feed_url for candidate in VERIFIED_FEEDS)
    assert all(
        "example.com" not in candidate.homepage_url
        for candidate in VERIFIED_FEEDS
    )
    assert all(
        candidate.feed_url and "example.com" not in candidate.feed_url
        for candidate in VERIFIED_FEEDS
    )


def test_seed_all_candidates_creates_no_placeholder_rows():
    with session_factory() as session:
        seed_all_candidate_sources(session)
        feeds = session.scalars(select(FeedSubscription)).all()
        assert feeds
        assert all(feed.feed_url for feed in feeds)
        assert all("example.com" not in feed.feed_url for feed in feeds)
        assert not any(
            "example.com" in feed.source.homepage_url for feed in feeds
        )
        names = {feed.source.name for feed in feeds}
        assert "Reuters" not in names
        assert "Associated Press" not in names


def test_report_and_cleanup_placeholder_sources():
    with session_factory() as session:
        source = __import__(
            "app.db.models", fromlist=["NewsSource"]
        ).NewsSource(
            name="Bad Source",
            language="en",
            country="Test",
            homepage_url="https://www.example.com/bad",
        )
        session.add(source)
        session.flush()
        empty = FeedSubscription(
            source_id=source.id,
            title="Empty URL",
            feed_url=None,
            category="test",
            language="en",
        )
        fake = FeedSubscription(
            source_id=source.id,
            title="Fake URL",
            feed_url="https://www.example.com/rss.xml",
            category="test",
            language="en",
        )
        api = FeedSubscription(
            source_id=source.id,
            title="Licensed",
            feed_url="https://licensed.test/rss.xml",
            category="test",
            language="en",
            rss_url_status="api_or_licensed_only",
        )
        session.add_all([empty, fake, api])
        session.commit()

        report = report_placeholder_sources(session)
        assert "Empty URL" in report["empty_feed_titles"]
        assert "Bad Source" in report["example_source_names"]
        assert "Licensed" in report["api_or_licensed_only_titles"]
        assert report["total_non_operational_feeds"] == 3

        summary = cleanup_placeholder_sources(session)
        assert summary["feeds_deleted"] == 3
        assert summary["sources_deleted"] == 1
        assert summary["skipped_due_to_existing_articles"] == 0
        assert not session.scalars(select(FeedSubscription)).all()


def test_source_wishlist_mentions_reuters_and_ap():
    text = __import__("pathlib").Path("docs/source_wishlist.md").read_text()
    assert "Reuters" in text
    assert "Associated Press" in text
