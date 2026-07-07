from datetime import UTC, datetime, timedelta

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Article, ClusterArticle, StoryCluster
from app.services.settings_service import get_int_setting


def article_text(article: Article) -> str:
    return f"{article.normalized_title} {article.normalized_summary}".strip()


def find_exact_cluster(
    session: Session, article: Article
) -> StoryCluster | None:
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


def _candidate_clusters(
    session: Session, article: Article, since: datetime
) -> list[StoryCluster]:
    return list(
        session.scalars(
            select(StoryCluster)
            .join(ClusterArticle)
            .join(Article, Article.id == ClusterArticle.article_id)
            .where(Article.id != article.id)
            .where(Article.language == article.language)
            .where(Article.created_at >= since)
            .order_by(StoryCluster.id)
            .distinct()
        )
    )


def _cluster_articles(
    session: Session, cluster: StoryCluster, article: Article, since: datetime
) -> list[Article]:
    return list(
        session.scalars(
            select(Article)
            .join(ClusterArticle, ClusterArticle.article_id == Article.id)
            .where(ClusterArticle.cluster_id == cluster.id)
            .where(Article.id != article.id)
            .where(Article.language == article.language)
            .where(Article.created_at >= since)
            .order_by(Article.id)
        )
    )


def find_fuzzy_cluster(
    session: Session, article: Article
) -> tuple[StoryCluster, float] | None:
    window_hours = get_int_setting(
        session, "fuzzy_candidate_window_hours", 72
    )
    since = datetime.now(UTC) - timedelta(hours=window_hours)
    best_cluster = None
    best_score = 0.0
    new_text = article_text(article)
    min_length = get_int_setting(session, "min_text_length_for_fuzzy", 100)
    if len(new_text) < min_length:
        return None

    for cluster in _candidate_clusters(session, article, since):
        cluster_best_score = 0.0
        for candidate in _cluster_articles(session, cluster, article, since):
            candidate_text = article_text(candidate)
            if len(candidate_text) < min_length:
                continue
            score = max(
                float(fuzz.token_set_ratio(new_text, candidate_text)),
                float(
                    fuzz.token_set_ratio(
                        article.normalized_title,
                        candidate.normalized_title,
                    )
                ),
            )
            cluster_best_score = max(cluster_best_score, score)
        if cluster_best_score > best_score:
            best_cluster = cluster
            best_score = cluster_best_score

    threshold = get_int_setting(
        session, f"fuzzy_threshold_{article.language}",
        get_int_setting(session, "fuzzy_threshold_default", 82),
    )
    if best_cluster is None or best_score < threshold:
        return None
    return best_cluster, best_score


def match_type_for_exact(session: Session, article: Article) -> str:
    url_exists = session.scalar(
        select(Article.id).where(
            Article.id != article.id,
            Article.canonical_url == article.canonical_url,
        )
    )
    return "exact_url" if url_exists else "exact_hash"
