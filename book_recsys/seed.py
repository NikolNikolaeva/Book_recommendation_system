"""Сийд: CSV книги, демо потребители с парола demo123, синтетични взаимодействия."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from book_recsys.auth_pass import hash_password
from book_recsys.config import BASE_DIR
from book_recsys.data_io import count_books, import_books_csv
from book_recsys.database import SessionLocal, init_db
from book_recsys.orm import Book, Friendship, Interaction, User

BOOKS_CSV = BASE_DIR / "data" / "books.csv"
DEMO_PASSWORD = "demo123"


def _books_fallback(session) -> None:
    """Минимален каталог ако липсва data/books.csv."""
    cover = "https://placehold.co/240x360/1a1a24/f5e6d3?text=Book"
    session.add_all(
        [
            Book(
                title="Примерна книга A",
                authors="Автор А",
                description="Демо.",
                tags="класика",
                language="bg",
                year=2000,
                cover_url=cover,
            ),
            Book(
                title="Примерна книга B",
                authors="Автор Б",
                description="Демо.",
                tags="sci-fi",
                language="en",
                year=2010,
                cover_url=cover,
            ),
        ]
    )
    session.commit()


def patch_demo_passwords(session) -> None:
    """Задава парола на стари демо акаунти без hash."""
    for uname in (
        "nikol",
        "gabriela",
        "alex_reader",
        "mira",
        "stefan",
        "elena",
        "dimitar",
        "hana",
    ):
        u = session.scalar(select(User).where(User.username == uname))
        if u is None:
            continue
        if not u.password_hash:
            u.password_hash = hash_password(DEMO_PASSWORD)
        u.onboarding_completed = True


def ensure_seed(force: bool = False) -> None:
    init_db()
    with SessionLocal() as session:
        patch_demo_passwords(session)
        session.commit()

        if not force and count_books(session) > 0:
            return

        if force:
            session.execute(delete(Interaction))
            session.execute(delete(Friendship))
            session.execute(delete(User))
            session.execute(delete(Book))
            session.commit()

        if BOOKS_CSV.is_file():
            import_books_csv(session, BOOKS_CSV, replace_catalog=False)
        else:
            _books_fallback(session)

        books = list(session.scalars(select(Book)))
        if not books:
            return

        users_meta = [
            ("nikol", "фентъзи,sci-fi,българска литература", "both", "Роулинг,Толкин"),
            ("gabriela", "романс,трилър,класика", "both", "Оруел,Маас"),
            ("alex_reader", "нехудожествена,психология", "en", "Kahneman,Clear"),
            ("mira", "фентъзи,young adult", "bg", "Роулинг,Маас"),
            ("stefan", "история,класика", "bg", "Вазов,Толстой"),
            ("elena", "sci-fi,дистопия", "en", "Orwell,Herbert"),
            ("dimitar", "трилър,криминале", "bg", "Christie,Larsson"),
            ("hana", "романс,drama", "en", "Austen,Reid"),
        ]
        for uname, genres, lang, authors in users_meta:
            session.add(
                User(
                    username=uname,
                    password_hash=hash_password(DEMO_PASSWORD),
                    onboarding_genres=genres,
                    onboarding_authors=authors,
                    onboarding_language=lang,
                    onboarding_completed=True,
                )
            )
        session.commit()

        users = list(session.scalars(select(User)))
        rng = random.Random(42)
        base = datetime.utcnow() - timedelta(days=120)

        def add_interaction(
            uid: int,
            bid: int,
            et: str,
            *,
            rating: float | None = None,
            day_offset: int,
        ) -> None:
            ts = base + timedelta(days=day_offset, hours=rng.randint(0, 23))
            session.add(
                Interaction(
                    user_id=uid,
                    book_id=bid,
                    event_type=et,
                    rating=rating,
                    created_at=ts,
                )
            )

        for i, u in enumerate(users):
            pool = books[:]
            rng.shuffle(pool)
            cap = min(18, len(pool))
            for j, b in enumerate(pool[:cap]):
                et = rng.choice(["rating", "like", "to_read", "finished", "rating"])
                rating = None
                if et == "rating":
                    rating = float(rng.choices([3, 4, 5], weights=[1, 2, 3])[0])
                    add_interaction(u.id, b.id, "rating", rating=rating, day_offset=j + i * 2)
                else:
                    add_interaction(u.id, b.id, et, day_offset=j + i * 2)

        n_u = len(users)
        pairs = [(0, 1), (0, 2), (1, 3), (2, 4), (3, 5), (4, 6), (5, 7)]
        for a, b in pairs:
            if a < n_u and b < n_u:
                session.add(Friendship(user_id=users[a].id, friend_id=users[b].id))
                session.add(Friendship(user_id=users[b].id, friend_id=users[a].id))
        session.commit()


def reset_and_seed() -> None:
    ensure_seed(force=True)
