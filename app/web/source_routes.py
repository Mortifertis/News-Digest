import logging
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db.models import FeedSubscription
from app.db.session import get_session
from app.services.rss_fetcher import (
    fetch_enabled_feeds,
    fetch_feed,
    test_feed_by_id,
)
from app.services.source_filters import (
    FILTER_KEYS,
    get_filtered_feeds,
    normalize_filters,
    source_filter_options,
    source_summary,
)

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
templates = Jinja2Templates(directory="app/web/templates")
logger = logging.getLogger(__name__)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _filter_values(request: Request) -> dict[str, str]:
    return normalize_filters(dict(request.query_params))


def _query_suffix(filters: dict[str, str]) -> str:
    pairs = [f"{key}={value}" for key, value in filters.items() if value]
    return "&".join(pairs)


def _sources_context(
    session: Session,
    filters: dict[str, str],
    message: str | None = None,
) -> dict:
    feeds = get_filtered_feeds(session, filters)
    return {
        "feeds": feeds,
        "message": message,
        "filters": filters,
        "filter_options": source_filter_options(session),
        "summary": source_summary(feeds),
    }


def _source_redirect(
    filters: dict[str, str], message: str
) -> RedirectResponse:
    suffix = _query_suffix(filters)
    separator = "&" if suffix else ""
    return RedirectResponse(
        f"/sources?{suffix}{separator}message={message}", status_code=303
    )


def _row_response(
    request: Request,
    session: Session,
    feed: FeedSubscription,
    message: str,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        "partials/source_row.html",
        {"feed": feed, "message": message},
    )
    response.headers["HX-Trigger"] = "sourcesChanged"
    return response


async def _request_filters(request: Request) -> dict[str, str]:
    values = dict(request.query_params)
    if request.method == "POST":
        body = (await request.body()).decode()
        form = parse_qs(body, keep_blank_values=True)
        values.update(
            {key: form[key][0] for key in FILTER_KEYS if key in form}
        )
    return normalize_filters(values)


@router.get("/sources")
def sources(
    request: Request,
    session: SessionDep,
    message: str | None = None,
    enabled: str | None = None,
    url_status: str | None = None,
    language: str | None = None,
    country: str | None = None,
    category: str | None = None,
    outlet_type: str | None = None,
    reliability_score: str | None = None,
    bias_profile: str | None = None,
    last_fetch_status: str | None = None,
):
    filters = normalize_filters(
        {
            "enabled": enabled,
            "url_status": url_status,
            "language": language,
            "country": country,
            "category": category,
            "outlet_type": outlet_type,
            "reliability_score": reliability_score,
            "bias_profile": bias_profile,
            "last_fetch_status": last_fetch_status,
        }
    )
    return templates.TemplateResponse(
        request, "sources.html", _sources_context(session, filters, message)
    )


@router.get("/sources/table")
def sources_table(request: Request, session: SessionDep):
    filters = _filter_values(request)
    return templates.TemplateResponse(
        request,
        "partials/source_table.html",
        _sources_context(session, filters),
    )


@router.get("/sources/summary")
def sources_summary(request: Request, session: SessionDep):
    filters = _filter_values(request)
    return templates.TemplateResponse(
        request,
        "partials/source_summary.html",
        _sources_context(session, filters),
    )


@router.post("/sources/{feed_id}/url")
async def save_source_url(
    request: Request,
    feed_id: int,
    session: SessionDep,
):
    filters = await _request_filters(request)
    allowed = {
        "verified_official",
        "candidate_pattern",
        "needs_verification",
        "needs_url_verification",
        "api_or_licensed_only",
        "unavailable",
    }
    form = parse_qs((await request.body()).decode(), keep_blank_values=True)
    feed_url = form.get("feed_url", [""])[0]
    rss_url_status = form.get("rss_url_status", ["candidate_pattern"])[0]
    feed = session.get(FeedSubscription, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    url = feed_url.strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        message = "Invalid feed URL"
        if _is_htmx(request):
            return _row_response(request, session, feed, message)
        return _source_redirect(filters, message)
    if rss_url_status == "needs_url_verification":
        rss_url_status = "needs_verification"
    if rss_url_status not in allowed:
        rss_url_status = "candidate_pattern" if url else "needs_verification"
    feed.feed_url = url or None
    feed.rss_url_status = rss_url_status if url else "needs_verification"
    feed.fetchable = bool(feed.feed_url) and feed.rss_url_status not in {
        "api_or_licensed_only",
        "unavailable",
    }
    if not feed.feed_url:
        feed.is_enabled = False
    session.commit()
    session.refresh(feed)
    if _is_htmx(request):
        return _row_response(request, session, feed, "Feed URL saved")
    return _source_redirect(filters, "Feed URL saved")


@router.post("/sources/{feed_id}/enable")
async def enable_source(request: Request, feed_id: int, session: SessionDep):
    filters = await _request_filters(request)
    try:
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="Feed not found")
        message = "Feed enabled"
        if not feed.feed_url:
            message = "Cannot enable feed without RSS URL"
        else:
            feed.is_enabled = True
            session.commit()
            session.refresh(feed)
        if _is_htmx(request):
            return _row_response(request, session, feed, message)
        return _source_redirect(filters, message)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Enable feed failed")
        return _source_redirect(filters, "Enable feed failed")


@router.post("/sources/{feed_id}/disable")
async def disable_source(request: Request, feed_id: int, session: SessionDep):
    filters = await _request_filters(request)
    try:
        feed = session.get(FeedSubscription, feed_id)
        if feed is None:
            raise HTTPException(status_code=404, detail="Feed not found")
        feed.is_enabled = False
        session.commit()
        session.refresh(feed)
        if _is_htmx(request):
            return _row_response(request, session, feed, "Feed disabled")
        return _source_redirect(filters, "Feed disabled")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Disable feed failed")
        return _source_redirect(filters, "Disable feed failed")


@router.post("/sources/{feed_id}/test")
async def test_source(request: Request, feed_id: int, session: SessionDep):
    filters = await _request_filters(request)
    try:
        result = test_feed_by_id(session, feed_id)
        feed = session.get(FeedSubscription, feed_id)
        if result.status == "success":
            message = f"Feed test succeeded: {result.entries_count} entries"
        else:
            message = f"Feed test failed: {result.error or result.status}"
        if _is_htmx(request):
            return _row_response(request, session, feed, message)
        return _source_redirect(filters, message)
    except Exception:
        logger.exception("Test feed failed")
        return _source_redirect(filters, "Test feed failed")


_BULK_SKIP_STATUSES = {"api_or_licensed_only", "unavailable"}


def _bulk_feeds(session: Session, filters: dict[str, str]) -> list:
    return get_filtered_feeds(session, filters)


@router.post("/sources/bulk-enable")
async def bulk_enable_sources(request: Request, session: SessionDep):
    filters = await _request_filters(request)
    feeds = _bulk_feeds(session, filters)
    enabled_count = 0
    skipped_count = 0
    for feed_item in feeds:
        if (
            not feed_item.feed_url
            or not feed_item.fetchable
            or feed_item.rss_url_status in _BULK_SKIP_STATUSES
        ):
            skipped_count += 1
            continue
        if not feed_item.is_enabled:
            enabled_count += 1
        feed_item.is_enabled = True
    session.commit()
    message = (
        f"Enabled {enabled_count} visible fetchable feeds; "
        f"skipped {skipped_count}."
    )
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "partials/source_workspace.html",
            _sources_context(session, filters, message),
        )
    return _source_redirect(filters, message)


@router.post("/sources/bulk-disable")
async def bulk_disable_sources(request: Request, session: SessionDep):
    filters = await _request_filters(request)
    feeds = _bulk_feeds(session, filters)
    disabled_count = 0
    for feed_item in feeds:
        if feed_item.is_enabled:
            disabled_count += 1
        feed_item.is_enabled = False
    session.commit()
    message = f"Disabled {disabled_count} visible feeds."
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "partials/source_workspace.html",
            _sources_context(session, filters, message),
        )
    return _source_redirect(filters, message)


@router.post("/sources/bulk-test")
async def bulk_test_sources(request: Request, session: SessionDep):
    filters = await _request_filters(request)
    feeds = [
        feed_item
        for feed_item in _bulk_feeds(session, filters)
        if feed_item.feed_url and feed_item.fetchable
    ]
    skipped_count = len(_bulk_feeds(session, filters)) - len(feeds)
    success_count = 0
    failed_count = 0
    from app.web import routes as main_routes

    with main_routes.create_http_client(
        timeout=20.0, follow_redirects=True
    ) as client:
        for feed_item in feeds:
            result = fetch_feed(session, client, feed_item)
            if result.status == "success":
                success_count += 1
            else:
                failed_count += 1
    message = (
        "Bulk test may take some time. "
        f"Tested {len(feeds)} visible fetchable feeds: "
        f"{success_count} succeeded, {failed_count} failed, "
        f"{skipped_count} skipped."
    )
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "partials/source_workspace.html",
            _sources_context(session, filters, message),
        )
    return _source_redirect(filters, message)




@router.post("/fetch/run")
async def fetch_run(request: Request, session: SessionDep):
    filters = await _request_filters(request)
    try:
        run = fetch_enabled_feeds(session, mode="manual")
        message = (
            f'Fetch run created: <a href="/fetch-runs/{run.id}">view run</a>.'
        )
        if _is_htmx(request):
            return templates.TemplateResponse(
                request,
                "partials/source_workspace.html",
                _sources_context(session, filters, message),
            )
        return RedirectResponse(f"/fetch-runs/{run.id}", status_code=303)
    except Exception:
        logger.exception("Fetch run failed")
        if _is_htmx(request):
            return templates.TemplateResponse(
                request,
                "partials/source_flash.html",
                {"message": "Fetch run failed"},
            )
        return _source_redirect(filters, "Fetch run failed")


