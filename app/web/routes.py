from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    Article,
    ClusterArticle,
    FeedSubscription,
    NewsSource,
    StoryCluster,
)
from app.db.session import get_session

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
templates = Jinja2Templates(directory="app/web/templates")


def cluster_cards(session: Session):
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
    clusters = (
        session.scalars(
            select(StoryCluster)
            .options(
                joinedload(StoryCluster.lead_article).joinedload(
                    Article.source
                ),
                joinedload(StoryCluster.articles)
                .joinedload(ClusterArticle.article)
                .joinedload(Article.source),
            )
            .order_by(StoryCluster.last_seen_at.desc())
        )
        .unique()
        .all()
    )
    return [
        (cluster, source_counts.get(cluster.id, 0)) for cluster in clusters
    ]


@router.get("/")
def dashboard(request: Request, session: SessionDep):
    latest = session.scalar(select(func.max(FeedSubscription.last_fetched_at)))
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "total_sources": session.scalar(select(func.count(NewsSource.id))),
            "total_feeds": session.scalar(
                select(func.count(FeedSubscription.id))
            ),
            "total_articles": session.scalar(select(func.count(Article.id))),
            "total_clusters": session.scalar(
                select(func.count(StoryCluster.id))
            ),
            "latest_fetch_time": latest,
        },
    )


@router.get("/sources")
def sources(request: Request, session: SessionDep):
    feeds = session.scalars(
        select(FeedSubscription)
        .options(joinedload(FeedSubscription.source))
        .order_by(FeedSubscription.title)
    ).all()
    return templates.TemplateResponse(
        request, "sources.html", {"feeds": feeds}
    )


@router.get("/feed")
def feed(request: Request, session: SessionDep):
    return templates.TemplateResponse(
        request, "feed.html", {"cluster_cards": cluster_cards(session)}
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
