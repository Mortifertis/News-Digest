from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Article,
    ClusterArticle,
    FeedSubscription,
    NewsSource,
    StoryCluster,
)


def collect_stats(session: Session) -> dict:
    cluster_sizes = (
        select(
            StoryCluster.id.label("cluster_id"),
            StoryCluster.title.label("title"),
            StoryCluster.language.label("language"),
            func.count(ClusterArticle.article_id).label("article_count"),
            func.count(distinct(Article.source_id)).label("source_count"),
        )
        .join(ClusterArticle, ClusterArticle.cluster_id == StoryCluster.id)
        .join(Article, Article.id == ClusterArticle.article_id)
        .group_by(StoryCluster.id)
        .subquery()
    )
    total_clusters = session.scalar(select(func.count(StoryCluster.id))) or 0
    total_articles = session.scalar(select(func.count(Article.id))) or 0
    return {
        "total_sources": session.scalar(select(func.count(NewsSource.id)))
        or 0,
        "total_feeds": session.scalar(select(func.count(FeedSubscription.id)))
        or 0,
        "enabled_feeds": session.scalar(
            select(func.count(FeedSubscription.id)).where(
                FeedSubscription.is_enabled.is_(True)
            )
        )
        or 0,
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
        "total_articles": total_articles,
        "total_clusters": total_clusters,
        "articles_per_source": session.execute(
            select(NewsSource.name, func.count(Article.id))
            .join(Article, Article.source_id == NewsSource.id)
            .group_by(NewsSource.name)
            .order_by(NewsSource.name)
        ).all(),
        "articles_per_language": session.execute(
            select(Article.language, func.count(Article.id))
            .group_by(Article.language)
            .order_by(Article.language)
        ).all(),
        "clusters_per_language": session.execute(
            select(StoryCluster.language, func.count(StoryCluster.id))
            .group_by(StoryCluster.language)
            .order_by(StoryCluster.language)
        ).all(),
        "singleton_clusters": session.scalar(
            select(func.count())
            .select_from(cluster_sizes)
            .where(cluster_sizes.c.article_count == 1)
        )
        or 0,
        "multi_article_clusters": session.scalar(
            select(func.count())
            .select_from(cluster_sizes)
            .where(cluster_sizes.c.article_count > 1)
        )
        or 0,
        "multi_source_clusters": session.scalar(
            select(func.count())
            .select_from(cluster_sizes)
            .where(cluster_sizes.c.source_count > 1)
        )
        or 0,
        "average_articles_per_cluster": (
            total_articles / total_clusters if total_clusters else 0
        ),
        "top_clusters": session.execute(
            select(
                cluster_sizes.c.cluster_id,
                cluster_sizes.c.title,
                cluster_sizes.c.language,
                cluster_sizes.c.article_count,
                cluster_sizes.c.source_count,
            )
            .order_by(
                cluster_sizes.c.article_count.desc(),
                cluster_sizes.c.cluster_id,
            )
            .limit(10)
        ).all(),
    }
