"""Offline grid search за HybridTuning."""

from __future__ import annotations

import itertools
from dataclasses import replace

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from book_recsys.orm import Book, Interaction, User
from book_recsys.recommender.cf_model import CFModel, build_user_item_matrix, interaction_weight
from book_recsys.recommender.hybrid import HybridContext, build_context, rank_books_hybrid
from book_recsys.tuning import HybridTuning, default_tuning, save_tuning
from book_recsys.evaluation import _precision_recall_ndcg, _train_cf_without_interactions


def _build_fold(session: Session) -> tuple[dict[int, tuple[int, int]], set[int], HybridContext, CFModel, dict[int, int], list[Book]]:
    tests: dict[int, tuple[int, int]] = {}
    skip_ids: set[int] = set()
    for u in session.scalars(select(User)):
        rows = list(
            session.scalars(
                select(Interaction)
                .where(Interaction.user_id == u.id)
                .order_by(Interaction.created_at, Interaction.id)
            )
        )
        last_it = None
        for it in reversed(rows):
            if it.event_type in ("like", "rating", "finished") and (
                it.event_type != "rating" or (it.rating is not None and it.rating >= 4.0)
            ):
                last_it = it
                break
        if last_it is None:
            continue
        tests[u.id] = (last_it.book_id, last_it.id)
        skip_ids.add(last_it.id)

    books = list(session.scalars(select(Book).order_by(Book.id)))
    cf_model, internal_user_row, _, _, _ = _train_cf_without_interactions(session, skip_ids)
    full_ctx = build_context(session)
    return tests, skip_ids, full_ctx, cf_model, internal_user_row, books


def score_tuning(
    session: Session,
    tuning: HybridTuning,
    *,
    k: int = 10,
    use_mmr: bool = False,
) -> dict[str, float]:
    """Среден NDCG@K за Hybrid и Hybrid+Social при дадени параметри."""
    tests, _, full_ctx, cf_model, _, books = _build_fold(session)
    if not tests:
        return {"Hybrid": 0.0, "Hybrid+Social": 0.0, "combined": 0.0}

    n_items = len(books)
    hybrid_ndcgs: list[float] = []
    social_ndcgs: list[float] = []

    for uid, (held_book, held_iid) in tests.items():
        user = session.get(User, uid)
        if user is None:
            continue
        positive = {held_book}
        ctx2 = HybridContext(
            books=full_ctx.books,
            book_index=full_ctx.book_index,
            internal_user_row=full_ctx.internal_user_row,
            vectorizer=full_ctx.vectorizer,
            tfidf_sparse=full_ctx.tfidf_sparse,
            tfidf_mat=full_ctx.tfidf_mat,
            cf_model=cf_model,
            popular=full_ctx.popular,
        )
        rank_k = k if use_mmr else n_items
        h_ids = rank_books_hybrid(
            session,
            ctx2,
            user,
            k=rank_k,
            omit_interaction_ids={held_iid},
            tuning=tuning,
            use_mmr=use_mmr,
            with_social=False,
            for_eval=not use_mmr,
        )
        _, _, h_ndcg = _precision_recall_ndcg(h_ids, positive, k)
        hybrid_ndcgs.append(h_ndcg)

        s_ids = rank_books_hybrid(
            session,
            ctx2,
            user,
            k=rank_k,
            omit_interaction_ids={held_iid},
            tuning=tuning,
            use_mmr=use_mmr,
            with_social=True,
            for_eval=not use_mmr,
        )
        _, _, s_ndcg = _precision_recall_ndcg(s_ids, positive, k)
        social_ndcgs.append(s_ndcg)

    h_mean = float(np.mean(hybrid_ndcgs)) if hybrid_ndcgs else 0.0
    s_mean = float(np.mean(social_ndcgs)) if social_ndcgs else 0.0
    return {"Hybrid": h_mean, "Hybrid+Social": s_mean, "combined": 0.5 * h_mean + 0.5 * s_mean}


def grid_search(session: Session, *, k: int = 10) -> tuple[HybridTuning, dict]:
    """Двустепенен grid: тегла (без MMR), после MMR λ."""
    baseline = default_tuning()
    base_score = score_tuning(session, baseline, k=k, use_mmr=False)
    best = baseline
    best_score = base_score["combined"]

    warm_triples = [
        (0.40, 0.35, 0.20),
        (0.45, 0.35, 0.20),
        (0.50, 0.30, 0.15),
        (0.35, 0.45, 0.15),
        (0.45, 0.40, 0.10),
        (0.40, 0.30, 0.25),
        (0.55, 0.30, 0.10),
        (0.35, 0.35, 0.25),
    ]
    cold_triples = [
        (0.15, 0.55, 0.15),
        (0.10, 0.60, 0.10),
        (0.20, 0.50, 0.15),
        (0.15, 0.50, 0.20),
    ]
    social_blends = [(0.55, 0.45), (0.50, 0.50), (0.60, 0.40), (0.65, 0.35)]

    tried = 0
    for (wcf, wcbf, wsoc), (ccf, ccbf, csoc), (sbh, sbs) in itertools.product(
        warm_triples, cold_triples, social_blends
    ):
        t = replace(
            baseline,
            weight_cf=wcf,
            weight_cbf=wcbf,
            weight_social=wsoc,
            cold_weight_cf=ccf,
            cold_weight_cbf=ccbf,
            cold_weight_social=csoc,
            social_blend_hybrid=sbh,
            social_blend_social=sbs,
        )
        tried += 1
        sc = score_tuning(session, t, k=k, use_mmr=False)["combined"]
        if sc > best_score:
            best_score = sc
            best = t

    mmr_best = best.mmr_lambda
    for lam in (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
        t = replace(best, mmr_lambda=lam)
        tried += 1
        sc = score_tuning(session, t, k=k, use_mmr=True)["combined"]
        if sc > best_score:
            best_score = sc
            best = t
            mmr_best = lam

    meta = {
        "baseline_combined_ndcg": base_score["combined"],
        "best_combined_ndcg": best_score,
        "improvement": best_score - base_score["combined"],
        "combinations_tried": tried,
        "selected_mmr_lambda": mmr_best,
    }
    return best, meta
