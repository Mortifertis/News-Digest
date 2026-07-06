from datetime import UTC, datetime, timedelta

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Article, ClusterArticle, StoryCluster


def article_text(article: Article) -> str:
    return f"{article.normalized_title} {article.normalized_summary}".strip()


def find_exact_cluster(session: Session, article: Article) -> StoryCluster | None:
    duplicate = session.scalar(
        select(Article)
        .join(ClusterArticle, ClusterArticle.article_id == Article.id)
        .where(Article.id != article.id)
        .where(
            (Article.canonical_url == article.canonical_url)
            | (Article.text_hash == article.text_hash)
        )
        .order_by(Article.id)
        .limit(1)
    )
    if duplicate is None:
        return None
    return session.scalar(
        select(StoryCluster)
        .join(ClusterArticle)
        .where(ClusterArticle.article_id == duplicate.id)
    )


def find_fuzzy_cluster(
    session: Session, article: Article
) -> tuple[StoryCluster, float] | None:
    settings = get_settings()
    since = datetime.now(UTC) - timedelta(hours=settings.fuzzy_lookback_hours)
    candidates = session.scalars(
        select(Article)
        .join(ClusterArticle, ClusterArticle.article_id == Article.id)
        .where(Article.id != article.id)
        .where(Article.language == article.language)
        .where(Article.created_at >= since)
    ).all()
    best_article = None
    best_score = 0.0
    for candidate in candidates:
        score = float(
            fuzz.token_set_ratio(article_text(article), article_text(candidate))
        )
        if score > best_score:
            best_article = candidate
            best_score = score
    if best_article is None or best_score < settings.fuzzy_duplicate_threshold:
        return None
    cluster = session.scalar(
        select(StoryCluster)
        .join(ClusterArticle)
        .where(ClusterArticle.article_id == best_article.id)
    )
    if cluster is None:
        return None
    return cluster, best_score


def match_type_for_exact(session: Session, article: Article) -> str:
    url_exists = session.scalar(
        select(Article.id).where(
            Article.id != article.id,
            Article.canonical_url == article.canonical_url,
        )
    )
    return "exact_url" if url_exists else "exact_hash"
