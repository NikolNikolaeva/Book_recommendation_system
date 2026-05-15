"""Social boost from friends' high ratings and reading signals."""

from __future__ import annotations

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from book_recsys.orm import Book, Friendship, Interaction


def friend_ids_for(session: Session, user_id: int) -> list[int]:
    rows = session.execute(
        select(Friendship.friend_id).where(Friendship.user_id == user_id)
    ).all()
    out = [int(r[0]) for r in rows]
    rows2 = session.execute(
        select(Friendship.user_id).where(Friendship.friend_id == user_id)
    ).all()
    out.extend(int(r[0]) for r in rows2)
    return sorted(set(out))


def social_scores_vector(
    session: Session,
    user_id: int,
    book_index: dict[int, int],
    n_books: int,
) -> np.ndarray:
    vec = np.zeros(n_books, dtype=np.float64)
    friends = friend_ids_for(session, user_id)
    if not friends:
        return vec

    stmt = select(Interaction).where(
        Interaction.user_id.in_(friends),
        or_(
            Interaction.event_type == "like",
            Interaction.event_type == "finished",
            Interaction.event_type == "to_read",
            (Interaction.event_type == "rating")
            & (Interaction.rating.is_not(None))
            & (Interaction.rating >= 4.0),
        ),
    )
    for it in session.scalars(stmt):
        idx = book_index.get(it.book_id)
        if idx is None:
            continue
        boost = 1.0
        if it.event_type == "rating" and it.rating is not None:
            boost = float(it.rating) / 5.0
        elif it.event_type == "to_read":
            boost = 0.65
        vec[idx] += boost

    m = float(vec.max())
    if m > 1e-12:
        vec = vec / m
    return vec


def friend_explanation_for_book(
    session: Session,
    user_id: int,
    book: Book,
    friend_usernames: dict[int, str],
) -> str | None:
    friends = friend_ids_for(session, user_id)
    if not friends:
        return None
    stmt = (
        select(Interaction)
        .where(
            Interaction.user_id.in_(friends),
            Interaction.book_id == book.id,
        )
        .order_by(Interaction.created_at.desc())
        .limit(3)
    )
    parts: list[str] = []
    for it in session.scalars(stmt):
        uname = friend_usernames.get(it.user_id, f"user_{it.user_id}")
        if it.event_type == "rating" and it.rating is not None:
            parts.append(f"{uname} даде {it.rating:.0f}/5")
        elif it.event_type == "to_read":
            parts.append(f"{uname} добави в To-Read")
        elif it.event_type == "like":
            parts.append(f"{uname} хареса книгата")
        elif it.event_type == "finished":
            parts.append(f"{uname} я отбеляза като прочетена")
    if not parts:
        return None
    return "Приятел: " + "; ".join(parts)
