from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import FeedSubscription, NewsSource

FILTER_KEYS = (
    "enabled",
    "url_status",
    "language",
    "country",
    "category",
    "outlet_type",
    "reliability_score",
    "bias_profile",
    "last_fetch_status",
)


@dataclass(frozen=True)
class SourceSummary:
    total: int
    enabled: int
    disabled: int
    fetchable: int
    needs_url_verification: int
    success: int
    failed: int
    never: int


def clean_filter(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def normalize_filters(values: dict[str, str | None]) -> dict[str, str]:
    return {key: clean_filter(values.get(key)) or "" for key in FILTER_KEYS}


def parse_enabled_filter(value: str | None) -> bool | None:
    cleaned = clean_filter(value)
    if cleaned in {None, "all"}:
        return None
    if cleaned == "enabled":
        return True
    if cleaned == "disabled":
        return False
    return None


def parse_reliability_filter(value: str | None) -> int | None:
    cleaned = clean_filter(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _url_status(value: str | None) -> str | None:
    normalized = clean_filter(value)
    if normalized in {None, "all"}:
        return None
    if normalized == "needs_url_verification":
        return "needs_verification"
    return normalized


def filtered_feeds_statement(filters: dict[str, str | None]):
    stmt = select(FeedSubscription).options(
        joinedload(FeedSubscription.source)
    )
    enabled_value = parse_enabled_filter(filters.get("enabled"))
    if enabled_value is not None:
        stmt = stmt.where(FeedSubscription.is_enabled.is_(enabled_value))
    normalized_url_status = _url_status(filters.get("url_status"))
    allowed_url_statuses = {
        "verified_official",
        "candidate_pattern",
        "needs_verification",
        "api_or_licensed_only",
        "unavailable",
    }
    if normalized_url_status == "has_url":
        stmt = stmt.where(FeedSubscription.feed_url.is_not(None))
        stmt = stmt.where(FeedSubscription.feed_url != "")
    elif normalized_url_status in allowed_url_statuses:
        stmt = stmt.where(
            FeedSubscription.rss_url_status == normalized_url_status
        )
    source_joined = False
    if clean_filter(filters.get("language")):
        stmt = stmt.where(FeedSubscription.language == filters["language"])
    if clean_filter(filters.get("country")):
        stmt = stmt.join(FeedSubscription.source)
        source_joined = True
        stmt = stmt.where(NewsSource.country == filters["country"])
    if clean_filter(filters.get("category")):
        stmt = stmt.where(FeedSubscription.category == filters["category"])
    if clean_filter(filters.get("outlet_type")):
        if not source_joined:
            stmt = stmt.join(FeedSubscription.source)
            source_joined = True
        stmt = stmt.where(NewsSource.outlet_type == filters["outlet_type"])
    reliability_value = parse_reliability_filter(
        filters.get("reliability_score")
    )
    if reliability_value is not None:
        if not source_joined:
            stmt = stmt.join(FeedSubscription.source)
            source_joined = True
        stmt = stmt.where(
            NewsSource.editorial_reliability_score == reliability_value
        )
    if clean_filter(filters.get("bias_profile")):
        if not source_joined:
            stmt = stmt.join(FeedSubscription.source)
        stmt = stmt.where(NewsSource.bias_profile == filters["bias_profile"])
    if clean_filter(filters.get("last_fetch_status")):
        stmt = stmt.where(
            FeedSubscription.last_fetch_status == filters["last_fetch_status"]
        )
    return stmt.order_by(FeedSubscription.title)


def get_filtered_feeds(
    session: Session, filters: dict[str, str | None]
) -> list[FeedSubscription]:
    return session.scalars(filtered_feeds_statement(filters)).unique().all()


def source_summary(feeds: list[FeedSubscription]) -> SourceSummary:
    return SourceSummary(
        total=len(feeds),
        enabled=sum(1 for feed in feeds if feed.is_enabled),
        disabled=sum(1 for feed in feeds if not feed.is_enabled),
        fetchable=sum(1 for feed in feeds if feed.fetchable),
        needs_url_verification=sum(
            1 for feed in feeds if feed.rss_url_status == "needs_verification"
        ),
        success=sum(
            1 for feed in feeds if feed.last_fetch_status == "success"
        ),
        failed=sum(1 for feed in feeds if feed.last_fetch_status == "failed"),
        never=sum(1 for feed in feeds if feed.last_fetch_status == "never"),
    )


def source_filter_options(session: Session) -> dict[str, list]:
    return {
        "languages": session.scalars(
            select(FeedSubscription.language)
            .distinct()
            .order_by(FeedSubscription.language)
        ).all(),
        "countries": session.scalars(
            select(NewsSource.country).distinct().order_by(NewsSource.country)
        ).all(),
        "categories": session.scalars(
            select(FeedSubscription.category)
            .distinct()
            .order_by(FeedSubscription.category)
        ).all(),
        "outlet_types": session.scalars(
            select(NewsSource.outlet_type)
            .where(NewsSource.outlet_type.is_not(None))
            .distinct()
            .order_by(NewsSource.outlet_type)
        ).all(),
        "bias_profiles": session.scalars(
            select(NewsSource.bias_profile)
            .where(NewsSource.bias_profile.is_not(None))
            .distinct()
            .order_by(NewsSource.bias_profile)
        ).all(),
    }
