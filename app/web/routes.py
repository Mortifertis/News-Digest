import logging
from typing import Annotated
from urllib.parse import parse_qs

import httpx
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
from app.services.settings_service import (
    SETTING_SPECS,
    get_all_settings_with_defaults,
    get_int_setting,
    set_setting,
    validate_settings_payload,
)
from app.web.source_routes import router as source_router

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
templates = Jinja2Templates(directory="app/web/templates")
logger = logging.getLogger(__name__)


def create_http_client(**kwargs):
    return httpx.Client(**kwargs)


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


router.include_router(source_router)

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
