"""CLI: импорт на книги от CSV. Употреба: python -m book_recsys.import_csv [--replace]"""

from __future__ import annotations

import sys

from book_recsys.config import BASE_DIR
from book_recsys.data_io import count_books, import_books_csv
from book_recsys.database import SessionLocal, init_db

BOOKS_CSV = BASE_DIR / "data" / "books.csv"


def main() -> None:
    replace = "--replace" in sys.argv
    init_db()
    with SessionLocal() as session:
        if count_books(session) > 0 and not replace:
            print(
                "Има вече книги в БД — новите редове се сливат по ISBN или заглавие+автор (без дубли). "
                "За пълно изчистване на каталога и interactions пусни с --replace."
            )
        n = import_books_csv(session, BOOKS_CSV, replace_catalog=replace)
    print(f"Нови или обновени книги от CSV: {n} (replace_catalog={replace})")


if __name__ == "__main__":
    main()
