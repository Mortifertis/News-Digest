from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"([!?.,;:])\1+")
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ")


def normalize_text(value: str | None, *, html: bool = False) -> str:
    text = strip_html(value) if html else value or ""
    text = text.lower().strip()
    text = _PUNCT_RE.sub(r"\1", text)
    return _SPACE_RE.sub(" ", text)


def normalize_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PREFIXES)
        and key.lower() not in _TRACKING_KEYS
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(query, doseq=True),
            "",
        )
    )


def text_hash(normalized_title: str, normalized_summary: str) -> str:
    payload = f"{normalized_title}\n{normalized_summary}".encode()
    return hashlib.sha256(payload).hexdigest()


def normalize_article_fields(
    title: str | None, summary: str | None, url: str | None
) -> dict[str, str]:
    normalized_title = normalize_text(title)
    normalized_summary = normalize_text(summary, html=True)
    return {
        "title": (title or "").strip(),
        "summary": strip_html(summary).strip(),
        "canonical_url": normalize_url(url),
        "normalized_title": normalized_title,
        "normalized_summary": normalized_summary,
        "text_hash": text_hash(normalized_title, normalized_summary),
    }
