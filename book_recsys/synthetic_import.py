"""Импорт на синтетични потребители и взаимодействия (подобни на демо seed / API)."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from book_recsys.auth_pass import hash_password
from book_recsys.database import SessionLocal, init_db
from book_recsys.orm import Book, Friendship, Interaction, User

SYNTH_PASSWORD = "synth123"
SYNTH_PREFIX = "synth_"


def _tag_tokens(tags: str) -> set[str]:
    return {t.strip().lower() for t in (tags or "").split(",") if t.strip()}


def _book_score_for_prefs(book: Book, genres: set[str], authors: set[str]) -> float:
    score = 0.0
    bt = _tag_tokens(book.tags)
    ba = (book.authors or "").lower()
    desc = (book.description or "").lower()
    for g in genres:
        if g in bt or g in desc:
            score += 2.0
    for a in authors:
        if a and a in ba:
            score += 3.0
    return score


def _normalize_scores(books: list[Book], genres: set[str], authors: set[str]) -> dict[int, float]:
    raw = {b.id: _book_score_for_prefs(b, genres, authors) for b in books}
    mx = max(raw.values()) if raw else 1.0
    if mx < 1e-9:
        return {bid: 0.5 for bid in raw}
    return {bid: min(1.0, v / mx) for bid, v in raw.items()}


def _pick_interaction_like_demo(rng: random.Random, affinity: float) -> tuple[str, float | None]:
    """
    Разпределение близко до ensure_seed():
    rating (3–5★ с тежест към 4–5), like, to_read, finished; рядко dislike при ниска affinity.
    """
    weights = [5, 3, 2, 2, 4, 1]
    types = ["rating", "like", "to_read", "finished", "rating", "dislike"]
    if affinity >= 0.35:
        weights[-1] = 0
    et = rng.choices(types, weights=weights)[0]
    if et == "rating":
        if affinity >= 0.65:
            rating = float(rng.choices([4.0, 4.5, 5.0], weights=[2, 2, 3])[0])
        elif affinity >= 0.35:
            rating = float(rng.choices([3.0, 4.0, 5.0], weights=[1, 2, 3])[0])
        else:
            rating = float(rng.choices([3.0, 4.0], weights=[2, 1])[0])
        return et, rating
    return et, None


def generate_synthetic_dataset(
    books: list[Book],
    *,
    n_users: int = 35,
    min_interactions: int = 14,
    max_interactions: int = 18,
    seed: int = 2026,
    username_offset: int = 0,
) -> list[dict]:
    """Генерира потребители с взаимодействия, подобни на демо seed."""
    rng = random.Random(seed)
    genre_pool = [
        "фентъзи", "sci-fi", "класика", "трилър", "романс", "история",
        "дистопия", "young adult", "криминале", "нехудожествена",
        "психология", "българска литература", "магия", "epic", "драма",
    ]
    author_pool = [
        "роулинг", "толкин", "оруел", "вазов", "herbert", "christie",
        "austen", "маас", "толстой", "larsson", "orwell", "rowling",
        "king", "gaiman", "murakami",
    ]
    users_out: list[dict] = []
    for i in range(n_users):
        g_count = rng.randint(2, 4)
        genres = rng.sample(genre_pool, g_count)
        a_count = rng.randint(1, 3)
        authors = rng.sample(author_pool, a_count)
        lang = rng.choice(["bg", "en", "both"])
        aff_map = _normalize_scores(books, set(genres), set(authors))
        scored = sorted(books, key=lambda b: aff_map.get(b.id, 0), reverse=True)
        n_pick = rng.randint(min_interactions, min(max_interactions, len(scored)))
        pool = scored[: max(n_pick + 12, 24)]
        rng.shuffle(pool)
        picks = pool[:n_pick]
        interactions = []
        base_day = rng.randint(0, 100)
        for j, book in enumerate(picks):
            aff = aff_map.get(book.id, 0.5)
            et, rating = _pick_interaction_like_demo(rng, aff)
            interactions.append(
                {
                    "book_id": book.id,
                    "title": book.title,
                    "event_type": et,
                    "rating": rating,
                    "day_offset": base_day + j + rng.randint(0, 1),
                    "hour": rng.randint(0, 23),
                }
            )
        users_out.append(
            {
                "username": f"{SYNTH_PREFIX}{username_offset + i + 1:02d}",
                "genres": genres,
                "authors": authors,
                "language": lang,
                "interactions": interactions,
            }
        )
    return users_out


def generate_expansion_dataset(
    books: list[Book],
    existing_usernames: list[str],
    *,
    extra_per_user: int = 8,
    n_new_users: int = 20,
    seed: int = 3030,
) -> tuple[list[dict], list[dict]]:
    """
    Вълна 2: допълнителни взаимодействия за съществуващи synth потребители
    + нови потребители.
    """
    rng = random.Random(seed)
    append_rows: list[dict] = []
    for uname in existing_usernames:
        append_rows.append(
            {
                "username": uname,
                "interactions_only": True,
                "interactions": [],  # попълва се при импорт с book ids
            }
        )

    new_users = generate_synthetic_dataset(
        books,
        n_users=n_new_users,
        min_interactions=12,
        max_interactions=16,
        seed=seed + 1,
        username_offset=len(existing_usernames),
    )
    return append_rows, new_users


def _user_book_pairs(session, user_id: int) -> set[int]:
    return set(
        session.scalars(
            select(Interaction.book_id).where(Interaction.user_id == user_id)
        )
    )


def import_synthetic_users(
    dataset: list[dict],
    *,
    add_friendships: bool = True,
    allow_existing: bool = False,
) -> dict:
    """Записва синтетични потребители. При allow_existing добавя само нови взаимодействия."""
    init_db()
    created_users = 0
    created_interactions = 0
    skipped_duplicates = 0
    created_friendships = 0
    user_ids: list[int] = []

    with SessionLocal() as session:
        for entry in dataset:
            uname = entry["username"]
            existing = session.scalar(select(User).where(User.username == uname))
            if existing:
                u = existing
                if not allow_existing:
                    user_ids.append(u.id)
                    continue
            else:
                u = User(
                    username=uname,
                    password_hash=hash_password(SYNTH_PASSWORD),
                    onboarding_genres=",".join(entry.get("genres", [])),
                    onboarding_authors=",".join(entry.get("authors", [])),
                    onboarding_language=entry.get("language", "both"),
                    onboarding_completed=True,
                )
                session.add(u)
                session.flush()
                created_users += 1
            user_ids.append(u.id)

            have_books = _user_book_pairs(session, u.id)
            base = datetime.utcnow() - timedelta(days=120)
            interactions = entry.get("interactions", [])

            if entry.get("interactions_only") and not interactions:
                genres = [g.strip() for g in (u.onboarding_genres or "").split(",") if g.strip()]
                authors = [a.strip() for a in (u.onboarding_authors or "").split(",") if a.strip()]
                books = list(session.scalars(select(Book)))
                aff_map = _normalize_scores(books, set(genres), set(authors))
                candidates = [b for b in books if b.id not in have_books]
                candidates.sort(key=lambda b: aff_map.get(b.id, 0), reverse=True)
                rng = random.Random(hash(uname) % 2**31)
                extra_n = min(8, len(candidates))
                picks = candidates[: extra_n + 5]
                rng.shuffle(picks)
                interactions = []
                for j, book in enumerate(picks[:extra_n]):
                    aff = aff_map.get(book.id, 0.5)
                    et, rating = _pick_interaction_like_demo(rng, aff)
                    interactions.append(
                        {
                            "book_id": book.id,
                            "event_type": et,
                            "rating": rating,
                            "day_offset": 95 + j,
                            "hour": rng.randint(0, 23),
                        }
                    )

            for it in interactions:
                bid = it.get("book_id")
                if bid is None:
                    continue
                book = session.get(Book, int(bid))
                if book is None:
                    continue
                if book.id in have_books:
                    skipped_duplicates += 1
                    continue
                have_books.add(book.id)
                day = int(it.get("day_offset", 0))
                hour = int(it.get("hour", 12))
                ts = base + timedelta(days=day, hours=hour)
                session.add(
                    Interaction(
                        user_id=u.id,
                        book_id=book.id,
                        event_type=it["event_type"],
                        rating=it.get("rating"),
                        created_at=ts,
                    )
                )
                created_interactions += 1

        if add_friendships and len(user_ids) >= 4:
            rng = random.Random(99)
            demo = list(
                session.scalars(
                    select(User).where(
                        User.username.in_(
                            ["nikol", "gabriela", "mira", "stefan", "elena", "dimitar", "hana"]
                        )
                    )
                )
            )
            synth_ids = [
                uid
                for uid in user_ids
                if session.get(User, uid) and str(session.get(User, uid).username).startswith(SYNTH_PREFIX)
            ]
            for du in demo:
                pool = synth_ids if synth_ids else user_ids
                for su_id in rng.sample(pool, min(4, len(pool))):
                    if du.id == su_id:
                        continue
                    if not session.scalar(
                        select(Friendship).where(
                            Friendship.user_id == du.id,
                            Friendship.friend_id == su_id,
                        )
                    ):
                        session.add(Friendship(user_id=du.id, friend_id=su_id))
                        session.add(Friendship(user_id=su_id, friend_id=du.id))
                        created_friendships += 2
            for i in range(0, len(synth_ids) - 1, 3):
                if i + 1 >= len(synth_ids):
                    break
                a, b = synth_ids[i], synth_ids[i + 1]
                if not session.scalar(
                    select(Friendship).where(Friendship.user_id == a, Friendship.friend_id == b)
                ):
                    session.add(Friendship(user_id=a, friend_id=b))
                    session.add(Friendship(user_id=b, friend_id=a))
                    created_friendships += 2

        session.commit()

    return {
        "users_created": created_users,
        "interactions_created": created_interactions,
        "interactions_skipped_duplicate": skipped_duplicates,
        "friendships_created": created_friendships,
    }


def generate_and_import(
    *,
    n_users: int = 35,
    min_interactions: int = 14,
    max_interactions: int = 18,
    seed: int = 2026,
    save_json: Path | None = None,
) -> dict:
    init_db()
    with SessionLocal() as session:
        books = list(session.scalars(select(Book)))
    dataset = generate_synthetic_dataset(
        books,
        n_users=n_users,
        min_interactions=min_interactions,
        max_interactions=max_interactions,
        seed=seed,
    )
    if save_json:
        save_json.parent.mkdir(parents=True, exist_ok=True)
        save_json.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = import_synthetic_users(dataset, add_friendships=True)
    stats["dataset_size"] = len(dataset)
    return stats


def expand_synthetic_wave2(
    *,
    n_new_users: int = 20,
    seed: int = 3030,
    save_json: Path | None = None,
) -> dict:
    """Вълна 2: още взаимодействия + нови потребители."""
    init_db()
    with SessionLocal() as session:
        books = list(session.scalars(select(Book)))
        existing = list(
            session.scalars(
                select(User.username).where(User.username.like(f"{SYNTH_PREFIX}%"))
            )
        )
    append_meta, new_users = generate_expansion_dataset(
        books,
        list(existing),
        n_new_users=n_new_users,
        seed=seed,
    )
    combined = append_meta + new_users
    if save_json:
        save_json.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = import_synthetic_users(combined, add_friendships=True, allow_existing=True)
    stats["new_users_in_wave"] = n_new_users
    stats["expanded_existing"] = len(existing)
    return stats
