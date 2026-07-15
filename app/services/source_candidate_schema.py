from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

NEEDS_VERIFICATION = "Needs verification"
FIND_RSS = "Find official RSS URL"


@dataclass(frozen=True)
class SourceCandidate:
    source_name: str
    language: str
    country: str
    homepage_url: str
    feed_title: str
    feed_url: str | None
    category: str
    priority: int = 1
    notes: str = NEEDS_VERIFICATION
    enabled_by_default: bool = False
    display_name: str | None = None
    language_primary: str | None = None
    languages_available: list[str] = field(default_factory=list)
    region: str = ""
    outlet_type: str = "digital_native"
    ownership_type: str = "unknown"
    paywall_level: str = "unknown"
    editorial_reliability_score: int = 3
    bias_profile: str = "unknown"
    rating_confidence: str = "low"
    rating_notes: str = "Heuristic placeholder; verify before production use."
    default_priority: int = 3
    tags: list[str] = field(default_factory=list)
    feed_tags: list[str] = field(default_factory=list)
    is_official_url: str = "unknown"
    url_confidence: str = "low"
    rss_url_status: str = "needs_verification"
    rss_url_checked_at: datetime | None = None
    rss_url_source_note: str | None = None
    terms_note: str | None = None
    fetchable: bool = False


@dataclass
class ProbeResult:
    candidate: SourceCandidate
    status: str
    http_status: int | None
    elapsed_seconds: float
    entries_count: int
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status == "success"


def _feed(
    title: str,
    url: str | None,
    category: str,
    tags: list[str],
    *,
    official: str = "true",
    confidence: str = "high",
    notes: str = NEEDS_VERIFICATION,
    status: str | None = None,
    terms_note: str | None = None,
) -> dict:
    if not url:
        official = "unknown"
        confidence = "low"
        notes = FIND_RSS
    if status is None:
        if url and confidence == "high":
            status = "verified_official"
        else:
            status = "needs_verification"
    fetchable = bool(url) and status not in {
        "api_or_licensed_only",
        "unavailable",
    }
    return {
        "feed_title": title,
        "feed_url": url,
        "category": category,
        "feed_tags": tags,
        "is_official_url": official,
        "url_confidence": confidence,
        "notes": notes,
        "rss_url_status": status,
        "rss_url_source_note": notes,
        "terms_note": terms_note,
        "fetchable": fetchable,
    }


def _source(
    name: str,
    lang: str,
    country: str,
    region: str,
    home: str,
    outlet_type: str,
    reliability: int,
    bias: str,
    feeds: list[dict],
    *,
    ownership: str = "private",
    paywall: str = "partial",
    confidence: str = "medium",
    tags: list[str] | None = None,
) -> list[SourceCandidate]:
    base_tags = tags or ["world", "politics"]
    return [
        SourceCandidate(
            source_name=name,
            language=lang,
            country=country,
            homepage_url=home,
            display_name=name,
            language_primary=lang,
            languages_available=[lang],
            region=region,
            outlet_type=outlet_type,
            ownership_type=ownership,
            paywall_level=paywall,
            editorial_reliability_score=reliability,
            bias_profile=bias,
            rating_confidence=confidence,
            rating_notes=(
                "Editorial reliability heuristic, not a truth guarantee. "
                "Verify before production use."
            ),
            default_priority=max(1, min(5, reliability)),
            priority=max(1, min(5, reliability)),
            tags=base_tags,
            enabled_by_default=False,
            **feed,
        )
        for feed in feeds
    ]


def validate_candidate_catalog(
    candidates: list[SourceCandidate],
) -> list[SourceCandidate]:
    errors: list[str] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()
    for candidate in candidates:
        feed_url = (candidate.feed_url or "").strip()
        homepage_url = candidate.homepage_url.strip()
        key = (candidate.source_name, candidate.feed_title)
        if not feed_url:
            errors.append(f"{candidate.feed_title}: feed_url is empty")
        if feed_url and not feed_url.startswith(("http://", "https://")):
            errors.append(f"{candidate.feed_title}: feed_url is not HTTP(S)")
        if "example.com" in homepage_url.lower():
            errors.append(
                f"{candidate.source_name}: homepage_url is example.com"
            )
        if "example.com" in feed_url.lower():
            errors.append(f"{candidate.feed_title}: feed_url is example.com")
        if key in seen_keys:
            errors.append(
                f"{candidate.source_name}/{candidate.feed_title}: duplicate"
            )
        seen_keys.add(key)
        if feed_url in seen_urls:
            errors.append(f"{candidate.feed_title}: duplicate feed_url")
        seen_urls.add(feed_url)
        if candidate.rss_url_status == "api_or_licensed_only":
            errors.append(f"{candidate.feed_title}: api_or_licensed_only feed")
    if errors:
        raise ValueError(
            "Invalid source candidate catalog: " + "; ".join(errors)
        )
    return candidates
