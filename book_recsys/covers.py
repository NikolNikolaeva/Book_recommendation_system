"""Resolve book cover bytes with Open Library fallbacks and in-memory cache."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path

from book_recsys.data_io import cover_from_isbn
from book_recsys.orm import Book

BASE_DIR = Path(__file__).resolve().parent.parent
PLACEHOLDER_PATH = BASE_DIR / "static" / "placeholder-cover.svg"
_USER_AGENT = "BookRecsys/1.0"
_CACHE: dict[int, tuple[bytes, str]] = {}
_CACHE_MAX = 512

_OL_SIZE_RE = re.compile(r"-([LSM])\.jpg$", re.IGNORECASE)


def _placeholder() -> tuple[bytes, str]:
    return PLACEHOLDER_PATH.read_bytes(), "image/svg+xml"


def _candidate_urls(book: Book) -> list[str]:
    base: str | None = None
    if book.cover_url:
        base = book.cover_url.strip()
    elif book.isbn:
        base = cover_from_isbn(book.isbn)
    if not base:
        return []

    ordered: list[str] = []
    seen: set[str] = set()
    for size in ("M", "S", "L"):
        sized = _OL_SIZE_RE.sub(f"-{size}.jpg", base)
        if sized not in seen:
            seen.add(sized)
            ordered.append(sized)
    if base not in seen:
        ordered.append(base)
    return ordered[:4]


def _fetch_url(url: str, *, timeout: float = 5.0) -> tuple[bytes, str] | None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if len(data) < 256:
                return None
            ct = resp.headers.get("Content-Type", "image/jpeg")
            return data, ct.split(";")[0].strip() or "image/jpeg"
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def resolve_cover_bytes(book: Book) -> tuple[bytes, str]:
    cached = _CACHE.get(book.id)
    if cached is not None:
        return cached

    for url in _candidate_urls(book):
        fetched = _fetch_url(url)
        if fetched is not None:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[book.id] = fetched
            return fetched

    placeholder = _placeholder()
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[book.id] = placeholder
    return placeholder
