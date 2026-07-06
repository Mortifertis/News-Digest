from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.cli import ALLOWED_INTERVALS
from app.db.models import (
    AppSetting,
    Article,
    Base,
    FeedFetchResult,
    FeedSubscription,
    FetchRun,
    NewsSource,
)
from app.db.session import get_session
from app.main import app
from app.services.rss_fetcher import fetch_enabled_feeds
from app.services.seed_sources import seed_all_candidates


class FakeResponse:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def get(self, url):
        if "fail" in url:
            raise RuntimeError("network failed")
        xml = """
        <rss version="2.0"><channel><title>Test</title>
        <item><title>One</title><link>https://example.test/one</link>
        <guid>one</guid><description>Summary</description></item>
        </channel></rss>
        """
        return FakeResponse(content=xml.encode())


def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def add_feed(session, title="Feed", url="https://ok.test/rss", enabled=True):
    source = NewsSource(
        name=f"Source {title}",
        language="en",
        country="Test",
        homepage_url="https://example.test",
    )
    session.add(source)
    session.flush()
    feed = FeedSubscription(
        source_id=source.id,
        title=title,
        feed_url=url,
        category="test",
        language="en",
        is_enabled=enabled,
    )
    session.add(feed)
    session.commit()
    return feed


def test_seed_all_candidates_inserts_disabled_feeds():
    with session_factory() as session:
        seed_all_candidates(session)
        feeds = session.scalars(select(FeedSubscription)).all()
        assert feeds
        assert all(not feed.is_enabled for feed in feeds)


def test_enable_disable_feed_changes_is_enabled():
    with session_factory() as session:
        feed = add_feed(session, enabled=False)
        feed.is_enabled = True
        session.commit()
        assert session.get(FeedSubscription, feed.id).is_enabled is True
        feed.is_enabled = False
        session.commit()
        assert session.get(FeedSubscription, feed.id).is_enabled is False


def test_fetch_with_no_enabled_feeds_creates_no_enabled_run():
    with session_factory() as session:
        add_feed(session, enabled=False)
        run = fetch_enabled_feeds(session)
        assert run.status == "no_enabled_feeds"
        assert session.scalar(select(func.count(FetchRun.id))) == 1


def test_fetch_with_one_success_creates_success_run(monkeypatch):
    monkeypatch.setattr("app.services.rss_fetcher.httpx.Client", FakeClient)
    with session_factory() as session:
        feed = add_feed(session)
        run = fetch_enabled_feeds(session)
        assert run.status == "success"
        assert run.successful_feeds == 1
        assert run.total_new_articles == 1
        assert session.scalar(select(func.count(Article.id))) == 1
        assert session.scalar(select(func.count(FeedFetchResult.id))) == 1
        refreshed = session.get(FeedSubscription, feed.id)
        assert refreshed.last_fetch_status == "success"
        assert refreshed.last_successful_fetch_at is not None


def test_fetch_with_one_success_and_one_failure_is_partial(monkeypatch):
    monkeypatch.setattr("app.services.rss_fetcher.httpx.Client", FakeClient)
    with session_factory() as session:
        add_feed(session, title="Ok")
        add_feed(session, title="Fail", url="https://fail.test/rss")
        run = fetch_enabled_feeds(session)
        assert run.status == "partial"
        assert run.successful_feeds == 1
        assert run.failed_feeds == 1


def test_fetch_with_all_failed_feeds_is_failed(monkeypatch):
    monkeypatch.setattr("app.services.rss_fetcher.httpx.Client", FakeClient)
    with session_factory() as session:
        feed = add_feed(session, url="https://fail.test/rss")
        run = fetch_enabled_feeds(session)
        assert run.status == "failed"
        assert run.failed_feeds == 1
        refreshed = session.get(FeedSubscription, feed.id)
        assert refreshed.last_fetch_status == "failed"
        assert refreshed.last_fetch_error


def test_settings_interval_allowed_values_only():
    assert {0, 60, 180, 360, 720, 1440} == ALLOWED_INTERVALS
    with session_factory() as session:
        setting = AppSetting(key="fetch_interval_minutes", value="180")
        session.add(setting)
        session.commit()
        stored = session.get(AppSetting, "fetch_interval_minutes")
        assert int(stored.value) == 180


def test_dashboard_sources_and_fetch_runs_routes_return_200():
    session = session_factory()
    add_feed(session)

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert client.get("/sources").status_code == 200
        assert client.get("/fetch-runs").status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()
