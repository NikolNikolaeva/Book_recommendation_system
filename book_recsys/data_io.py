"""CSV импорт на книги и помощни URL-и за корици (Open Library)."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from book_recsys.orm import Book, Interaction

OPENLIB_COVER = "https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"


def isbn_digits(isbn: str | None) -> str | None:
    if not isbn:
        return None
    d = re.sub(r"[^0-9Xx]", "", isbn)
    return d or None


def cover_from_isbn(isbn: str | None) -> str | None:
    d = isbn_digits(isbn)
    if not d:
        return None
    return OPENLIB_COVER.format(isbn=d)


def import_books_csv(
    session: Session,
    csv_path: Path,
    *,
    replace_catalog: bool = False,
) -> int:
    """
    Чете CSV с колони: title,authors,description,tags,language,year,isbn,cover_url
    cover_url е опционален; ако липсва, опитва се от isbn през Open Library.
    При replace_catalog изтрива всички книги и взаимодействия (внимание!).
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"Липсва CSV: {csv_path}")

    if replace_catalog:
        session.execute(delete(Interaction))
        session.execute(delete(Book))
        session.commit()

    n = 0
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if not row or not any((v or "").strip() for v in row.values()):
                continue
            row = {
                (k or "").strip().lower(): (v or "").strip()
                for k, v in row.items()
                if k is not None
            }
            title = row.get("title", "")
            authors = row.get("authors", "")
            if not title or not authors:
                continue
            desc = row.get("description", "")
            tags = row.get("tags", "")
            lang = row.get("language", "en") or "en"
            year_s = row.get("year", "")
            year = int(year_s) if year_s.isdigit() else None
            isbn = row.get("isbn", "") or None
            cover = row.get("cover_url", "") or None
            if not cover:
                cover = cover_from_isbn(isbn)
            action = upsert_book_record(
                session,
                title=title,
                authors=authors,
                description=desc,
                tags=tags,
                language=lang,
                year=year,
                isbn=isbn,
                cover_url=cover,
            )
            if action != "skipped":
                n += 1
    session.commit()
    return n


def count_books(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Book)) or 0)


def upsert_book_record(
    session: Session,
    *,
    title: str,
    authors: str,
    description: str = "",
    tags: str = "",
    language: str = "en",
    year: int | None = None,
    isbn: str | None = None,
    cover_url: str | None = None,
) -> Literal["inserted", "updated", "skipped"]:
    """Добавя или освежава книга (ISBN или заглавие+автор). Не прави commit."""
    t = (title or "").strip()
    a = (authors or "").strip()
    if not t or not a:
        return "skipped"
    isbn_key = isbn_digits(isbn)
    existing: Book | None = None
    if isbn_key:
        existing = session.scalar(select(Book).where(Book.isbn == isbn_key))
    if existing is None:
        existing = session.scalar(
            select(Book).where(
                func.lower(Book.title) == t.lower(),
                func.lower(Book.authors) == a.lower(),
            )
        )
    if existing is None:
        session.add(
            Book(
                title=t[:512],
                authors=a[:512],
                description=(description or "").strip(),
                tags=_merge_tag_fields("", tags),
                language=(language or "en").strip()[:32],
                year=year,
                isbn=isbn_key[:32] if isbn_key else None,
                cover_url=(cover_url[:1024] if cover_url else None) or cover_from_isbn(isbn_key),
            )
        )
        return "inserted"

    changed = False
    if isbn_key and not existing.isbn:
        existing.isbn = isbn_key[:32]
        changed = True
    nd = (description or "").strip()
    if nd and len(nd) > len((existing.description or "").strip()):
        existing.description = nd[:65535]
        changed = True
    merged = _merge_tag_fields(existing.tags, tags)
    if merged != (existing.tags or ""):
        existing.tags = merged
        changed = True
    if year and existing.year is None:
        existing.year = year
        changed = True
    lang = (language or "en").strip()[:32]
    if lang and lang != "en" and (existing.language or "") == "en":
        existing.language = lang
        changed = True
    cu = cover_url[:1024] if cover_url else None
    if cu and (not existing.cover_url or len(cu) > len(existing.cover_url)):
        existing.cover_url = cu
        changed = True
    if not existing.cover_url:
        cf = cover_from_isbn(existing.isbn or isbn_key)
        if cf:
            existing.cover_url = cf[:1024]
            changed = True
    return "updated" if changed else "skipped"


def _merge_tag_fields(old: str, new: str) -> str:
    s = [x.strip() for x in (old or "").split(",") if x.strip()]
    seen = set(x.lower() for x in s)
    for part in (new or "").split(","):
        p = part.strip()
        if not p or p.lower() in seen:
            continue
        s.append(p)
        seen.add(p.lower())
        if len(s) >= 32:
            break
    return ", ".join(s)