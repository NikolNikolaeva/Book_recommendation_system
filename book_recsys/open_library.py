"""Клиент към Open Library (без API ключ) — търсене и опционално work metadata."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from book_recsys.data_io import cover_from_isbn, isbn_digits

USER_AGENT = "BookRecsysEdu/1.0 (+local/student-project)"
SEARCH_URL = "https://openlibrary.org/search.json"

SLEEP_BETWEEN_WORK_FETCH = 0.35


def _get_json(url: str, *, timeout: float = 28.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def search_books(query: str, *, limit: int = 40, offset: int = 0) -> list[dict[str, Any]]:
    """Връща списък от документи от search.json (до `limit`)."""
    lim = max(1, min(int(limit), 100))
    off = max(0, int(offset))
    q = urllib.parse.urlencode({"q": query, "limit": lim, "offset": off})
    try:
        data = _get_json(f"{SEARCH_URL}?{q}")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return []
    docs = data.get("docs")
    if not isinstance(docs, list):
        return []
    return [d for d in docs if isinstance(d, dict)]


def work_description(work_key: str | None) -> str:
    """Кратко описание от /works/OL....json — празно при грешка."""
    if not work_key or not work_key.startswith("/works/"):
        return ""
    path = work_key if work_key.endswith(".json") else f"{work_key}.json"
    url = f"https://openlibrary.org{path}"
    try:
        data = _get_json(url)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return ""
    desc = data.get("description")
    if isinstance(desc, str):
        return desc.strip()
    if isinstance(desc, dict):
        v = desc.get("value")
        if isinstance(v, str):
            return v.strip()
    return ""


def _title_from_doc(doc: dict[str, Any]) -> str | None:
    t = doc.get("title")
    if isinstance(t, str) and t.strip():
        return t.strip()
    subs = doc.get("subtitle")
    if isinstance(subs, str) and subs.strip():
        return subs.strip()
    return None


def _authors_from_doc(doc: dict[str, Any]) -> str:
    names = doc.get("author_name")
    if isinstance(names, list):
        parts = [str(n).strip() for n in names[:8] if n is not None and str(n).strip()]
        if parts:
            return ", ".join(parts)
    return "Unknown"


def _pick_isbn(doc: dict[str, Any]) -> str | None:
    raw = doc.get("isbn")
    if not isinstance(raw, list):
        return None
    for c in raw:
        d = isbn_digits(str(c))
        if d and len(d) in (10, 13):
            return d
    for c in raw:
        sd = isbn_digits(str(c))
        if sd:
            return sd
    return None


def _tags_from_doc(doc: dict[str, Any]) -> str:
    subjects = doc.get("subject")
    if not isinstance(subjects, list):
        return ""
    parts = []
    for s in subjects[:14]:
        if s is None:
            continue
        st = str(s).strip()
        if st and st not in parts:
            parts.append(st)
    return ", ".join(parts)


def _language_from_doc(doc: dict[str, Any]) -> str:
    langs = doc.get("language")
    if isinstance(langs, list) and langs:
        return str(langs[0])[:12]
    return "en"


def doc_to_book_fields(doc: dict[str, Any], *, extra_description: str = "") -> dict[str, Any] | None:
    title = _title_from_doc(doc)
    if not title:
        return None
    authors = _authors_from_doc(doc)
    tags = _tags_from_doc(doc)
    year = doc.get("first_publish_year")
    y: int | None = None
    if isinstance(year, int):
        y = year
    elif isinstance(year, str) and year.isdigit():
        y = int(year)
    isbn = _pick_isbn(doc)
    cover_i = doc.get("cover_i")
    cover_url: str | None = None
    if isinstance(cover_i, int):
        cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"
    if not cover_url:
        cover_url = cover_from_isbn(isbn)
    desc = (tags + " — " + title).strip(" —") if tags else title
    if extra_description:
        desc = (extra_description + "\n\n" + desc).strip()[:24000]
    return {
        "title": title,
        "authors": authors,
        "description": desc,
        "tags": tags,
        "language": _language_from_doc(doc),
        "year": y,
        "isbn": isbn,
        "cover_url": cover_url,
    }


def fetch_catalog_into_db(
    session,
    query: str,
    *,
    limit: int = 25,
    fetch_descriptions: bool = False,
    detail_budget: int = 18,
) -> tuple[int, int, int]:
    """Търси Open Library, upsert-ва в БД. Връща (inserted, updated, skipped)."""
    from book_recsys.data_io import upsert_book_record

    docs = search_books(query, limit=limit)
    inserted = updated = skipped = 0
    for i, doc in enumerate(docs):
        extra = ""
        if fetch_descriptions and i < detail_budget:
            wk = doc.get("key")
            if isinstance(wk, str):
                extra = work_description(wk)
                if extra:
                    time.sleep(SLEEP_BETWEEN_WORK_FETCH)
        row = doc_to_book_fields(doc, extra_description=extra)
        if row is None:
            skipped += 1
            continue
        res = upsert_book_record(session, **row)
        if res == "inserted":
            inserted += 1
        elif res == "updated":
            updated += 1
        else:
            skipped += 1
    session.commit()
    return inserted, updated, skipped
