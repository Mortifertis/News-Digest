from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, joinedload

from app.cli import ALLOWED_INTERVALS
from app.db.models import (
    AppSetting,
    Article,
    ClusterArticle,
    FeedSubscription,
    FetchRun,
    NewsSource,
    StoryCluster,
)
from app.db.session import get_session
from app.services.rss_fetcher import fetch_enabled_feeds, test_feed_by_id

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
templates = Jinja2Templates(directory="app/web/templates")


def redirect(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?message={message}", status_code=303)


def cluster_cards(session: Session, filters: dict):
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
    return rows


@router.get("/")
def dashboard(
    request: Request, session: SessionDep, message: str | None = None
):
    latest_run = session.scalar(
        select(FetchRun).order_by(FetchRun.started_at.desc())
    )
    enabled = session.scalar(
        select(func.count(FeedSubscription.id)).where(
            FeedSubscription.is_enabled.is_(True)
        )
    ) or 0
    disabled = session.scalar(
        select(func.count(FeedSubscription.id)).where(
            FeedSubscription.is_enabled.is_(False)
        )
    ) or 0
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
            "successful_feeds": session.scalar(
                select(func.count(FeedSubscription.id)).where(
                    FeedSubscription.last_fetch_status == "success"
                )
            ) or 0,
            "failed_feeds": session.scalar(
                select(func.count(FeedSubscription.id)).where(
                    FeedSubscription.last_fetch_status == "failed"
                )
            ) or 0,
            "never_feeds": session.scalar(
                select(func.count(FeedSubscription.id)).where(
                    FeedSubscription.last_fetch_status == "never"
                )
            ) or 0,
        },
    )


@router.get("/sources")
def sources(request: Request, session: SessionDep, message: str | None = None):
    feeds = session.scalars(
        select(FeedSubscription)
        .options(joinedload(FeedSubscription.source))
        .order_by(FeedSubscription.title)
    ).all()
    return templates.TemplateResponse(
        request, "sources.html", {"feeds": feeds, "message": message}
    )


@router.post("/sources/{feed_id}/enable")
def enable_source(feed_id: int, session: SessionDep):
    feed = session.get(FeedSubscription, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    feed.is_enabled = True
    session.commit()
    return redirect("/sources", "Feed enabled")


@router.post("/sources/{feed_id}/disable")
def disable_source(feed_id: int, session: SessionDep):
    feed = session.get(FeedSubscription, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    feed.is_enabled = False
    session.commit()
    return redirect("/sources", "Feed disabled")


@router.post("/sources/{feed_id}/test")
def test_source(feed_id: int, session: SessionDep):
    result = test_feed_by_id(session, feed_id)
    return redirect("/sources", f"Test finished: {result.status}")


@router.post("/fetch/run")
def fetch_run(session: SessionDep):
    run = fetch_enabled_feeds(session, mode="manual")
    return redirect("/sources", f"Fetch run #{run.id}: {run.status}")


@router.get("/feed")
def feed(
    request: Request,
    session: SessionDep,
    language: str | None = None,
    source: str | None = None,
    category: str | None = None,
    multi_article_only: bool = False,
    multi_source_only: bool = False,
):
    filters = {
        "language": language,
        "source": source,
        "category": category,
        "multi_article_only": multi_article_only,
        "multi_source_only": multi_source_only,
    }
    return templates.TemplateResponse(
        request,
        "feed.html",
        {
            "cluster_cards": cluster_cards(session, filters),
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
    setting = session.get(AppSetting, "fetch_interval_minutes")
    value = int(setting.value) if setting else 0
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "value": value,
            "allowed": sorted(ALLOWED_INTERVALS),
            "message": message,
        },
    )


@router.post("/settings")
async def save_settings(request: Request, session: SessionDep):
    form = await request.form()
    fetch_interval_minutes = int(form.get("fetch_interval_minutes", ""))
    if fetch_interval_minutes not in ALLOWED_INTERVALS:
        raise HTTPException(status_code=400, detail="Invalid interval")
    setting = session.get(AppSetting, "fetch_interval_minutes")
    if setting is None:
        setting = AppSetting(
            key="fetch_interval_minutes", value=str(fetch_interval_minutes)
        )
        session.add(setting)
    else:
        setting.value = str(fetch_interval_minutes)
    session.commit()
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
