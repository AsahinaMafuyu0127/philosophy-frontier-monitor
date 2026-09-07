"""Loss-minimizing normalization helpers."""

from __future__ import annotations

import re
import unicodedata

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


def normalize_doi(value: str | None) -> str | None:
    """Normalize a DOI string without claiming that the DOI resolves."""

    if value is None:
        return None
    normalized = value.strip()
    lowered = normalized.casefold()
    for prefix in DOI_PREFIXES:
        if lowered.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    normalized = normalized.casefold().rstrip(".,;)")
    return normalized if DOI_PATTERN.fullmatch(normalized) else None


def normalize_title(value: str) -> str:
    """Create a comparison-only title while preserving the original elsewhere."""

    value = unicodedata.normalize("NFKC", value).casefold()
    # PhilPapers display titles commonly use Markdown-like underscores or
    # asterisks for book titles. They are typography, not bibliographic text.
    value = value.replace("&", " and ").replace("_", " ").replace("*", " ")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())
