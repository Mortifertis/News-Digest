from app.services.normalizer import (
    normalize_article_fields,
    normalize_text,
    normalize_url,
)


def test_normalize_text_strips_html_and_punctuation() -> None:
    assert (
        normalize_text(" <p>Hello!!!   World</p> ", html=True)
        == "hello! world"
    )


def test_normalize_url_removes_tracking_and_fragment() -> None:
    url = "HTTPS://Example.com/news/?utm_source=x&id=1#section"
    assert normalize_url(url) == "https://example.com/news?id=1"


def test_normalize_article_fields_hash_is_stable() -> None:
    first = normalize_article_fields(
        "Title", "<b>Summary</b>", "https://x.test?a=1"
    )
    second = normalize_article_fields(
        " title ", "Summary", "https://x.test?a=1#x"
    )
    assert first["text_hash"] == second["text_hash"]
