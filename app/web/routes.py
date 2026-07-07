import logging
from typing import Annotated
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    Article,
    ClusterArticle,
    FeedSubscription,
    FetchRun,
    NewsSource,
    StoryCluster,
)
from app.db.session import get_session
from app.services.rss_fetcher import (
    fetch_enabled_feeds,
    fetch_feed,
    test_feed_by_id,
)
from app.services.settings_service import (
    SETTING_SPECS,
    get_all_settings_with_defaults,
    get_int_setting,
    set_setting,
    validate_settings_payload,
)
from app.services.source_filters import (
    FILTER_KEYS,
    get_filtered_feeds,
    normalize_filters,
    source_filter_options,
    source_summary,
)

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
templates = Jinja2Templates(directory="app/web/templates")
logger = logging.getLogger(__name__)


def redirect(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?message={message}", status_code=303)


def cluster_cards(session: Session, filters: dict, limit: int | None = None):
    source_counts = dict(
        session.execute(
            select(
                ClusterArticle.cluster_id,
                func.count(distinct(Article.source_id)),
            )
            .join(Article, Article.id == ClusterArticle.article_id)
            .group_by(ClusterArticle.cluster_id)
        ).all()
    )
    stmt = select(StoryCluster).options(
        joinedload(StoryCluster.lead_article).joinedload(Article.source),
        joinedload(StoryCluster.articles)
        .joinedload(ClusterArticle.article)
        .joinedload(Article.source),
    )
    if filters.get("language"):
        stmt = stmt.where(StoryCluster.language == filters["language"])
    sort = filters.get("sort") or "newest"
    if sort == "oldest":
        stmt = stmt.order_by(StoryCluster.last_seen_at.asc())
    elif sort == "source":
        stmt = stmt.join(StoryCluster.lead_article).join(Article.source)
        stmt = stmt.order_by(NewsSource.name, StoryCluster.last_seen_at.desc())
    else:
        stmt = stmt.order_by(StoryCluster.last_seen_at.desc())
    clusters = session.scalars(stmt).unique().all()
    rows = []
    for cluster in clusters:
        articles = [item.article for item in cluster.articles]
        if filters.get("source") and not any(
            item.source.name == filters["source"] for item in articles
        ):
            continue
        if filters.get("category") and not any(
            item.feed.category == filters["category"] for item in articles
        ):
            continue
        if filters.get("multi_article_only") and len(articles) < 2:
            continue
        if (
            filters.get("multi_source_only")
            and len({item.source_id for item in articles}) < 2
        ):
            continue
        rows.append((cluster, source_counts.get(cluster.id, 0)))
    if filters.get("sort") == "largest_cluster":
        rows.sort(key=lambda item: len(item[0].articles), reverse=True)
    return rows[:limit] if limit else rows


@router.get("/")
def dashboard(
    request: Request, session: SessionDep, message: str | None = None
):
    latest_run = session.scalar(
        select(FetchRun).order_by(FetchRun.started_at.desc())
    )
    enabled = (
        session.scalar(
            select(func.count(FeedSubscription.id)).where(
                FeedSubscription.is_enabled.is_(True)
            )
        )
        or 0
    )
    disabled = (
        session.scalar(
            select(func.count(FeedSubscription.id)).where(
                FeedSubscription.is_enabled.is_(False)
            )
        )
        or 0
    )
    multi_source_clusters = (
        session.scalar(
            select(func.count()).select_from(
                select(ClusterArticle.cluster_id)
                .join(Article, Article.id == ClusterArticle.article_id)
                .group_by(ClusterArticle.cluster_id)
                .having(func.count(distinct(Article.source_id)) > 1)
                .subquery()
            )
        )
        or 0
    )
    needs_url = (
        session.scalar(
            select(func.count(FeedSubscription.id)).where(
                FeedSubscription.feed_url.is_(None)
            )
        )
        or 0
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "message": message,
            "total_sources": session.scalar(select(func.count(NewsSource.id))),
            "total_feeds": session.scalar(
                select(func.count(FeedSubscription.id))
            ),
            "total_articles": session.scalar(select(func.count(Article.id))),
            "total_clusters": session.scalar(
                select(func.count(StoryCluster.id))
            ),
            "latest_run": latest_run,
            "enabled_feeds": enabled,
            "disabled_feeds": disabled,
            "needs_url_feeds": needs_url,
            "multi_source_clusters": multi_source_clusters,
            "successful_feeds": session.scalar(
                select(func.count(FeedSubscription.id)).where(
                    FeedSubscription.last_fetch_status == "success"
                )
            )
            or 0,
            "failed_feeds": session.scalar(
                select(func.count(FeedSubscription.id)).where(
                    FeedSubscription.last_fetch_status == "failed"
                )
            )
            or 0,
            "never_feeds": session.scalar(
                select(func.count(FeedSubscription.id)).where(
                    FeedSubscription.last_fetch_status == "never"
                )
            )
            or 0,
        },
    )


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _filter_values(request: Request) -> dict[str, str]:
    return normalize_filters(dict(request.query_params))


def _query_suffix(filters: dict[str, str]) -> str:
    pairs = [f"{key}={value}" for key, value in filters.items() if value]
    return "&".join(pairs)


def _sources_context(
    session: Session,
    filters: dict[str, str],
    message: str | None = None,
) -> dict:
    feeds = get_filtered_feeds(session, filters)
    return {
        "feeds": feeds,
        "message": message,
        "filters": filters,
        "filter_options": source_filter_options(session),
        "summary": source_summary(feeds),
    }


def _source_redirect(
    filters: dict[str, str], message: str
) -> RedirectResponse:
    suffix = _query_suffix(filters)
    separator = "&" if suffix else ""
    return RedirectResponse(
        f"/sources?{suffix}{separator}message={message}", status_code=303
    )


def _row_response(
    request: Request,
    session: Session,
    feed: FeedSubscription,
    message: str,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        "partials/source_row.html",
        {"feed": feed, "message": message},
    )
    response.headers["HX-Trigger"] = "sourcesChanged"
    return response


async def _request_filters(request: Request) -> dict[str, str]:
    values = dict(request.query_params)
    if request.method == "POST":
        body = (await request.body()).decode()
        form = parse_qs(body, keep_blank_values=True)
        values.update(
            {key: form[key][0] for key in FILTER_KEYS if key in form}
        )
    return normalize_filters(values)


@router.get("/sources")
def sources(
    request: Request,
    session: SessionDep,
    message: str | None = None,
    enabled: str | None = None,
    url_status: str | None = None,
    language: str | None = None,
    country: str | None = None,
    category: str | None = None,
    outlet_type: str | None = None,
    reliability_score: str | None = None,
    bias_profile: str | None = None,
    last_fetch_status: str | None = None,
):
    filters = normalize_filters(
        {
            "enabled": enabled,
            "url_status": url_status,
            "language": language,
            "country": country,
            "category": category,
            "outlet_type": outlet_type,
            "reliability_score": reliability_score,
            "bias_profile": bias_profile,
            "last_fetch_status": last_fetch_status,
        }
    )
    return templates.TemplateResponse(
        request, "sources.html", _sources_context(session, filters, message)
    )


@router.get("/sources/table")
def sources_table(request: Request, session: SessionDep):
    filters = _filter_values(request)
    return templates.TemplateResponse(
        request,
        "partials/source_table.html",
        _sources_context(session, filters),
    )


@router.get("/sources/summary")
def sources_summary(request: Request, session: SessionDep):
    filters = _filter_values(request)
    return templates.TemplateResponse(
        request,
        "partials/source_summary.html",
        _sources_context(session, filters),
    )


@router.post("/sources/{feed_id}/url")
async def save_source_url(
    request: Request,
    feed_id: int,
    session: SessionDep,
):
    filters = await _request_filters(request)
    allowed = {
        "verified_official",
        "candidate_pattern",
        "needs_verification",
        "needs_url_verification",
        "api_or_licensed_only",
        "unavailable",
    }
    form = parse_qs((await request.body()).decode(), keep_blank_values=True)
    feed_url = form.get("feed_url", [""])[0]
    rss_url_status = form.get("rss_url_status", ["candidate_pattern"])[0]
    feed = session.get(FeedSubscription, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    url = feed_url.strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        message = "Invalid feed URL"
        if _is_htmx(request):
            return _row_response(request, session, feed, message)
        return _source_redirect(filters, message)
    if rss_url_status == "needs_url_verification":
        rss_url_status = "needs_verification"
    if rss_url_status not in allowed:
        rss_url_status = "candidate_pattern" if url else "needs_verification"
    feed.feed_url = url or None
    feed.rss_url_status = rss_url_status if url else "needs_verification"
    feed.fetchable = bool(feed.feed_url) and feed.rss_url_status not in {
        "api_or_licensed_only",
        "unavailable",
    }
    if not feed.feed_url:
        feed.is_enabled = False
    session.commit()
    session.refresh(feed)
    if _is_htmx(request):
        return _row_response(request, session, feed, "Feed URL saved")
    return _source_redirect(filters, "Feed URL saved")


@router.post("/sources/{feed_id}/enable")
async def enable_source(request: Request, feed_id: int, session: SessionDep):
    filters = await _request_filters(request)
    try:
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="Feed not found")
        message = "Feed enabled"
        if not feed.feed_url:
            message = "Cannot enable feed without RSS URL"
        else:
            feed.is_enabled = True
            session.commit()
            session.refresh(feed)
        if _is_htmx(request):
            return _row_response(request, session, feed, message)
        return _source_redirect(filters, message)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Enable feed failed")
        return _source_redirect(filters, "Enable feed failed")


@router.post("/sources/{feed_id}/disable")
async def disable_source(request: Request, feed_id: int, session: SessionDep):
    filters = await _request_filters(request)
    try:
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="Feed not found")
        feed.is_enabled = False
        session.commit()
        session.refresh(feed)
        if _is_htmx(request):
            return _row_response(request, session, feed, "Feed disabled")
        return _source_redirect(filters, "Feed disabled")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Disable feed failed")
        return _source_redirect(filters, "Disable feed failed")


@router.post("/sources/{feed_id}/test")
async def test_source(request: Request, feed_id: int, session: SessionDep):
    filters = await _request_filters(request)
    try:
        result = test_feed_by_id(session, feed_id)
        feed = session.get(FeedSubscription, feed_id)
        if result.status == "success":
            message = f"Feed test succeeded: {result.entries_count} entries"
        else:
            message = f"Feed test failed: {result.error or result.status}"
        if _is_htmx(request):
            return _row_response(request, session, feed, message)
        return _source_redirect(filters, message)
    except Exception:
        logger.exception("Test feed failed")
        return _source_redirect(filters, "Test feed failed")


_BULK_SKIP_STATUSES = {"api_or_licensed_only", "unavailable"}


def _bulk_feeds(session: Session, filters: dict[str, str]) -> list:
    return get_filtered_feeds(session, filters)


@router.post("/sources/bulk-enable")
async def bulk_enable_sources(request: Request, session: SessionDep):
    filters = await _request_filters(request)
    feeds = _bulk_feeds(session, filters)
    enabled_count = 0
    skipped_count = 0
    for feed_item in feeds:
        if (
            not feed_item.feed_url
            or not feed_item.fetchable
            or feed_item.rss_url_status in _BULK_SKIP_STATUSES
        ):
            skipped_count += 1
            continue
        if not feed_item.is_enabled:
            enabled_count += 1
        feed_item.is_enabled = True
    session.commit()
    message = (
        f"Enabled {enabled_count} visible fetchable feeds; "
        f"skipped {skipped_count}."
    )
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "partials/source_workspace.html",
            _sources_context(session, filters, message),
        )
    return _source_redirect(filters, message)


@router.post("/sources/bulk-disable")
async def bulk_disable_sources(request: Request, session: SessionDep):
    filters = await _request_filters(request)
    feeds = _bulk_feeds(session, filters)
    disabled_count = 0
    for feed_item in feeds:
        if feed_item.is_enabled:
            disabled_count += 1
        feed_item.is_enabled = False
    session.commit()
    message = f"Disabled {disabled_count} visible feeds."
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "partials/source_workspace.html",
            _sources_context(session, filters, message),
        )
    return _source_redirect(filters, message)


@router.post("/sources/bulk-test")
async def bulk_test_sources(request: Request, session: SessionDep):
    filters = await _request_filters(request)
    feeds = [
        feed_item
        for feed_item in _bulk_feeds(session, filters)
        if feed_item.feed_url and feed_item.fetchable
    ]
    skipped_count = len(_bulk_feeds(session, filters)) - len(feeds)
    success_count = 0
    failed_count = 0
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for feed_item in feeds:
            result = fetch_feed(session, client, feed_item)
            if result.status == "success":
                success_count += 1
            else:
                failed_count += 1
    message = (
        "Bulk test may take some time. "
        f"Tested {len(feeds)} visible fetchable feeds: "
        f"{success_count} succeeded, {failed_count} failed, "
        f"{skipped_count} skipped."
    )
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "partials/source_workspace.html",
            _sources_context(session, filters, message),
        )
    return _source_redirect(filters, message)


@router.post("/fetch/run")
async def fetch_run(request: Request, session: SessionDep):
    filters = await _request_filters(request)
    try:
        run = fetch_enabled_feeds(session, mode="manual")
        message = (
            f'Fetch run created: <a href="/fetch-runs/{run.id}">view run</a>.'
        )
        if _is_htmx(request):
            return templates.TemplateResponse(
                request,
                "partials/source_workspace.html",
                _sources_context(session, filters, message),
            )
        return RedirectResponse(f"/fetch-runs/{run.id}", status_code=303)
    except Exception:
        logger.exception("Fetch run failed")
        if _is_htmx(request):
            return templates.TemplateResponse(
                request,
                "partials/source_flash.html",
                {"message": "Fetch run failed"},
            )
        return _source_redirect(filters, "Fetch run failed")


@router.get("/feed")
def feed(
    request: Request,
    session: SessionDep,
    language: str | None = None,
    source: str | None = None,
    category: str | None = None,
    multi_article_only: bool = False,
    multi_source_only: bool = False,
    sort: str | None = None,
):
    if language is None:
        language = (
            get_all_settings_with_defaults(session)["default_language_filter"]
            or None
        )
    if sort is None:
        sort = get_all_settings_with_defaults(session)["default_feed_sort"]
    filters = {
        "language": language,
        "source": source,
        "category": category,
        "multi_article_only": multi_article_only,
        "multi_source_only": multi_source_only,
        "sort": sort,
    }
    return templates.TemplateResponse(
        request,
        "feed.html",
        {
            "cluster_cards": cluster_cards(
                session,
                filters,
                get_int_setting(session, "items_per_page", 50),
            ),
            "filters": filters,
            "languages": session.scalars(select(Article.language).distinct()),
            "sources": session.scalars(select(NewsSource.name).distinct()),
            "categories": session.scalars(
                select(FeedSubscription.category).distinct()
            ),
        },
    )


@router.get("/review")
def review(request: Request, session: SessionDep):
    counts = (
        select(
            ClusterArticle.cluster_id.label("cluster_id"),
            func.count(ClusterArticle.article_id).label("article_count"),
        )
        .group_by(ClusterArticle.cluster_id)
        .subquery()
    )
    clusters = (
        session.scalars(
            select(StoryCluster)
            .join(counts, counts.c.cluster_id == StoryCluster.id)
            .options(
                joinedload(StoryCluster.articles)
                .joinedload(ClusterArticle.article)
                .joinedload(Article.source),
            )
            .order_by(counts.c.article_count.desc(), StoryCluster.id)
        )
        .unique()
        .all()
    )
    return templates.TemplateResponse(
        request, "review.html", {"clusters": clusters}
    )


@router.get("/fetch-runs")
def fetch_runs(request: Request, session: SessionDep):
    runs = session.scalars(
        select(FetchRun).order_by(FetchRun.started_at.desc()).limit(50)
    ).all()
    return templates.TemplateResponse(
        request, "fetch_runs.html", {"runs": runs}
    )


@router.get("/fetch-runs/{run_id}")
def fetch_run_detail(run_id: int, request: Request, session: SessionDep):
    run = session.scalar(
        select(FetchRun)
        .where(FetchRun.id == run_id)
        .options(joinedload(FetchRun.feed_results))
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Fetch run not found")
    return templates.TemplateResponse(
        request, "fetch_run_detail.html", {"run": run}
    )


@router.get("/settings")
def settings(
    request: Request, session: SessionDep, message: str | None = None
):
    values = get_all_settings_with_defaults(session)
    sections = {}
    for spec in SETTING_SPECS:
        sections.setdefault(spec.section, []).append(spec)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "message": message,
            "sections": sections,
            "values": values,
        },
    )


@router.post("/settings")
async def save_settings(request: Request, session: SessionDep):
    body = (await request.body()).decode()
    form = {
        key: values[0]
        for key, values in parse_qs(body, keep_blank_values=True).items()
    }
    values, errors = validate_settings_payload(form)
    if errors:
        return redirect("/settings", "; ".join(errors[:3]))
    for key, value in values.items():
        set_setting(session, key, value)
    try:
        session.commit()
    except Exception:
        logger.exception("Settings save failed")
        return redirect("/settings", "Settings save failed")
    return redirect("/settings", "Settings saved")


@router.get("/clusters/{cluster_id}")
def cluster_detail(cluster_id: int, request: Request, session: SessionDep):
    cluster = session.scalar(
        select(StoryCluster)
        .where(StoryCluster.id == cluster_id)
        .options(
            joinedload(StoryCluster.lead_article).joinedload(Article.source),
            joinedload(StoryCluster.articles)
            .joinedload(ClusterArticle.article)
            .joinedload(Article.source),
        )
    )
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return templates.TemplateResponse(
        request, "cluster_detail.html", {"cluster": cluster}
    )
