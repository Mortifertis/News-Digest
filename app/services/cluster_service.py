from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Article, ClusterArticle, StoryCluster
from app.services.deduplicator import (
    find_exact_cluster,
    find_fuzzy_cluster,
    match_type_for_exact,
)


def cluster_articles(session: Session) -> int:
    articles = session.scalars(
        select(Article)
        .outerjoin(ClusterArticle, ClusterArticle.article_id == Article.id)
        .where(ClusterArticle.id.is_(None))
        .order_by(Article.published_at.nulls_last(), Article.created_at)
    ).all()
    processed = 0
    for article in articles:
        cluster = find_exact_cluster(session, article)
        if cluster is not None:
            match_type = match_type_for_exact(session, article)
            score = 100.0
        else:
            fuzzy = find_fuzzy_cluster(session, article)
            if fuzzy is not None:
                cluster, score = fuzzy
                match_type = "fuzzy_title"
            else:
                seen = (
                    article.published_at
                    or article.created_at
                    or datetime.now(UTC)
                )
                cluster = StoryCluster(
                    title=article.title,
                    lead_article_id=article.id,
                    language=article.language,
                    first_seen_at=seen,
                    last_seen_at=seen,
                )
                session.add(cluster)
                session.flush()
                score = 100.0
                match_type = "exact_hash"
        seen_at = (
            article.published_at or article.created_at or datetime.now(UTC)
        )
        cluster.first_seen_at = min(cluster.first_seen_at, seen_at)
        cluster.last_seen_at = max(cluster.last_seen_at, seen_at)
        session.add(
            ClusterArticle(
                cluster_id=cluster.id,
                article_id=article.id,
                similarity_score=score,
                match_type=match_type,
            )
        )
        processed += 1
    session.commit()
    return processed
