from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from time import perf_counter
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Article, FeedSubscription, NewsSource

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 20.0
TOTAL_TIMEOUT = 30.0
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


CANDIDATES: list[SourceCandidate] = []


def _add_catalog() -> None:
    le_monde_terms = (
        "Le Monde RSS feeds are for personal, non-professional, "
        "non-collective use; other uses require authorization."
    )
    data = [
        (
            "BBC News",
            "en",
            "United Kingdom",
            "Europe",
            "https://www.bbc.com/news",
            "public_broadcaster",
            5,
            "center",
            [
                _feed(
                    "BBC World",
                    "https://feeds.bbci.co.uk/news/world/rss.xml",
                    "world",
                    ["world"],
                ),
                _feed(
                    "BBC UK",
                    "https://feeds.bbci.co.uk/news/uk/rss.xml",
                    "politics",
                    ["uk"],
                ),
                _feed(
                    "BBC Business",
                    "https://feeds.bbci.co.uk/news/business/rss.xml",
                    "business",
                    ["business"],
                ),
                _feed(
                    "BBC Technology",
                    "https://feeds.bbci.co.uk/news/technology/rss.xml",
                    "technology",
                    ["technology"],
                ),
                _feed(
                    "BBC Science & Environment",
                    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
                    "science",
                    ["science", "climate"],
                ),
            ],
            "public",
            "free",
        ),
        (
            "The Guardian",
            "en",
            "United Kingdom",
            "Europe",
            "https://www.theguardian.com/international",
            "newspaper",
            5,
            "lean_left",
            [
                _feed(
                    "The Guardian World",
                    "https://www.theguardian.com/world/rss",
                    "world",
                    ["world"],
                ),
                _feed(
                    "The Guardian UK",
                    "https://www.theguardian.com/uk/rss",
                    "politics",
                    ["uk"],
                ),
                _feed(
                    "The Guardian US",
                    "https://www.theguardian.com/us-news/rss",
                    "us",
                    ["us"],
                ),
                _feed(
                    "The Guardian Europe",
                    "https://www.theguardian.com/world/europe-news/rss",
                    "europe",
                    ["europe"],
                ),
                _feed(
                    "The Guardian Technology",
                    "https://www.theguardian.com/technology/rss",
                    "technology",
                    ["technology"],
                ),
                _feed(
                    "The Guardian Business",
                    "https://www.theguardian.com/business/rss",
                    "business",
                    ["business"],
                ),
                _feed(
                    "The Guardian Culture",
                    "https://www.theguardian.com/culture/rss",
                    "culture",
                    ["culture"],
                ),
                _feed(
                    "The Guardian Environment",
                    "https://www.theguardian.com/environment/rss",
                    "climate",
                    ["climate"],
                ),
                _feed(
                    "The Guardian Football",
                    "https://www.theguardian.com/football/rss",
                    "sports",
                    ["football"],
                ),
            ],
            "private",
            "partial",
        ),
        (
            "France 24 English",
            "en",
            "France",
            "Europe",
            "https://www.france24.com/en/",
            "public_broadcaster",
            5,
            "center",
            [
                _feed(
                    "France 24 English",
                    "https://www.france24.com/en/rss",
                    "world",
                    ["world"],
                )
            ],
            "state-funded",
            "free",
        ),
        (
            "Euronews English",
            "en",
            "France",
            "Europe",
            "https://www.euronews.com/",
            "private_broadcaster",
            4,
            "center",
            [
                _feed(
                    "Euronews English News",
                    "https://www.euronews.com/rss?format=mrss&level=theme&name=news",
                    "world",
                    ["world"],
                ),
                _feed(
                    "Euronews English Next",
                    "https://www.euronews.com/rss?format=mrss&level=vertical&name=next",
                    "technology",
                    ["technology"],
                ),
                _feed(
                    "Euronews English Green",
                    "https://www.euronews.com/rss?format=mrss&level=vertical&name=green",
                    "climate",
                    ["climate"],
                ),
            ],
            "mixed",
            "free",
        ),
        (
            "CBC News",
            "en",
            "Canada",
            "North America",
            "https://www.cbc.ca/news",
            "public_broadcaster",
            5,
            "center",
            [
                _feed(
                    "CBC Top Stories",
                    "https://www.cbc.ca/webfeed/rss/rss-topstories",
                    "general",
                    ["canada"],
                ),
                _feed(
                    "CBC World",
                    "https://www.cbc.ca/webfeed/rss/rss-world",
                    "world",
                    ["world"],
                ),
                _feed(
                    "CBC Canada",
                    "https://www.cbc.ca/webfeed/rss/rss-canada",
                    "canada",
                    ["canada"],
                ),
                _feed(
                    "CBC Politics",
                    "https://www.cbc.ca/webfeed/rss/rss-politics",
                    "politics",
                    ["politics"],
                ),
                _feed(
                    "CBC Business",
                    "https://www.cbc.ca/webfeed/rss/rss-business",
                    "business",
                    ["business"],
                ),
                _feed(
                    "CBC Technology & Science",
                    "https://www.cbc.ca/webfeed/rss/rss-technology",
                    "technology",
                    ["technology", "science"],
                ),
            ],
            "public",
            "free",
        ),
        (
            "NPR",
            "en",
            "United States",
            "North America",
            "https://www.npr.org/",
            "public_broadcaster",
            5,
            "center",
            [
                _feed(
                    "NPR News",
                    "https://feeds.npr.org/1001/rss.xml",
                    "general",
                    ["us"],
                ),
                _feed(
                    "NPR World",
                    "https://feeds.npr.org/1004/rss.xml",
                    "world",
                    ["world"],
                ),
                _feed(
                    "NPR Politics",
                    "https://feeds.npr.org/1014/rss.xml",
                    "politics",
                    ["politics"],
                ),
                _feed(
                    "NPR Business",
                    "https://feeds.npr.org/1006/rss.xml",
                    "business",
                    ["business"],
                ),
                _feed(
                    "NPR Technology",
                    "https://feeds.npr.org/1019/rss.xml",
                    "technology",
                    ["technology"],
                ),
                _feed(
                    "NPR Science",
                    "https://feeds.npr.org/1007/rss.xml",
                    "science",
                    ["science"],
                ),
                _feed(
                    "NPR Culture",
                    "https://feeds.npr.org/1008/rss.xml",
                    "culture",
                    ["culture"],
                ),
            ],
            "nonprofit",
            "free",
        ),
        (
            "Al Jazeera English",
            "en",
            "Qatar",
            "Middle East",
            "https://www.aljazeera.com/",
            "private_broadcaster",
            4,
            "state_perspective",
            [
                _feed(
                    "Al Jazeera English",
                    "https://www.aljazeera.com/xml/rss/all.xml",
                    "world",
                    ["world", "middle_east"],
                )
            ],
            "state-funded",
            "free",
        ),
        (
            "Deutsche Welle English",
            "en",
            "Germany",
            "Europe",
            "https://www.dw.com/en/",
            "public_broadcaster",
            5,
            "center",
            [
                _feed(
                    "DW English",
                    "https://rss.dw.com/xml/rss-en-all",
                    "world",
                    ["world", "europe"],
                )
            ],
            "state-funded",
            "free",
        ),
        (
            "France 24 Français",
            "fr",
            "France",
            "Europe",
            "https://www.france24.com/fr/",
            "public_broadcaster",
            5,
            "center",
            [
                _feed(
                    "France 24 Français",
                    "https://www.france24.com/fr/rss",
                    "world",
                    ["world", "france"],
                )
            ],
            "state-funded",
            "free",
        ),
        (
            "Le Monde",
            "fr",
            "France",
            "Europe",
            "https://www.lemonde.fr/",
            "newspaper",
            5,
            "center",
            [
                _feed(
                    "Le Monde International",
                    "https://www.lemonde.fr/international/rss_full.xml",
                    "world",
                    ["world"],
                    terms_note=le_monde_terms,
                ),
                _feed(
                    "Le Monde Politique",
                    "https://www.lemonde.fr/politique/rss_full.xml",
                    "politics",
                    ["politics"],
                    terms_note=le_monde_terms,
                ),
                _feed(
                    "Le Monde Économie",
                    "https://www.lemonde.fr/economie/rss_full.xml",
                    "business",
                    ["business"],
                    terms_note=le_monde_terms,
                ),
                _feed(
                    "Le Monde Culture",
                    "https://www.lemonde.fr/culture/rss_full.xml",
                    "culture",
                    ["culture"],
                    terms_note=le_monde_terms,
                ),
                _feed(
                    "Le Monde Sciences",
                    "https://www.lemonde.fr/sciences/rss_full.xml",
                    "science",
                    ["science"],
                    terms_note=le_monde_terms,
                ),
                _feed(
                    "Le Monde Pixels",
                    "https://www.lemonde.fr/pixels/rss_full.xml",
                    "technology",
                    ["technology"],
                    terms_note=le_monde_terms,
                ),
                _feed(
                    "Le Monde Sport",
                    "https://www.lemonde.fr/sport/rss_full.xml",
                    "sports",
                    ["sports"],
                    terms_note=le_monde_terms,
                ),
                _feed(
                    "Le Monde Planète",
                    "https://www.lemonde.fr/planete/rss_full.xml",
                    "climate",
                    ["climate"],
                    terms_note=le_monde_terms,
                ),
                _feed(
                    "Le Monde Idées",
                    "https://www.lemonde.fr/idees/rss_full.xml",
                    "opinion",
                    ["opinion"],
                    terms_note=le_monde_terms,
                ),
            ],
            "private",
            "partial",
        ),
        (
            "RFI Français",
            "fr",
            "France",
            "Europe",
            "https://www.rfi.fr/fr/",
            "public_broadcaster",
            5,
            "center",
            [
                _feed(
                    "RFI Français",
                    "https://www.rfi.fr/fr/rss",
                    "world",
                    ["world"],
                ),
                _feed(
                    "RFI Afrique",
                    "https://www.rfi.fr/fr/afrique/rss",
                    "africa",
                    ["africa"],
                ),
                _feed(
                    "RFI Europe",
                    "https://www.rfi.fr/fr/europe/rss",
                    "europe",
                    ["europe"],
                ),
                _feed(
                    "RFI France",
                    "https://www.rfi.fr/fr/france/rss",
                    "france",
                    ["france"],
                ),
                _feed(
                    "RFI Économie",
                    "https://www.rfi.fr/fr/economie/rss",
                    "business",
                    ["business"],
                ),
                _feed(
                    "RFI Culture",
                    "https://www.rfi.fr/fr/culture/rss",
                    "culture",
                    ["culture"],
                ),
                _feed(
                    "RFI Sports",
                    "https://www.rfi.fr/fr/sports/rss",
                    "sports",
                    ["sports"],
                ),
                _feed(
                    "RFI Science",
                    "https://www.rfi.fr/fr/science/rss",
                    "science",
                    ["science"],
                ),
            ],
            "state-funded",
            "free",
        ),
        (
            "Euronews Français",
            "fr",
            "France",
            "Europe",
            "https://fr.euronews.com/",
            "private_broadcaster",
            4,
            "center",
            [
                _feed(
                    "Euronews Français Info",
                    "https://fr.euronews.com/rss?format=mrss&level=theme&name=news",
                    "world",
                    ["world"],
                ),
                _feed(
                    "Euronews Français Next",
                    "https://fr.euronews.com/rss?format=mrss&level=vertical&name=next",
                    "technology",
                    ["technology"],
                ),
                _feed(
                    "Euronews Français Green",
                    "https://fr.euronews.com/rss?format=mrss&level=vertical&name=green",
                    "climate",
                    ["climate"],
                ),
            ],
            "mixed",
            "free",
        ),
    ]
    for row in data:
        CANDIDATES.extend(_source(*row[:9], ownership=row[9], paywall=row[10]))


_add_catalog()
VERIFIED_FEEDS = validate_candidate_catalog(CANDIDATES)


def probe_candidate(
    candidate: SourceCandidate, client: httpx.Client
) -> ProbeResult:
    start = perf_counter()
    if not candidate.feed_url:
        return ProbeResult(candidate, "failed", None, 0.0, 0, FIND_RSS)
    http_status = None
    entries_count = 0
    error = None
    status = "failed"
    try:
        response = client.get(candidate.feed_url)
        http_status = response.status_code
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        entries_count = len(parsed.entries)
        if http_status == 200 and entries_count > 0:
            status = "success"
        else:
            error = "Feed returned zero parsed entries."
        if parsed.bozo and error is None:
            error = f"Feed parse warning: {parsed.bozo_exception}"
    except Exception as exc:
        error = str(exc)
    return ProbeResult(
        candidate,
        status,
        http_status,
        perf_counter() - start,
        entries_count,
        error,
    )


def probe_candidates(
    candidates: list[SourceCandidate] | None = None,
) -> list[ProbeResult]:
    timeout = httpx.Timeout(
        TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT, read=READ_TIMEOUT
    )
    results = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for candidate in candidates or VERIFIED_FEEDS:
            results.append(probe_candidate(candidate, client))
    return results


def _apply_source(source: NewsSource, c: SourceCandidate) -> None:
    primary_language = c.language_primary or c.language
    source.language = primary_language
    source.display_name = c.display_name or c.source_name
    source.language_primary = primary_language
    source.languages_available = ",".join(
        c.languages_available or [primary_language]
    )
    source.country = c.country
    source.region = c.region
    source.homepage_url = c.homepage_url
    source.outlet_type = c.outlet_type
    source.ownership_type = c.ownership_type
    source.paywall_level = c.paywall_level
    source.editorial_reliability_score = c.editorial_reliability_score
    source.bias_profile = c.bias_profile
    source.rating_confidence = c.rating_confidence
    source.rating_notes = c.rating_notes
    source.default_priority = c.default_priority
    source.tags = ",".join(c.tags)


def upsert_candidate(session: Session, c: SourceCandidate) -> bool:
    source = session.scalar(
        select(NewsSource).where(NewsSource.name == c.source_name)
    )
    if source is None:
        source = NewsSource(
            name=c.source_name,
            language=c.language_primary or c.language,
            country=c.country,
            homepage_url=c.homepage_url,
        )
        session.add(source)
        session.flush()
    _apply_source(source, c)
    stmt = select(FeedSubscription).where(
        FeedSubscription.title == c.feed_title
    )
    if c.feed_url:
        stmt = select(FeedSubscription).where(
            FeedSubscription.feed_url == c.feed_url
        )
    feed = session.scalar(stmt)
    created = feed is None
    if feed is None:
        feed = FeedSubscription(
            source_id=source.id,
            title=c.feed_title,
            feed_url=c.feed_url,
            category=c.category,
            language=c.language,
        )
        session.add(feed)
    feed.source_id = source.id
    feed.title = c.feed_title
    feed.feed_url = c.feed_url
    feed.category = c.category
    feed.language = c.language
    feed.tags = ",".join(c.feed_tags)
    feed.is_official_url = c.is_official_url
    feed.url_confidence = c.url_confidence
    feed.enabled_by_default = c.enabled_by_default
    feed.notes = c.notes
    feed.rss_url_status = c.rss_url_status
    feed.rss_url_checked_at = c.rss_url_checked_at
    feed.rss_url_source_note = c.rss_url_source_note
    feed.terms_note = c.terms_note
    feed.fetchable = c.fetchable and bool(c.feed_url)
    feed.is_enabled = c.enabled_by_default and bool(c.feed_url)
    return created


class FeedLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "link":
            return
        attr = {k.lower(): v for k, v in attrs if v is not None}
        rel = attr.get("rel", "").lower()
        typ = attr.get("type", "").lower()
        href = attr.get("href")
        if (
            href
            and "alternate" in rel
            and typ in {"application/rss+xml", "application/atom+xml"}
        ):
            self.links.append(urljoin(self.base_url, href))


def discover_feed_urls_from_html(html: str, base_url: str) -> list[str]:
    parser = FeedLinkParser(base_url)
    parser.feed(html)
    return parser.links


def discover_feed_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return []
    try:
        response = httpx.get(url, timeout=TOTAL_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    return discover_feed_urls_from_html(response.text, str(response.url))


def save_discovered_feed_url(
    session: Session, feed_id: int, feed_url: str
) -> None:
    feed = session.get(FeedSubscription, feed_id)
    if feed is None:
        raise ValueError(f"Feed not found: {feed_id}")
    feed.feed_url = feed_url.strip()
    feed.rss_url_status = "candidate_pattern"
    feed.rss_url_source_note = "Saved from RSS autodiscovery."
    feed.fetchable = bool(feed.feed_url)
    session.commit()


def probe_feed_urls(session: Session) -> dict[str, int]:
    feeds = session.scalars(select(FeedSubscription)).all()
    summary = {
        "checked": 0,
        "success": 0,
        "failed": 0,
        "needs_url": 0,
        "api_or_licensed_only": 0,
    }
    for feed in feeds:
        if feed.rss_url_status == "api_or_licensed_only":
            summary["api_or_licensed_only"] += 1
            feed.fetchable = False
            continue
        if not feed.feed_url:
            summary["needs_url"] += 1
            feed.fetchable = False
            if feed.rss_url_status not in {
                "unavailable",
                "api_or_licensed_only",
            }:
                feed.rss_url_status = "needs_verification"
            continue
        summary["checked"] += 1
        feed.rss_url_checked_at = datetime.now(UTC)
        with httpx.Client(
            timeout=httpx.Timeout(
                TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT, read=READ_TIMEOUT
            ),
            follow_redirects=True,
        ) as client:
            result = probe_candidate(
                SourceCandidate(
                    source_name=feed.source.name,
                    language=feed.language,
                    country=feed.source.country,
                    homepage_url=feed.source.homepage_url,
                    feed_title=feed.title,
                    feed_url=feed.feed_url,
                    category=feed.category,
                ),
                client,
            )
        feed.last_fetch_status = result.status
        feed.last_http_status = result.http_status
        feed.last_entries_count = result.entries_count
        feed.last_fetch_error = result.error
        if result.is_success:
            summary["success"] += 1
            if feed.rss_url_status in {
                "candidate_pattern",
                "verified_official",
            }:
                feed.rss_url_status = "verified_official"
            feed.fetchable = True
        else:
            summary["failed"] += 1
            feed.fetchable = False
    session.commit()
    return summary


PLACEHOLDER_FILTER = or_(
    FeedSubscription.feed_url.is_(None),
    FeedSubscription.feed_url == "",
    FeedSubscription.feed_url.ilike("%example.com%"),
    NewsSource.homepage_url.ilike("%example.com%"),
)


def report_placeholder_sources(session: Session) -> dict[str, object]:
    empty_feeds = session.scalars(
        select(FeedSubscription)
        .join(NewsSource)
        .where(
            or_(
                FeedSubscription.feed_url.is_(None),
                FeedSubscription.feed_url == "",
            )
        )
    ).all()
    example_sources = session.scalars(
        select(NewsSource).where(
            NewsSource.homepage_url.ilike("%example.com%")
        )
    ).all()
    api_feeds = session.scalars(
        select(FeedSubscription).where(
            FeedSubscription.rss_url_status == "api_or_licensed_only"
        )
    ).all()
    bad_feed_ids = {
        feed.id
        for feed in session.scalars(
            select(FeedSubscription).join(NewsSource).where(PLACEHOLDER_FILTER)
        ).all()
    }
    bad_feed_ids.update(feed.id for feed in api_feeds)
    return {
        "empty_feed_titles": [feed.title for feed in empty_feeds],
        "example_source_names": [source.name for source in example_sources],
        "api_or_licensed_only_titles": [feed.title for feed in api_feeds],
        "total_non_operational_feeds": len(bad_feed_ids),
    }


def cleanup_placeholder_sources(
    session: Session, *, force: bool = False
) -> dict[str, int]:
    feeds = session.scalars(
        select(FeedSubscription).join(NewsSource).where(PLACEHOLDER_FILTER)
    ).all()
    feeds_deleted = 0
    skipped = 0
    for feed in feeds:
        article_count = session.scalar(
            select(func.count(Article.id)).where(Article.feed_id == feed.id)
        )
        if article_count and not force:
            skipped += 1
            continue
        session.delete(feed)
        feeds_deleted += 1
    session.flush()
    sources = session.scalars(select(NewsSource)).all()
    sources_deleted = 0
    for source in sources:
        feed_count = session.scalar(
            select(func.count(FeedSubscription.id)).where(
                FeedSubscription.source_id == source.id
            )
        )
        if feed_count == 0:
            session.delete(source)
            sources_deleted += 1
    session.commit()
    return {
        "feeds_deleted": feeds_deleted,
        "sources_deleted": sources_deleted,
        "skipped_due_to_existing_articles": skipped,
    }


def seed_all_candidate_sources(session: Session) -> int:
    count = 0
    for candidate in validate_candidate_catalog(VERIFIED_FEEDS):
        if upsert_candidate(session, candidate):
            count += 1
    session.commit()
    return count


def seed_verified_feeds(session: Session) -> list[ProbeResult]:
    results = probe_candidates(VERIFIED_FEEDS)
    for result in results:
        if not result.is_success:
            continue
        upsert_candidate(session, result.candidate)
        feed = session.scalar(
            select(FeedSubscription).where(
                FeedSubscription.feed_url == result.candidate.feed_url
            )
        )
        if feed is not None:
            feed.last_fetch_status = result.status
            feed.last_http_status = result.http_status
            feed.last_entries_count = result.entries_count
            feed.last_fetch_error = result.error
            feed.fetchable = True
    session.commit()
    return results


def seed_accessible_sources(session: Session) -> list[ProbeResult]:
    results = probe_candidates()
    for result in results:
        if result.candidate.feed_url:
            upsert_candidate(session, result.candidate)
        if result.candidate.feed_url:
            feed = session.scalar(
                select(FeedSubscription).where(
                    FeedSubscription.feed_url == result.candidate.feed_url
                )
            )
        else:
            feed = session.scalar(
                select(FeedSubscription).where(
                    FeedSubscription.title == result.candidate.feed_title
                )
            )
        if feed is not None:
            feed.is_enabled = result.is_success
            feed.last_fetch_status = result.status
            feed.last_http_status = result.http_status
            feed.last_entries_count = result.entries_count
            feed.last_new_articles_count = 0
            feed.last_skipped_articles_count = 0
            feed.last_fetch_error = result.error
    session.commit()
    return results
