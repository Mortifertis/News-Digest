from app.db.models import Article
from app.services.deduplicator import article_text


def test_article_text_combines_normalized_title_and_summary() -> None:
    article = Article(
        source_id=1,
        feed_id=1,
        external_id="1",
        title="Title",
        summary="Summary",
        canonical_url="https://example.test/a",
        language="en",
        normalized_title="title",
        normalized_summary="summary",
        text_hash="abc",
        raw_payload_json="{}",
    )
    assert article_text(article) == "title summary"
