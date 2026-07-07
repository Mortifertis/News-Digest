from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.db.models import AppSetting


@dataclass(frozen=True)
class SettingSpec:
    key: str
    section: str
    label: str
    default: str
    allowed: tuple[str, ...] | None = None
    min_value: int | None = None
    max_value: int | None = None


SETTING_SPECS = (
    SettingSpec(
        "fetch_interval_minutes", "Fetch", "Fetch interval", "0",
        ("0", "60", "180", "360", "720", "1440"),
    ),
    SettingSpec(
        "max_entries_per_feed", "Fetch", "Max entries per feed", "50",
        ("10", "20", "50", "100"),
    ),
    SettingSpec(
        "request_timeout_seconds", "Fetch", "Request timeout", "30",
        ("10", "20", "30", "60"),
    ),
    SettingSpec(
        "auto_cluster_after_fetch", "Fetch", "Auto-cluster after fetch",
        "true", ("true", "false"),
    ),
    SettingSpec(
        "fuzzy_threshold_default", "Clustering", "Default fuzzy threshold",
        "82", min_value=60, max_value=95,
    ),
    SettingSpec(
        "fuzzy_threshold_en", "Clustering", "English fuzzy threshold", "82",
        min_value=60, max_value=95,
    ),
    SettingSpec(
        "fuzzy_threshold_fr", "Clustering", "French fuzzy threshold", "82",
        min_value=60, max_value=95,
    ),
    SettingSpec(
        "fuzzy_candidate_window_hours", "Clustering", "Candidate window", "72",
        ("24", "48", "72", "168"),
    ),
    SettingSpec(
        "min_text_length_for_fuzzy", "Clustering", "Minimum fuzzy text length",
        "100", ("50", "100", "150", "200"),
    ),
    SettingSpec(
        "items_per_page", "Display", "Items per page", "50",
        ("20", "50", "100"),
    ),
    SettingSpec(
        "default_language_filter", "Display", "Default language", "",
        ("", "en", "fr"),
    ),
    SettingSpec(
        "default_feed_sort", "Display", "Default feed sort", "newest",
        ("newest", "oldest", "largest_cluster", "source"),
    ),
    SettingSpec(
        "article_retention_days", "Data", "Article retention", "0",
        ("0", "7", "30", "90", "180", "365"),
    ),
)

SPECS_BY_KEY = {spec.key: spec for spec in SETTING_SPECS}


def get_setting(session: Session, key: str, default: str) -> str:
    setting = session.get(AppSetting, key)
    return setting.value if setting is not None else default


def get_int_setting(session: Session, key: str, default: int) -> int:
    try:
        return int(get_setting(session, key, str(default)))
    except ValueError:
        return default


def get_bool_setting(session: Session, key: str, default: bool) -> bool:
    value = get_setting(session, key, str(default).lower()).lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    return default


def set_setting(session: Session, key: str, value: str) -> None:
    setting = session.get(AppSetting, key)
    if setting is None:
        session.add(AppSetting(key=key, value=value))
    else:
        setting.value = value


def get_all_settings_with_defaults(session: Session) -> dict[str, str]:
    return {
        spec.key: get_setting(session, spec.key, spec.default)
        for spec in SETTING_SPECS
    }


def validate_settings_payload(
    form: FormData | dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    errors: list[str] = []
    for spec in SETTING_SPECS:
        raw_value = str(form.get(spec.key, spec.default)).strip()
        if spec.allowed is not None:
            if raw_value in spec.allowed:
                values[spec.key] = raw_value
            else:
                errors.append(f"Invalid value for {spec.key}")
            continue
        try:
            int_value = int(raw_value)
        except ValueError:
            errors.append(f"Invalid value for {spec.key}")
            continue
        if spec.min_value <= int_value <= spec.max_value:
            values[spec.key] = str(int_value)
        else:
            errors.append(f"Invalid value for {spec.key}")
    return values, errors
