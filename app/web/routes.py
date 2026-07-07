import logging
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
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
from app.services.rss_fetcher import fetch_enabled_feeds, test_feed_by_id
from app.services.settings_service import (
    SETTING_SPECS,
    get_all_settings_with_defaults,
    get_int_setting,
    set_setting,
    validate_settings_payload,
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


def clean_filter(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def parse_enabled_filter(value: str | None) -> bool | None:
    cleaned = clean_filter(value)
    if cleaned in {None, "all"}:
        return None
    if cleaned == "enabled":
        return True
    if cleaned == "disabled":
        return False
    return None


def parse_reliability_filter(value: str | None) -> int | None:
    cleaned = clean_filter(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


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
    filters = {
        "enabled": clean_filter(enabled) or "",
        "url_status": clean_filter(url_status) or "",
        "language": clean_filter(language) or "",
        "country": clean_filter(country) or "",
        "category": clean_filter(category) or "",
        "outlet_type": clean_filter(outlet_type) or "",
        "reliability_score": clean_filter(reliability_score) or "",
        "bias_profile": clean_filter(bias_profile) or "",
        "last_fetch_status": clean_filter(last_fetch_status) or "",
    }
    stmt = select(FeedSubscription).options(
        joinedload(FeedSubscription.source)
    )
    enabled_value = parse_enabled_filter(enabled)
    if enabled_value is not None:
        stmt = stmt.where(FeedSubscription.is_enabled.is_(enabled_value))
    allowed_url_statuses = {
        "has_url",
        "needs_url_verification",
        "verified_official",
        "candidate_pattern",
        "api_or_licensed_only",
        "unavailable",
    }
    normalized_url_status = clean_filter(url_status)
    if normalized_url_status == "all":
        normalized_url_status = None
    if normalized_url_status == "needs_url_verification":
        normalized_url_status = "needs_verification"
    if normalized_url_status == "has_url":
        stmt = stmt.where(FeedSubscription.feed_url.is_not(None))
        stmt = stmt.where(FeedSubscription.feed_url != "")
    elif normalized_url_status in allowed_url_statuses:
        stmt = stmt.where(
            FeedSubscription.rss_url_status == normalized_url_status
        )
    source_joined = False
    if filters["language"]:
        stmt = stmt.where(FeedSubscription.language == filters["language"])
    if filters["country"]:
        stmt = stmt.join(FeedSubscription.source)
        source_joined = True
        stmt = stmt.where(NewsSource.country == filters["country"])
    if filters["category"]:
        stmt = stmt.where(FeedSubscription.category == filters["category"])
    if filters["outlet_type"]:
        if not source_joined:
            stmt = stmt.join(FeedSubscription.source)
            source_joined = True
        stmt = stmt.where(NewsSource.outlet_type == filters["outlet_type"])
    reliability_value = parse_reliability_filter(reliability_score)
    if reliability_value is not None:
        if not source_joined:
            stmt = stmt.join(FeedSubscription.source)
            source_joined = True
        stmt = stmt.where(
            NewsSource.editorial_reliability_score == reliability_value
        )
    if filters["bias_profile"]:
        if not source_joined:
            stmt = stmt.join(FeedSubscription.source)
            source_joined = True
        stmt = stmt.where(NewsSource.bias_profile == filters["bias_profile"])
    if filters["last_fetch_status"]:
        stmt = stmt.where(
            FeedSubscription.last_fetch_status == filters["last_fetch_status"]
        )
    feeds = (
        session.scalars(stmt.order_by(FeedSubscription.title)).unique().all()
    )
    filter_options = {
        "languages": session.scalars(
            select(FeedSubscription.language).distinct().order_by(
                FeedSubscription.language
            )
        ).all(),
        "countries": session.scalars(
            select(NewsSource.country).distinct().order_by(NewsSource.country)
        ).all(),
        "categories": session.scalars(
            select(FeedSubscription.category).distinct().order_by(
                FeedSubscription.category
            )
        ).all(),
        "outlet_types": session.scalars(
            select(NewsSource.outlet_type)
            .where(NewsSource.outlet_type.is_not(None))
            .distinct()
            .order_by(NewsSource.outlet_type)
        ).all(),
        "bias_profiles": session.scalars(
            select(NewsSource.bias_profile)
            .where(NewsSource.bias_profile.is_not(None))
            .distinct()
            .order_by(NewsSource.bias_profile)
        ).all(),
    }
    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "feeds": feeds,
            "message": message,
            "filters": filters,
            "filter_options": filter_options,
        },
    )

@router.post("/sources/{feed_id}/url")
async def save_source_url(
    request: Request,
    feed_id: int,
    session: SessionDep,
):
    allowed = {
        "verified_official",
        "candidate_pattern",
        "needs_verification",
        "needs_url_verification",
        "api_or_licensed_only",
        "unavailable",
    }
    body = (await request.body()).decode()
    form = parse_qs(body, keep_blank_values=True)
    feed_url = form.get("feed_url", [""])[0]
    rss_url_status = form.get(
        "rss_url_status", ["candidate_pattern"]
    )[0]
    feed = session.get(FeedSubscription, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    url = feed_url.strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        return redirect("/sources", "Invalid feed URL")
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
    return redirect("/sources", "Feed URL saved")


@router.post("/sources/{feed_id}/enable")
def enable_source(feed_id: int, session: SessionDep):
    try:
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="Feed not found")
        if not feed.feed_url:
            return redirect("/sources", "Feed needs URL verification")
        feed.is_enabled = True
        session.commit()
        return redirect("/sources", "Feed enabled")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Enable feed failed")
        return redirect("/sources", "Enable feed failed")


@router.post("/sources/{feed_id}/disable")
def disable_source(feed_id: int, session: SessionDep):
    try:
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="Feed not found")
        feed.is_enabled = False
        session.commit()
        return redirect("/sources", "Feed disabled")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Disable feed failed")
        return redirect("/sources", "Disable feed failed")


@router.post("/sources/{feed_id}/test")
def test_source(feed_id: int, session: SessionDep):
    try:
        result = test_feed_by_id(session, feed_id)
        return redirect("/sources", f"Test finished: {result.status}")
    except Exception:
        logger.exception("Test feed failed")
        return redirect("/sources", "Test feed failed")


@router.post("/fetch/run")
def fetch_run(session: SessionDep):
    try:
        run = fetch_enabled_feeds(session, mode="manual")
        return RedirectResponse(f"/fetch-runs/{run.id}", status_code=303)
    except Exception:
        logger.exception("Fetch run failed")
        return redirect("/fetch-runs", "Fetch run failed")


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
        language = get_all_settings_with_defaults(session)[
            "default_language_filter"
        ] or None
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
