"""Разширяване на каталога през Open Library (без ключ, нужен интернет).

Примери:

  python -m book_recsys.fetch_catalog --limit 30 "science fiction"
  python -m book_recsys.fetch_catalog --limit 40 --descriptions 'subject:\"Detective and mystery stories\"'
"""

from __future__ import annotations

import argparse

from book_recsys.database import SessionLocal, init_db
from book_recsys.data_io import count_books
from book_recsys.open_library import fetch_catalog_into_db


def main() -> None:
    p = argparse.ArgumentParser(description="Импорт книги от Open Library в локалната БД.")
    p.add_argument("query", help="Търсене (както в openlibrary.org/search)")
    p.add_argument("--limit", type=int, default=30, help="Резултати (1–80)")
    p.add_argument(
        "--descriptions",
        action="store_true",
        help="Зарежда по-големи описания от works.json (по-бавно, щади API).",
    )
    args = p.parse_args()
    lim = min(80, max(1, int(args.limit)))
    init_db()
    with SessionLocal() as session:
        ins, upd, sk = fetch_catalog_into_db(
            session,
            args.query.strip(),
            limit=lim,
            fetch_descriptions=bool(args.descriptions),
        )
        total = count_books(session)
    print(f"inserted={ins} updated={upd} skipped={sk} total_books={total}")


if __name__ == "__main__":
    main()
