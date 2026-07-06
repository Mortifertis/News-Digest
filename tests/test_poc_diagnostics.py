import json
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Article,
    Base,
    ClusterArticle,
    FeedSubscription,
    NewsSource,
)
from app.services.cluster_service import cluster_articles
from app.services.demo_loader import load_demo_articles
from app.services.normalizer import normalize_article_fields, normalize_url


def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def add_article(
    session, *, title, summary, url, source_name="Test", language="en"
):
    source = session.scalar(
        select(NewsSource).where(NewsSource.name == source_name)
    )
    if source is None:
        source = NewsSource(
            name=source_name,
            language=language,
            country="Test",
            homepage_url="https://example.test",
        )
        session.add(source)
        session.flush()
    feed = session.scalar(
        select(FeedSubscription).where(FeedSubscription.source_id == source.id)
    )
    if feed is None:
        feed = FeedSubscription(
            source_id=source.id,
            title=f"{source_name} Feed",
            feed_url=f"https://example.test/{source_name}.xml",
            category="test",
            language=language,
        )
        session.add(feed)
        session.flush()
    fields = normalize_article_fields(title, summary, url)
    article = Article(
        source_id=source.id,
        feed_id=feed.id,
        external_id=fields["canonical_url"],
        title=fields["title"],
        summary=fields["summary"],
        canonical_url=fields["canonical_url"],
        published_at=None,
        language=language,
        normalized_title=fields["normalized_title"],
        normalized_summary=fields["normalized_summary"],
        text_hash=fields["text_hash"],
        raw_payload_json=json.dumps({}),
    )
    session.add(article)
    session.commit()
    return article


def test_url_canonicalization_removes_utm_params_and_fragments():
    url = "https://Example.test/a?utm_source=x&id=9&utm_medium=y#part"
    assert normalize_url(url) == "https://example.test/a?id=9"


def test_text_normalization_removes_html_from_summary():
    fields = normalize_article_fields(
        "Title", "<p>Hello <b>World</b></p>", "https://x.test"
    )
    assert fields["summary"] == "Hello World"
    assert fields["normalized_summary"] == "hello world"


def test_repeated_import_does_not_duplicate_articles():
    with session_factory() as session:
        load_demo_articles(session)
        load_demo_articles(session, reset=False)
        assert session.scalar(select(func.count(Article.id))) == 12


def test_repeated_clustering_does_not_duplicate_cluster_article_rows():
    with session_factory() as session:
        add_article(
            session,
            title="Same story",
            summary="Same summary",
            url="https://example.test/story",
        )
        cluster_articles(session)
        cluster_articles(session)
        assert session.scalar(select(func.count(ClusterArticle.id))) == 1


def test_demo_similar_english_articles_are_clustered_together():
    with session_factory() as session:
        load_demo_articles(session)
        rows = (
            session.execute(
                select(ClusterArticle.cluster_id)
                .join(Article, Article.id == ClusterArticle.article_id)
                .where(
                    Article.external_id.in_(
                        ["en-space-1", "en-space-2", "en-space-3"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(set(rows)) == 1


def test_demo_similar_french_articles_are_clustered_together():
    with session_factory() as session:
        load_demo_articles(session)
        rows = (
            session.execute(
                select(ClusterArticle.cluster_id)
                .join(Article, Article.id == ClusterArticle.article_id)
                .where(
                    Article.external_id.in_(
                        ["fr-rail-1", "fr-rail-2", "fr-rail-3"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(set(rows)) == 1


def test_unrelated_demo_articles_remain_separate():
    with session_factory() as session:
        load_demo_articles(session)
        fixture = json.loads(Path("fixtures/demo_articles.json").read_text())
        unrelated = [item["external_id"] for item in fixture[6:]]
        rows = (
            session.execute(
                select(ClusterArticle.cluster_id)
                .join(Article, Article.id == ClusterArticle.article_id)
                .where(Article.external_id.in_(unrelated))
            )
            .scalars()
            .all()
        )
        assert len(rows) == 6
        assert len(set(rows)) == 6


def test_demo_loads_twelve_articles_and_eight_clusters():
    from app.db.models import StoryCluster

    with session_factory() as session:
        load_demo_articles(session)
        assert session.scalar(select(func.count(Article.id))) == 12
        assert session.scalar(select(func.count(StoryCluster.id))) == 8


def test_related_demo_pairwise_scores_are_above_threshold():
    from itertools import combinations

    from rapidfuzz import fuzz

    from app.core.config import get_settings

    fixture = json.loads(Path("fixtures/demo_articles.json").read_text())
    related_ids = {
        "en-space-1",
        "en-space-2",
        "en-space-3",
        "fr-rail-1",
        "fr-rail-2",
        "fr-rail-3",
    }
    texts = {}
    languages = {}
    for item in fixture:
        if item["external_id"] in related_ids:
            fields = normalize_article_fields(
                item["title"], item["summary"], item["url"]
            )
            texts[item["external_id"]] = (
                f"{fields['normalized_title']} {fields['normalized_summary']}"
            ).strip()
            languages[item["external_id"]] = item["language"]

    threshold = get_settings().fuzzy_duplicate_threshold
    for left_id, right_id in combinations(texts, 2):
        if languages[left_id] == languages[right_id]:
            score = fuzz.token_set_ratio(texts[left_id], texts[right_id])
            assert score >= threshold
            assert score >= 78


def test_unrelated_demo_pairwise_scores_stay_below_threshold():
    from rapidfuzz import fuzz

    from app.core.config import get_settings

    fixture = json.loads(Path("fixtures/demo_articles.json").read_text())
    related_ids = {
        "en-space-1",
        "en-space-2",
        "en-space-3",
        "fr-rail-1",
        "fr-rail-2",
        "fr-rail-3",
    }
    threshold = get_settings().fuzzy_duplicate_threshold
    texts = []
    for item in fixture:
        fields = normalize_article_fields(
            item["title"], item["summary"], item["url"]
        )
        text = (
            f"{fields['normalized_title']} {fields['normalized_summary']}"
        ).strip()
        texts.append((item["external_id"], item["language"], text))

    for item_id, language, text in texts:
        if item_id in related_ids:
            continue
        for related_id, related_language, related_text in texts:
            if related_id not in related_ids or language != related_language:
                continue
            score = fuzz.token_set_ratio(text, related_text)
            assert score < threshold
            assert score < 60
