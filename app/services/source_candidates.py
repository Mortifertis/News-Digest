from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FeedSubscription, NewsSource

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 20.0
TOTAL_TIMEOUT = 30.0


@dataclass(frozen=True)
class SourceCandidate:
    source_name: str
    language: str
    country: str
    homepage_url: str
    feed_title: str
    feed_url: str
    category: str
    priority: int
    notes: str
    enabled_by_default: bool = False


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


CANDIDATES = [
    SourceCandidate(
        "The Guardian",
        "en",
        "United Kingdom",
        "https://www.theguardian.com/international",
        "The Guardian World",
        "https://www.theguardian.com/world/rss",
        "world",
        10,
        "Worked earlier; reverify locally before enabling.",
    ),
    SourceCandidate(
        "The Guardian",
        "en",
        "United Kingdom",
        "https://www.theguardian.com/international",
        "The Guardian Technology",
        "https://www.theguardian.com/technology/rss",
        "technology",
        20,
        "Worked earlier; reverify locally before enabling.",
    ),
    SourceCandidate(
        "France 24",
        "en",
        "France",
        "https://www.france24.com/en/",
        "France 24 English",
        "https://www.france24.com/en/rss",
        "world",
        30,
        "Known working candidate; still probed before enabling.",
    ),
    SourceCandidate(
        "Euronews",
        "en",
        "France",
        "https://www.euronews.com/",
        "Euronews English World News",
        "https://www.euronews.com/rss?format=mrss&level=theme&name=news",
        "world",
        40,
        "Official Euronews MRSS widget URL; needs local verification.",
    ),
    SourceCandidate(
        "Euronews",
        "en",
        "France",
        "https://www.euronews.com/next",
        "Euronews English Next",
        "https://www.euronews.com/rss?format=mrss&level=vertical&name=next",
        "technology",
        50,
        "Official Euronews MRSS widget URL; needs local verification.",
    ),
    SourceCandidate(
        "The Christian Science Monitor",
        "en",
        "United States",
        "https://www.csmonitor.com/World",
        "Christian Science Monitor World",
        "https://www.csmonitor.com/rss/world",
        "world",
        60,
        "Official RSS page exists; URL needs local verification.",
    ),
    SourceCandidate(
        "CBC",
        "en",
        "Canada",
        "https://www.cbc.ca/news",
        "CBC Top Stories",
        "https://www.cbc.ca/webfeed/rss/rss-topstories",
        "top",
        70,
        "Official CBC webfeed; needs local verification.",
    ),
    SourceCandidate(
        "CBC",
        "en",
        "Canada",
        "https://www.cbc.ca/news/world",
        "CBC World",
        "https://www.cbc.ca/webfeed/rss/rss-world",
        "world",
        80,
        "Official CBC webfeed; needs local verification.",
    ),
    SourceCandidate(
        "CBC",
        "en",
        "Canada",
        "https://www.cbc.ca/news/canada",
        "CBC Canada",
        "https://www.cbc.ca/webfeed/rss/rss-canada",
        "canada",
        90,
        "Official CBC webfeed; needs local verification.",
    ),
    SourceCandidate(
        "NPR",
        "en",
        "United States",
        "https://www.npr.org/sections/world/",
        "NPR World",
        "https://feeds.npr.org/1004/rss.xml",
        "world",
        100,
        "Optional candidate using public NPR feed; needs verification.",
    ),
    SourceCandidate(
        "Al Jazeera English",
        "en",
        "Qatar",
        "https://www.aljazeera.com/",
        "Al Jazeera English",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "world",
        110,
        "Optional official-looking feed; needs local verification.",
    ),
    SourceCandidate(
        "France 24",
        "fr",
        "France",
        "https://www.france24.com/fr/",
        "France 24 Français",
        "https://www.france24.com/fr/rss",
        "world",
        120,
        "Known working candidate; still probed before enabling.",
    ),
    SourceCandidate(
        "Euronews",
        "fr",
        "France",
        "https://fr.euronews.com/",
        "Euronews Français Infos",
        "https://fr.euronews.com/rss?format=mrss&level=theme&name=news",
        "world",
        130,
        "Official Euronews MRSS widget URL; needs local verification.",
    ),
    SourceCandidate(
        "Le Monde",
        "fr",
        "France",
        "https://www.lemonde.fr",
        "Le Monde International",
        "https://www.lemonde.fr/international/rss_full.xml",
        "world",
        140,
        "Previously failed locally; keep disabled unless probe succeeds.",
    ),
    SourceCandidate(
        "Le Monde",
        "fr",
        "France",
        "https://www.lemonde.fr",
        "Le Monde Économie",
        "https://www.lemonde.fr/economie/rss_full.xml",
        "economy",
        150,
        "Previously failed locally; keep disabled unless probe succeeds.",
    ),
    SourceCandidate(
        "RFI",
        "fr",
        "France",
        "https://www.rfi.fr/fr/",
        "RFI Français",
        "https://www.rfi.fr/fr/rss",
        "world",
        160,
        "Previously failed locally; keep disabled unless probe succeeds.",
    ),
    SourceCandidate(
        "Radio-Canada",
        "fr",
        "Canada",
        "https://ici.radio-canada.ca/info",
        "Radio-Canada Nouvelles National",
        "https://ici.radio-canada.ca/rss/4159",
        "canada",
        170,
        "Optional French candidate; needs local verification.",
    ),
    SourceCandidate(
        "SWI swissinfo.ch",
        "fr",
        "Switzerland",
        "https://www.swissinfo.ch/fre/",
        "SWI swissinfo.ch Français",
        "https://www.swissinfo.ch/fre/rss",
        "world",
        180,
        "Optional French candidate; needs local verification.",
    ),
]


def probe_candidate(
    candidate: SourceCandidate, client: httpx.Client
) -> ProbeResult:
    start = perf_counter()
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
        candidate=candidate,
        status=status,
        http_status=http_status,
        elapsed_seconds=perf_counter() - start,
        entries_count=entries_count,
        error=error,
    )


def probe_candidates(
    candidates: list[SourceCandidate] | None = None,
) -> list[ProbeResult]:
    timeout = httpx.Timeout(
        TOTAL_TIMEOUT,
        connect=CONNECT_TIMEOUT,
        read=READ_TIMEOUT,
    )
    results = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for candidate in candidates or CANDIDATES:
            results.append(probe_candidate(candidate, client))
    return results


def seed_accessible_sources(session: Session) -> list[ProbeResult]:
    results = probe_candidates()
    for result in results:
        candidate = result.candidate
        source = session.scalar(
            select(NewsSource).where(NewsSource.name == candidate.source_name)
        )
        if source is None:
            source = NewsSource(
                name=candidate.source_name,
                language=candidate.language,
                country=candidate.country,
                homepage_url=candidate.homepage_url,
            )
            session.add(source)
            session.flush()
        feed = session.scalar(
            select(FeedSubscription).where(
                FeedSubscription.feed_url == candidate.feed_url
            )
        )
        if feed is None:
            feed = FeedSubscription(
                source_id=source.id,
                title=candidate.feed_title,
                feed_url=candidate.feed_url,
                category=candidate.category,
                language=candidate.language,
            )
            session.add(feed)
        feed.source_id = source.id
        feed.title = candidate.feed_title
        feed.category = candidate.category
        feed.language = candidate.language
        feed.is_enabled = result.is_success
        feed.last_fetch_status = result.status
        feed.last_http_status = result.http_status
        feed.last_entries_count = result.entries_count
        feed.last_new_articles_count = 0
        feed.last_skipped_articles_count = 0
        feed.last_fetch_error = result.error
    session.commit()
    return results
