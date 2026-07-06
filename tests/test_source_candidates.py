import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import Base, FeedSubscription
from app.services.source_candidates import (
    ProbeResult,
    SourceCandidate,
    probe_candidate,
    seed_accessible_sources,
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
