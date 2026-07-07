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
from app.services.rss_fetcher import (
    fetch_enabled_feeds,
)
from app.services.rss_fetcher import (
    test_feed_by_id as run_test_feed_by_id,
)
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
        feed_url=url or None,
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


def test_settings_routes_valid_and_invalid_posts():
    session = session_factory()

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        assert client.get("/settings").status_code == 200
        response = client.post(
            "/settings", data={"fetch_interval_minutes": "180"}
        )
        assert response.status_code == 200
        stored = session.get(AppSetting, "fetch_interval_minutes")
        assert stored.value == "180"
        response = client.post(
            "/settings", data={"fetch_interval_minutes": "17"}
        )
        assert response.status_code == 200
        assert session.get(AppSetting, "fetch_interval_minutes").value == "180"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_fetch_run_routes_empty_and_no_enabled_feeds():
    session = session_factory()
    add_feed(session, enabled=False)

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get("/fetch-runs")
        assert response.status_code == 200
        assert "No fetch runs yet" in response.text
        response = client.post("/fetch/run", follow_redirects=False)
        assert response.status_code == 303
        run = session.scalar(select(FetchRun))
        assert run.status == "no_enabled_feeds"
        assert client.get("/fetch-runs").status_code == 200
        assert client.get(f"/fetch-runs/{run.id}").status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_source_catalog_metadata_and_empty_url_workflow():
    session = session_factory()
    seed_all_candidates(session)
    feeds = session.scalars(select(FeedSubscription)).all()
    assert len(feeds) >= 80
    empty_url_feeds = [feed for feed in feeds if not feed.feed_url]
    assert empty_url_feeds
    assert all(not feed.is_enabled for feed in empty_url_feeds)
    source = session.scalar(select(NewsSource))
    assert source.outlet_type
    assert source.editorial_reliability_score
    result = run_test_feed_by_id(session, empty_url_feeds[0].id)
    assert result.status == "failed"
    assert "needs URL verification" in result.error


def test_sources_filters_return_200():
    session = session_factory()
    seed_all_candidates(session)

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        paths = [
            "/sources?language=en",
            "/sources?language=fr",
            "/sources?reliability_score=5",
            "/sources?needs_url=true",
        ]
        for path in paths:
            assert client.get(path).status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_sources_dropdown_options_and_selected_values():
    session = session_factory()
    seed_all_candidates(session)

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.get(
            "/sources?language=fr&category=world&outlet_type=public_broadcaster"
        )
        assert response.status_code == 200
        assert '<select name="language">' in response.text
        assert '<option value="fr" selected>fr</option>' in response.text
        assert '<option value="en"' in response.text
        assert '<option value="world" selected>world</option>' in response.text
        assert 'value="public_broadcaster" selected' in response.text
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_sources_url_editor_valid_invalid_clear_and_verified_status():
    session = session_factory()
    feed = add_feed(session, url=None, enabled=False)

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.post(
            f"/sources/{feed.id}/url",
            data={
                "feed_url": " https://example.test/rss.xml ",
                "rss_url_status": "verified_official",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        session.refresh(feed)
        assert feed.feed_url == "https://example.test/rss.xml"
        assert feed.rss_url_status == "verified_official"
        assert feed.fetchable is True
        assert feed.is_enabled is False

        response = client.post(
            f"/sources/{feed.id}/url",
            data={
                "feed_url": "notaurl",
                "rss_url_status": "candidate_pattern",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        session.refresh(feed)
        assert feed.feed_url == "https://example.test/rss.xml"

        response = client.post(
            f"/sources/{feed.id}/url",
            data={"feed_url": "", "rss_url_status": "verified_official"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        session.refresh(feed)
        assert feed.feed_url is None
        assert feed.rss_url_status == "needs_verification"
        assert feed.fetchable is False
        assert feed.is_enabled is False
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_sources_empty_dropdown_filters_return_200():
    session = session_factory()
    seed_all_candidates(session)

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        path = (
            "/sources?enabled=&url_status=&language=en&country="
            "&category=&outlet_type=&reliability_score="
            "&bias_profile=&last_fetch_status="
        )
        assert client.get(path).status_code == 200
        paths = [
            "/sources?country=Canada",
            "/sources?reliability_score=",
            "/sources?reliability_score=bad",
            "/sources?enabled=enabled",
            "/sources?enabled=disabled",
        ]
        for item in paths:
            assert client.get(item).status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_settings_full_payload_and_defaults():
    from app.services.settings_service import get_all_settings_with_defaults

    session = session_factory()

    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        payload = {
            "fetch_interval_minutes": "60",
            "max_entries_per_feed": "20",
            "request_timeout_seconds": "10",
            "auto_cluster_after_fetch": "false",
            "fuzzy_threshold_default": "80",
            "fuzzy_threshold_en": "81",
            "fuzzy_threshold_fr": "82",
            "fuzzy_candidate_window_hours": "48",
            "min_text_length_for_fuzzy": "50",
            "items_per_page": "20",
            "default_language_filter": "en",
            "default_feed_sort": "source",
            "article_retention_days": "30",
        }
        assert client.get("/settings").status_code == 200
        assert client.post("/settings", data=payload).status_code == 200
        defaults = get_all_settings_with_defaults(session)
        assert set(payload).issubset(defaults)
        assert defaults["max_entries_per_feed"] == "20"
        bad = dict(payload)
        bad["fetch_interval_minutes"] = "17"
        assert client.post("/settings", data=bad).status_code == 200
        bad = dict(payload)
        bad["fuzzy_threshold_en"] = "200"
        assert client.post("/settings", data=bad).status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_fetch_uses_max_entries_setting(monkeypatch):
    monkeypatch.setattr("app.services.rss_fetcher.httpx.Client", FakeClient)
    with session_factory() as session:
        session.add(AppSetting(key="max_entries_per_feed", value="10"))
        add_feed(session)
        run = fetch_enabled_feeds(session)
        assert run.total_entries == 1


def test_scheduler_reads_fetch_interval_setting():
    from app.services.settings_service import get_int_setting

    with session_factory() as session:
        session.add(AppSetting(key="fetch_interval_minutes", value="60"))
        session.commit()
        assert get_int_setting(session, "fetch_interval_minutes", 0) == 60


def add_custom_feed(
    session,
    title="Feed",
    url=None,
    enabled=False,
    language="en",
    category="world",
    fetchable=True,
    status="candidate_pattern",
    reliability=5,
):
    if url is None and fetchable:
        url = f"https://ok.test/{title.replace(' ', '-')}/rss"
    source = NewsSource(
        name=f"Custom {title}",
        language=language,
        country="Test",
        homepage_url="https://example.test",
        editorial_reliability_score=reliability,
    )
    session.add(source)
    session.flush()
    feed = FeedSubscription(
        source_id=source.id,
        title=title,
        feed_url=url or None,
        category=category,
        language=language,
        rss_url_status=status,
        fetchable=fetchable,
        is_enabled=enabled,
    )
    session.add(feed)
    session.commit()
    return feed


def client_for_session(session):
    def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_enable_with_filters_preserves_query_params_non_htmx():
    session = session_factory()
    feed = add_custom_feed(session, language="fr", enabled=False)
    try:
        client = client_for_session(session)
        response = client.post(
            f"/sources/{feed.id}/enable?language=fr&category=world",
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "language=fr" in response.headers["location"]
        assert "category=world" in response.headers["location"]
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_htmx_enable_and_disable_return_row_partial():
    session = session_factory()
    feed = add_custom_feed(session, enabled=False)
    try:
        client = client_for_session(session)
        response = client.post(
            f"/sources/{feed.id}/enable",
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert f'id="source-row-{feed.id}"' in response.text
        assert "<html" not in response.text
        response = client.post(
            f"/sources/{feed.id}/disable",
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert f'id="source-row-{feed.id}"' in response.text
        assert "disabled" in response.text
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_htmx_test_empty_url_returns_controlled_row_message():
    session = session_factory()
    feed = add_custom_feed(session, url=None, fetchable=False)
    try:
        client = client_for_session(session)
        response = client.post(
            f"/sources/{feed.id}/test",
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert "Feed test failed" in response.text
        assert "needs URL verification" in response.text
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_sources_table_and_summary_filtered_counts():
    session = session_factory()
    add_custom_feed(session, title="FR enabled", language="fr", enabled=True)
    add_custom_feed(session, title="EN disabled", language="en", enabled=False)
    try:
        client = client_for_session(session)
        response = client.get("/sources/table?language=fr")
        assert response.status_code == 200
        assert "FR enabled" in response.text
        assert "EN disabled" not in response.text
        response = client.get("/sources/summary?language=fr")
        assert response.status_code == 200
        assert "visible feeds" in response.text
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_bulk_enable_only_filtered_and_skips_empty_url():
    session = session_factory()
    fr = add_custom_feed(session, title="FR", language="fr", enabled=False)
    empty = add_custom_feed(
        session,
        title="FR empty",
        url=None,
        language="fr",
        enabled=False,
        fetchable=False,
    )
    en = add_custom_feed(session, title="EN", language="en", enabled=False)
    try:
        client = client_for_session(session)
        response = client.post(
            "/sources/bulk-enable?language=fr", follow_redirects=False
        )
        assert response.status_code == 303
        session.refresh(fr)
        session.refresh(empty)
        session.refresh(en)
        assert fr.is_enabled is True
        assert empty.is_enabled is False
        assert en.is_enabled is False
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_bulk_disable_only_filtered():
    session = session_factory()
    fr = add_custom_feed(session, title="FR", language="fr", enabled=True)
    en = add_custom_feed(session, title="EN", language="en", enabled=True)
    try:
        client = client_for_session(session)
        response = client.post(
            "/sources/bulk-disable?language=fr", follow_redirects=False
        )
        assert response.status_code == 303
        session.refresh(fr)
        session.refresh(en)
        assert fr.is_enabled is False
        assert en.is_enabled is True
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_bulk_test_handles_failures_without_crashing(monkeypatch):
    monkeypatch.setattr("app.web.routes.httpx.Client", FakeClient)
    session = session_factory()
    feed = add_custom_feed(session, title="Fail", url="https://fail.test/rss")
    try:
        client = client_for_session(session)
        response = client.post(
            "/sources/bulk-test?language=en", follow_redirects=False
        )
        assert response.status_code == 303
        session.refresh(feed)
        assert feed.last_fetch_status == "failed"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_filters_service_matches_sources_table_subset():
    from app.services.source_filters import get_filtered_feeds

    session = session_factory()
    fr = add_custom_feed(session, title="FR", language="fr")
    add_custom_feed(session, title="EN", language="en")
    try:
        feeds = get_filtered_feeds(session, {"language": "fr"})
        assert [feed.id for feed in feeds] == [fr.id]
    finally:
        session.close()
