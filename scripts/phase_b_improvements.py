"""Фаза B: разширяване на каталога, обогатяване на метаданни, приятелства."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from book_recsys.database import SessionLocal, init_db
from book_recsys.data_io import _merge_tag_fields
from book_recsys.open_library import fetch_catalog_into_db
from book_recsys.orm import Book, Friendship, User

OL_QUERIES = [
    ("historical fiction", 20),
    ("science fiction", 20),
    ("mystery detective", 15),
]

ENRICHMENT_RULES: list[tuple[str, str]] = [
    ("фентъзи", "епично,магия,приключение"),
    ("sci-fi", "научна фантастика,бъдеще,космос"),
    ("трилър", "напрежение,загадка,криминале"),
    ("романс", "любов,емоции,отношения"),
    ("класика", "класическа литература,вечни теми"),
    ("история", "исторически,период,епоха"),
    ("дистопия", "антиутопия,общество,контрол"),
    ("young adult", "младежка литература,идентичност"),
    ("българска литература", "български автор,национална класика"),
    ("нехудожествена", "факти,анализ,знание"),
]


def enrich_book_tags(session) -> int:
    updated = 0
    books = list(session.scalars(select(Book)))
    for book in books:
        blob = f"{book.title} {book.tags} {book.description}".lower()
        extra: list[str] = []
        for keyword, tags in ENRICHMENT_RULES:
            if keyword in blob:
                extra.append(tags)
        if not extra:
            continue
        merged = _merge_tag_fields(book.tags, ", ".join(extra))
        if merged != (book.tags or ""):
            book.tags = merged
            if len((book.description or "").strip()) < 40 and book.tags:
                book.description = (
                    (book.description or "").strip()
                    + f" Жанрови насоки: {book.tags[:200]}."
                ).strip()
            updated += 1
    session.commit()
    return updated


def expand_friendships(session) -> int:
    users = list(session.scalars(select(User).order_by(User.id)))
    if len(users) < 3:
        return 0
    added = 0
    pairs = [
        (0, 3),
        (1, 4),
        (2, 5),
        (3, 6),
        (4, 7),
        (0, 5),
        (1, 6),
        (2, 7),
    ]
    for a, b in pairs:
        if a >= len(users) or b >= len(users):
            continue
        ua, ub = users[a], users[b]
        for uid, fid in ((ua.id, ub.id), (ub.id, ua.id)):
            exists = session.scalar(
                select(Friendship).where(
                    Friendship.user_id == uid,
                    Friendship.friend_id == fid,
                )
            )
            if not exists:
                session.add(Friendship(user_id=uid, friend_id=fid))
                added += 1
    session.commit()
    return added


def fetch_open_library_batches(session) -> dict:
    totals = {"inserted": 0, "updated": 0, "skipped": 0}
    for query, limit in OL_QUERIES:
        ins, upd, sk = fetch_catalog_into_db(session, query, limit=limit, fetch_descriptions=False)
        totals["inserted"] += ins
        totals["updated"] += upd
        totals["skipped"] += sk
    return totals


def run_phase_b(*, skip_ol: bool = False) -> dict:
    init_db()
    with SessionLocal() as session:
        ol = {"inserted": 0, "updated": 0, "skipped": 0}
        if not skip_ol:
            try:
                ol = fetch_open_library_batches(session)
            except Exception as exc:  # noqa: BLE001
                ol = {"error": str(exc), "inserted": 0, "updated": 0, "skipped": 0}
        tags_updated = enrich_book_tags(session)
        friends_added = expand_friendships(session)
    return {
        "open_library": ol,
        "tags_enriched": tags_updated,
        "friendships_added": friends_added,
    }


def main() -> None:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--skip-ol", action="store_true", help="Пропусни Open Library (offline)")
    args = p.parse_args()
    result = run_phase_b(skip_ol=args.skip_ol)
    print(result)


if __name__ == "__main__":
    main()
