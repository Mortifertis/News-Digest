from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FeedSubscription, NewsSource

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
) -> dict:
    if not url:
        official = "unknown"
        confidence = "low"
        notes = FIND_RSS
    return {
        "feed_title": title,
        "feed_url": url,
        "category": category,
        "feed_tags": tags,
        "is_official_url": official,
        "url_confidence": confidence,
        "notes": notes,
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


CANDIDATES: list[SourceCandidate] = []


def _add_catalog() -> None:
    data = [
        (
            "Reuters",
            "en",
            "United Kingdom",
            "Global",
            "https://www.reuters.com/",
            "newswire",
            5,
            "center",
            [
                _feed(
                    "Reuters licensed/API candidate",
                    None,
                    "general",
                    ["world"],
                    notes=(
                        "Reuters content often requires licensed access; "
                        "use only if official public feed/API is configured."
                    ),
                )
            ],
            "private",
            "unknown",
        ),
        (
            "Associated Press",
            "en",
            "United States",
            "North America",
            "https://apnews.com/",
            "newswire",
            5,
            "center",
            [
                _feed(
                    "AP News licensed/API candidate",
                    None,
                    "general",
                    ["world"],
                    notes="AP content/API may require licensed access.",
                )
            ],
            "nonprofit",
            "unknown",
        ),
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
                    "BBC Technology",
                    "https://feeds.bbci.co.uk/news/technology/rss.xml",
                    "technology",
                    ["technology"],
                ),
                _feed(
                    "BBC Science",
                    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
                    "science",
                    ["science", "climate"],
                ),
                _feed(
                    "BBC Business",
                    "https://feeds.bbci.co.uk/news/business/rss.xml",
                    "business",
                    ["business"],
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
                    "Euronews World",
                    "https://www.euronews.com/rss?format=mrss&level=theme&name=news",
                    "world",
                    ["world"],
                ),
                _feed(
                    "Euronews Next",
                    "https://www.euronews.com/rss?format=mrss&level=vertical&name=next",
                    "technology",
                    ["technology"],
                ),
                _feed(
                    "Euronews Green",
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
    ]
    english_more = [
        "CNN",
        "Fox News",
        "NBC News",
        "CBS News",
        "ABC News",
        "USA Today",
        "Washington Post",
        "New York Times",
        "Wall Street Journal",
        "Politico",
        "Axios",
        "The Hill",
        "Financial Times",
        "The Economist",
        "Bloomberg",
        "CNBC",
        "Sky News",
        "The Independent",
        "The Telegraph",
        "The Times / Sunday Times",
        "Globe and Mail",
        "Toronto Star",
        "National Post",
        "CTV News",
        "Global News",
        "The Conversation",
        "Ars Technica",
        "Wired",
        "The Verge",
        "TechCrunch",
        "MIT Technology Review",
        "Nature News",
        "Science.org News",
        "Scientific American",
        "New Scientist",
        "Space.com",
    ]
    french_more = [
        "France 24 Français",
        "RFI Français",
        "Le Monde",
        "Franceinfo",
        "France Inter",
        "Radio-Canada",
        "TV5MONDE",
        "Euronews Français",
        "SWI swissinfo.ch Français",
        "Le Figaro",
        "Libération",
        "Les Échos",
        "La Croix",
        "L’Obs",
        "L’Express",
        "Le Point",
        "Marianne",
        "Mediapart",
        "Courrier international",
        "Ouest-France",
        "20 Minutes",
        "Le Parisien",
        "HuffPost France",
        "Slate France",
        "Challenges",
        "Alternatives Économiques",
        "Public Sénat",
        "BFMTV",
        "TF1 Info",
        "Usbek & Rica",
        "Numerama",
        "Frandroid",
        "Futura Sciences",
        "Sciences et Avenir",
        "Pour la Science",
        "Actu-Environnement",
        "Connaissance des Arts",
        "Télérama",
    ]
    for row in data:
        CANDIDATES.extend(
            _source(
                *row[:9],
                ownership=row[9],
                paywall=row[10],
            )
        )
    for name in english_more:
        score = 5 if name in {"Financial Times", "New York Times"} else 4
        bias = (
            "lean_right"
            if name in {"Fox News", "Wall Street Journal"}
            else "center"
        )
        if name in {"CNN", "Fox News", "CNBC", "BFMTV"}:
            score = 3
        CANDIDATES.extend(
            _source(
                name,
                "en",
                "United States",
                "North America",
                f"https://www.example.com/{name.lower().replace(' ', '-')}",
                "digital_native",
                score,
                bias,
                [
                    _feed(
                        f"{name} RSS candidate",
                        None,
                        "general",
                        ["world", "us"],
                    )
                ],
            )
        )
    for name in french_more:
        score = (
            5
            if name
            in {
                "France 24 Français",
                "RFI Français",
                "Le Monde",
                "Radio-Canada",
                "Euronews Français",
            }
            else 4
        )
        if name in {"BFMTV", "20 Minutes"}:
            score = 3
        CANDIDATES.extend(
            _source(
                name,
                "fr",
                "France",
                "Europe",
                f"https://www.example.com/{name.lower().replace(' ', '-')}",
                "newspaper",
                score,
                "center",
                [
                    _feed(
                        f"{name} RSS candidate",
                        None,
                        "general",
                        ["france", "world"],
                    )
                ],
            )
        )


_add_catalog()


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
        for candidate in candidates or CANDIDATES:
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
    feed.is_enabled = c.enabled_by_default and bool(c.feed_url)
    return created


def seed_all_candidate_sources(session: Session) -> int:
    count = 0
    for candidate in CANDIDATES:
        if upsert_candidate(session, candidate):
            count += 1
    session.commit()
    return count


def seed_accessible_sources(session: Session) -> list[ProbeResult]:
    results = probe_candidates()
    for result in results:
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
