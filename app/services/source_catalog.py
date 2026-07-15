from __future__ import annotations

import json
from importlib import resources
from typing import Any

from app.services.source_candidate_schema import SourceCandidate

CATALOG_RESOURCE = "source_catalog.json"
REQUIRED_CANDIDATE_FIELDS = {
    "source_name",
    "language",
    "country",
    "homepage_url",
    "feed_title",
    "feed_url",
    "category",
}


def _load_catalog_rows() -> list[dict[str, Any]]:
    catalog_path = resources.files(__package__).joinpath(CATALOG_RESOURCE)
    with catalog_path.open(encoding="utf-8") as catalog_file:
        rows = json.load(catalog_file)
    if not isinstance(rows, list):
        raise ValueError("Source catalog must contain a list of candidates.")
    return rows


def _validate_catalog_row(row: dict[str, Any], index: int) -> None:
    missing = REQUIRED_CANDIDATE_FIELDS - set(row)
    if missing:
        fields = ", ".join(sorted(missing))
        raise ValueError(f"Source catalog row {index} misses: {fields}")


def build_candidate_catalog() -> list[SourceCandidate]:
    candidates = []
    for index, row in enumerate(_load_catalog_rows(), start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Source catalog row {index} must be an object.")
        _validate_catalog_row(row, index)
        candidates.append(SourceCandidate(**row))
    return candidates
