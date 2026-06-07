"""Hybrid scorer, MMR diversity, explanations."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select
from sqlalchemy.orm import Session

from book_recsys.config import COLD_START_MAX_INTERACTIONS, MMR_DEFAULT_LAMBDA
from book_recsys.tuning import HybridTuning, get_tuning
from book_recsys.orm import Book, Interaction, User
from book_recsys.recommender.cbf import build_cbf_matrix, combined_cbf_scores, survey_explain_fragment
from book_recsys.recommender.cf_model import CFModel, build_user_item_matrix, interaction_weight
from book_recsys.recommender.social import friend_explanation_for_book, friend_ids_for, social_scores_vector


@dataclass
class HybridContext:
    books: list[Book]
    book_index: dict[int, int]
    internal_user_row: dict[int, int]
    vectorizer: TfidfVectorizer
    tfidf_sparse: object
    tfidf_mat: object
    cf_model: CFModel
    popular: np.ndarray


def _popular_vector(session: Session, book_index: dict[int, int], n_books: int) -> np.ndarray:
    vec = np.zeros(n_books, dtype=np.float64)
    stmt = select(Interaction.book_id).where(
        (Interaction.event_type == "rating") & (Interaction.rating >= 4.0)
        | (Interaction.event_type.in_(["like", "finished"]))
    )
    for bid in session.scalars(stmt):
        idx = book_index.get(int(bid))
        if idx is not None:
            vec[idx] += 1.0
    m = float(vec.max())
    if m > 1e-12:
        vec = vec / m
    return vec


def build_context(session: Session) -> HybridContext:
    books = list(session.scalars(select(Book).order_by(Book.id)))
    book_index = {b.id: i for i, b in enumerate(books)}
    n_books = len(books)

    users = list(session.scalars(select(User).order_by(User.id)))
    internal_user_row = {u.id: i for i, u in enumerate(users)}
    n_users = len(users)

    vectorizer, tfidf_sp = build_cbf_matrix(books)

    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for it in session.scalars(select(Interaction)):
        ui = internal_user_row.get(it.user_id)
        bi = book_index.get(it.book_id)
        if ui is None or bi is None:
            continue
        w = interaction_weight(it.event_type, it.rating)
        if w <= 0:
            continue
        rows.append(ui)
        cols.append(bi)
        vals.append(w)

    r_mat = build_user_item_matrix(n_users, n_books, rows, cols, vals)
    cf = CFModel()
    cf.fit(r_mat)

    pop = _popular_vector(session, book_index, n_books)
    return HybridContext(
        books=books,
        book_index=book_index,
        internal_user_row=internal_user_row,
        vectorizer=vectorizer,
        tfidf_sparse=tfidf_sp,
        tfidf_mat=tfidf_sp,
        cf_model=cf,
        popular=pop,
    )


def _mmr(
    candidate_indices: list[int],
    relevance: np.ndarray,
    item_sim: np.ndarray,
    k: int,
    lambda_mult: float,
) -> list[int]:
    """Maximal Marginal Relevance."""
    selected: list[int] = []
    remaining = set(candidate_indices)
    while remaining and len(selected) < k:
        best_i = None
        best_score = -1e9
        for i in remaining:
            rel = relevance[i]
            if selected:
                div = max(float(item_sim[i, j]) for j in selected)
            else:
                div = 0.0
            score = lambda_mult * rel - (1.0 - lambda_mult) * div
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is None:
            break
        selected.append(best_i)
        remaining.discard(best_i)
    return selected


def hybrid_scores(
    session: Session,
    ctx: HybridContext,
    user: User,
    omit_interaction_ids: set[int] | None = None,
    tuning: HybridTuning | None = None,
) -> tuple[np.ndarray, float, float, float]:
    iu = ctx.internal_user_row.get(user.id)
    n_books = len(ctx.books)
    interactions_all = list(
        session.scalars(
            select(Interaction).where(Interaction.user_id == user.id).order_by(Interaction.created_at)
        )
    )
    if omit_interaction_ids:
        interactions = [it for it in interactions_all if it.id not in omit_interaction_ids]
    else:
        interactions = interactions_all
    n_sig = sum(
        1
        for x in interactions
        if x.event_type in ("like", "dislike", "rating", "finished", "to_read")
    )

    cf_raw = (
        ctx.cf_model.predict_scores_for_user(iu, n_books)
        if iu is not None
        else np.zeros(n_books, dtype=np.float64)
    )

    cbf_raw = combined_cbf_scores(
        user,
        ctx.books,
        ctx.book_index,
        ctx.tfidf_sparse,
        interactions,
        onboarding_weight=0.5 if n_sig < COLD_START_MAX_INTERACTIONS else 0.35,
    )
    social_raw = social_scores_vector(session, user.id, ctx.book_index, n_books)

    has_friends = len(friend_ids_for(session, user.id)) > 0
    t = tuning or get_tuning()

    if n_sig < COLD_START_MAX_INTERACTIONS:
        a, b, g = t.cold_weight_cf, t.cold_weight_cbf, t.cold_weight_social if has_friends else 0.0
    else:
        a, b, g = t.weight_cf, t.weight_cbf, t.weight_social if has_friends else 0.0

    if has_friends and g > 0 and n_books > 0:
        smax = float(social_raw.max())
        if smax > 0.21:
            g = min(t.social_boost_cap, g * t.social_boost_factor)

    pop = ctx.popular
    rest = max(0.0, 1.0 - a - b - g)
    raw = a * cf_raw + b * cbf_raw + g * social_raw + rest * pop
    return raw, a, b, g


def excluded_book_ids(session: Session, user_id: int) -> set[int]:
    out: set[int] = set()
    # Не предлагаме книги, които потребителят вече е докоснал (like/rating/to_read/finished/dislike),
    # за да са препоръките "нови" и по-полезни.
    for it in session.scalars(select(Interaction).where(Interaction.user_id == user_id)):
        if it.event_type in ("dislike", "like", "to_read", "finished"):
            out.add(it.book_id)
        elif it.event_type == "rating" and it.rating is not None:
            out.add(it.book_id)
    return out


def rank_books_hybrid(
    session: Session,
    ctx: HybridContext,
    user: User,
    *,
    k: int = 10,
    omit_interaction_ids: set[int] | None = None,
    tuning: HybridTuning | None = None,
    use_mmr: bool = True,
    with_social: bool = False,
    for_eval: bool = False,
    candidate_pool: int = 80,
) -> list[int]:
    """Подредени book_id; for_eval=True подрежда всички книги (за leave-last-out)."""
    t = tuning or get_tuning()
    scores, _, _, _ = hybrid_scores(session, ctx, user, omit_interaction_ids=omit_interaction_ids, tuning=t)
    n_books = len(ctx.books)
    if with_social:
        soc = social_scores_vector(session, user.id, ctx.book_index, n_books)
        if float(soc.max()) > 0:
            scores = t.social_blend_hybrid * scores + t.social_blend_social * soc
    if for_eval:
        cand = list(range(n_books))
    else:
        exclude = excluded_book_ids(session, user.id)
        cand = [i for i in range(n_books) if ctx.books[i].id not in exclude]
    cand.sort(key=lambda i: float(scores[i]), reverse=True)
    cand = cand[:candidate_pool]
    if not cand:
        return []
    if not use_mmr or len(cand) <= k:
        return [ctx.books[i].id for i in cand[:k]]
    sim = cosine_similarity(ctx.tfidf_mat[cand], ctx.tfidf_mat[cand])
    rel = scores[cand]
    picked_local = _mmr(list(range(len(cand))), rel, sim, k=min(k, len(cand)), lambda_mult=t.mmr_lambda)
    return [ctx.books[cand[li]].id for li in picked_local]


def recommend(
    session: Session,
    ctx: HybridContext,
    user: User,
    k: int = 20,
    diversity_lambda: float | None = None,
    candidate_pool: int = 80,
    omit_interaction_ids: set[int] | None = None,
) -> list[tuple[Book, float, str]]:
    t = get_tuning()
    mmr_l = diversity_lambda if diversity_lambda is not None else t.mmr_lambda
    scores, _, _, _ = hybrid_scores(session, ctx, user, omit_interaction_ids=omit_interaction_ids, tuning=t)
    exclude = excluded_book_ids(session, user.id)

    cand = [i for i in range(len(ctx.books)) if ctx.books[i].id not in exclude]
    cand.sort(key=lambda i: float(scores[i]), reverse=True)
    cand = cand[:candidate_pool]

    sim = cosine_similarity(ctx.tfidf_mat[cand], ctx.tfidf_mat[cand]) if cand else np.array([[1.0]])
    rel = scores[cand]
    idxs = list(range(len(cand)))
    picked_local = _mmr(idxs, rel, sim, k=min(k, len(idxs)), lambda_mult=mmr_l)
    picked_global = [cand[li] for li in picked_local]

    interactions = list(session.scalars(select(Interaction).where(Interaction.user_id == user.id)))
    if omit_interaction_ids:
        interactions = [it for it in interactions if it.id not in omit_interaction_ids]
    liked_books: list[Book] = []
    for it in interactions:
        if it.event_type in ("like", "finished", "to_read"):
            if it.book_id in ctx.book_index:
                liked_books.append(ctx.books[ctx.book_index[it.book_id]])
        if it.event_type == "rating" and it.rating is not None and it.rating >= 4.0:
            if it.book_id in ctx.book_index:
                liked_books.append(ctx.books[ctx.book_index[it.book_id]])

    id_to_name = {u.id: u.username for u in session.scalars(select(User))}

    def explain(book: Book) -> str:
        reasons: list[str] = []
        fe = friend_explanation_for_book(session, user.id, book, id_to_name)
        if fe:
            reasons.append(fe)
        se = survey_explain_fragment(user, book)
        if se:
            reasons.append(se)
        if user.onboarding_genres:
            prefs = [g.strip() for g in user.onboarding_genres.split(",") if g.strip()]
            overlap = [g for g in prefs if g.lower() in (book.tags + book.title).lower()]
            if overlap:
                reasons.append("жанр: " + ", ".join(overlap[:2]))
        if user.onboarding_authors:
            aprefs = [a.strip() for a in user.onboarding_authors.split(",") if a.strip()]
            amatch = [a for a in aprefs if len(a) >= 2 and a.lower() in book.authors.lower()]
            if amatch:
                reasons.append("автор: " + ", ".join(amatch[:2]))
        if liked_books:
            bi = ctx.book_index[book.id]
            best = None
            best_s = -1.0
            for lb in liked_books[:15]:
                li = ctx.book_index.get(lb.id)
                if li is None:
                    continue
                s = float(
                    cosine_similarity(ctx.tfidf_mat[bi].reshape(1, -1), ctx.tfidf_mat[li].reshape(1, -1))[
                        0, 0
                    ]
                )
                if s > best_s:
                    best_s = s
                    best = lb
            if best is not None and best_s > 0.25:
                reasons.append(f"подобна на «{best.title}»")
        if not reasons:
            reasons.append("силна съгласуваност с каталога и твоите сигнали")
        return "Защо: " + " · ".join(reasons)

    out: list[tuple[Book, float, str]] = []
    for gi in picked_global:
        b = ctx.books[gi]
        out.append((b, float(scores[gi]), explain(b)))
    return out


def similar_books(ctx: HybridContext, book_id: int, k: int = 8) -> list[Book]:
    if book_id not in ctx.book_index:
        return []
    bi = ctx.book_index[book_id]
    sims = cosine_similarity(ctx.tfidf_mat[bi].reshape(1, -1), ctx.tfidf_mat).ravel()
    idxs = np.argsort(-sims)
    out: list[Book] = []
    for j in idxs:
        if int(j) == bi:
            continue
        out.append(ctx.books[int(j)])
        if len(out) >= k:
            break
    return out
