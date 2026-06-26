"""Offline evaluation: leave-last-out, Precision/Recall/NDCG @ K."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import random

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select
from sqlalchemy.orm import Session

from book_recsys.database import SessionLocal, init_db
from book_recsys.orm import Book, Interaction, User
from book_recsys.recommender.cbf import build_cbf_matrix, combined_cbf_scores
from book_recsys.recommender.cf_model import CFModel, build_user_item_matrix, interaction_weight
from book_recsys.recommender.hybrid import HybridContext, rank_books_hybrid
from book_recsys.tuning import get_tuning
from book_recsys.schemas import EvaluationReport, EvaluationRow

# Текстови блокове за отчет/демо — съответстват на очакваната структура от курса (проблем, хипотеза, методология).
EVAL_PROBLEM_BG = (
    "Потребителите се нуждаят от персонализирани препоръки в голям каталог при ограничена или липсваща "
    "история (cold start). Системата трябва да обяснява защо се препоръчва дадена книга и да включва "
    "социален контекст, когато потребителят има приятели в платформата."
)

EVAL_HYPOTHESIS_BG = (
    "Хибридно подреждане (колаборативна филтрация + съдържателно-базирана част чрез TF-IDF върху текст и тагове, "
    "допълнено с популярност и при нужда социален сигнал) дава по-добро качество от изолирана популярност или "
    "самостоятелен CF при оскъдни данни; MMR повишава разнообразието в Top-K списъка."
)

EVAL_METHODOLOGY_BG = (
    "Offline оценка с leave-last-out: за всеки потребител с поне един положителен сигнал (like, оценка ≥4, "
    "«прочетена») се „скрива“ последният такъв запис по време; моделите се фитират без него и се подреждат "
    "книгите. Отчитат се средни Precision@K, Recall@K и NDCG@K, плюс diversity: intra-list similarity "
    "(средна двойна cosine similarity в Top-K по TF-IDF; по-ниска = по-разнообразно) и long-tail coverage "
    "(доля препоръки извън top 20% най-популярни книги по training сигнали)."
)

EVAL_LIMITATIONS_BG = (
    "При малък или синтетичен каталог абсолютните метрики са ориентировъчни; по-смислено е сравнението между "
    "редовете в таблицата. За production са подходящи повече данни, други протоколи (напр. временни прозорци) "
    "и онлайн експерименти."
)


@dataclass
class EvalResult:
    method: str
    precision_at_k: float
    recall_at_k: float
    ndcg_at_k: float
    intra_list_similarity: float = 0.0
    long_tail_coverage: float = 0.0


@dataclass
class _UserEvalMetrics:
    precision_at_k: float
    recall_at_k: float
    ndcg_at_k: float
    intra_list_similarity: float
    long_tail_coverage: float


def _positive_interaction_sql():
    return (Interaction.event_type.in_(["like", "finished"])) | (
        (Interaction.event_type == "rating") & (Interaction.rating >= 4.0)
    )


def _precision_recall_ndcg(
    ranked: list[int],
    positive: set[int],
    k: int,
) -> tuple[float, float, float]:
    if not positive:
        return 0.0, 0.0, 0.0
    top = ranked[:k]
    hits = sum(1 for x in top if x in positive)
    prec = hits / k
    rec = hits / max(len(positive), 1)
    dcg = 0.0
    for i, bid in enumerate(top):
        if bid in positive:
            dcg += 1.0 / np.log2(i + 2)
    idcg = sum(1.0 / np.log2(i + 2) for i in range(min(len(positive), k)))
    ndcg = float(dcg / idcg) if idcg > 0 else 0.0
    return float(prec), float(rec), ndcg


def _intra_list_similarity(
    ranked: list[int],
    book_index: dict[int, int],
    tfidf_mat,
    k: int,
) -> float:
    """Average pairwise cosine similarity in Top-K (lower → more diverse)."""
    indices = [book_index[int(bid)] for bid in ranked[:k] if int(bid) in book_index]
    if len(indices) < 2:
        return 0.0
    sim = cosine_similarity(tfidf_mat[indices], tfidf_mat[indices])
    n = len(indices)
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += float(sim[i, j])
            pairs += 1
    return total / pairs if pairs else 0.0


def _long_tail_book_ids(
    pop_counts: np.ndarray,
    book_ids_by_index: list[int],
    *,
    head_fraction: float = 0.2,
) -> set[int]:
    """Books outside the top `head_fraction` by training popularity."""
    n = int(pop_counts.shape[0])
    if n <= 0:
        return set()
    order = np.argsort(-pop_counts)
    n_head = max(1, int(np.ceil(float(head_fraction) * n)))
    head_idx = set(int(i) for i in order[:n_head].tolist())
    return {int(book_ids_by_index[i]) for i in range(n) if i not in head_idx}


def _long_tail_coverage(ranked: list[int], long_tail_ids: set[int], k: int) -> float:
    top = ranked[:k]
    if not top:
        return 0.0
    hits = sum(1 for bid in top if int(bid) in long_tail_ids)
    return float(hits) / float(len(top))


def _metrics_for_ranking(
    ranked: list[int],
    positive: set[int],
    k: int,
    *,
    book_index: dict[int, int],
    tfidf_mat,
    long_tail_ids: set[int],
) -> _UserEvalMetrics:
    p, r, n = _precision_recall_ndcg(ranked, positive, k)
    intra = _intra_list_similarity(ranked, book_index, tfidf_mat, k)
    lt = _long_tail_coverage(ranked, long_tail_ids, k)
    return _UserEvalMetrics(p, r, n, intra, lt)


def _train_cf_without_interactions(
    session: Session,
    skip_ids: set[int],
) -> tuple[CFModel, dict[int, int], dict[int, int], int, int]:
    # Use only IDs here to reduce ORM overhead.
    user_ids = [int(x) for x in session.scalars(select(User.id).order_by(User.id)).all()]
    book_ids = [int(x) for x in session.scalars(select(Book.id).order_by(Book.id)).all()]
    internal_user_row = {uid: i for i, uid in enumerate(user_ids)}
    book_index = {bid: i for i, bid in enumerate(book_ids)}
    n_users, n_items = len(user_ids), len(book_ids)
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    stmt = select(
        Interaction.id,
        Interaction.user_id,
        Interaction.book_id,
        Interaction.event_type,
        Interaction.rating,
    )
    for iid, uid, bid, et, rating in session.execute(stmt):
        if int(iid) in skip_ids:
            continue
        ui = internal_user_row.get(int(uid))
        bi = book_index.get(int(bid))
        if ui is None or bi is None:
            continue
        w = interaction_weight(str(et), float(rating) if rating is not None else None)
        if w <= 0:
            continue
        rows.append(ui)
        cols.append(bi)
        vals.append(w)
    R = build_user_item_matrix(n_users, n_items, rows, cols, vals)
    cf = CFModel()
    cf.fit(R)
    return cf, internal_user_row, book_index, n_users, n_items


def _popular_counts(
    session: Session,
    book_index: dict[int, int],
    n_items: int,
    *,
    skip_ids: set[int],
) -> np.ndarray:
    """Raw positive-signal counts per book index (for long-tail split)."""
    counts = np.zeros(n_items, dtype=np.float64)
    stmt = select(
        Interaction.id,
        Interaction.book_id,
        Interaction.event_type,
        Interaction.rating,
    )
    for iid, bid, et, rating in session.execute(stmt):
        if int(iid) in skip_ids:
            continue
        bi = book_index.get(int(bid))
        if bi is None:
            continue
        if str(et) in ("like", "finished") or (
            str(et) == "rating" and rating is not None and float(rating) >= 4.0
        ):
            counts[bi] += 1.0
    return counts


def _top_k_book_ids_from_scores(scores: np.ndarray, book_ids_by_index: list[int], k: int) -> list[int]:
    n = int(scores.shape[0])
    if n <= 0:
        return []
    kk = max(1, min(int(k), n))
    if kk == n:
        order = np.argsort(-scores)
        return [int(book_ids_by_index[int(i)]) for i in order.tolist()]
    idx = np.argpartition(-scores, kk - 1)[:kk]
    idx = idx[np.argsort(-scores[idx])]
    return [int(book_ids_by_index[int(i)]) for i in idx.tolist()]

def _iter_chunks(xs: list[int], size: int) -> list[list[int]]:
    """Chunk helper to avoid SQLite max-params issues for IN (...)."""
    if size <= 0:
        return [xs]
    return [xs[i : i + size] for i in range(0, len(xs), size)]


def evaluate_session(
    session: Session,
    k: int = 10,
    *,
    max_users: int | None = None,
    seed: int = 42,
) -> tuple[list[EvalResult], int, int]:
    """
    Връща (методи с усреднени метрики, брой потребители в оценката, общо потребители във fold-а).

    Забележка: при големи БД правим sampling (max_users), за да е интерактивно в UI.
    """
    # 1) Leave-last-out: взимаме последния положителен сигнал за всеки user (като тест).
    tests_all: dict[int, tuple[int, int]] = {}
    stmt_holdout = (
        select(Interaction.user_id, Interaction.book_id, Interaction.id)
        .where(_positive_interaction_sql())
        .order_by(Interaction.user_id, Interaction.created_at, Interaction.id)
    )
    for uid, bid, iid in session.execute(stmt_holdout):
        tests_all[int(uid)] = (int(bid), int(iid))
    total_fold_users = len(tests_all)
    if total_fold_users == 0:
        return [], 0, 0

    user_ids = list(tests_all.keys())
    if max_users is not None and total_fold_users > max_users:
        rng = random.Random(int(seed))
        rng.shuffle(user_ids)
        user_ids = user_ids[: int(max_users)]
    tests: dict[int, tuple[int, int]] = {uid: tests_all[uid] for uid in user_ids}
    skip_interaction_ids: set[int] = {iid for _, iid in tests.values()}

    # 2) Книги + TF‑IDF (еднократно)
    books = list(session.scalars(select(Book).order_by(Book.id)))
    if not books:
        return [], 0, total_fold_users
    book_ids_by_index = [int(b.id) for b in books]
    book_index = {int(b.id): i for i, b in enumerate(books)}
    n_items = len(books)
    k_eval = max(1, min(int(k), n_items))

    # TF‑IDF матрица е скъпа — строим веднъж и ре‑ползваме за всички методи.
    vectorizer, tfidf_sp = build_cbf_matrix(books)

    # 3) Popular (еднократно) + CF model (еднократно)
    pop_counts = _popular_counts(session, book_index, n_items, skip_ids=skip_interaction_ids)
    pop_vec = pop_counts.copy()
    m = float(pop_vec.max())
    if m > 1e-12:
        pop_vec = pop_vec / m
    pop_topk = _top_k_book_ids_from_scores(pop_vec, book_ids_by_index, k_eval)
    long_tail_ids = _long_tail_book_ids(pop_counts, book_ids_by_index)

    cf_model, internal_user_row, _, _, _ = _train_cf_without_interactions(session, skip_interaction_ids)

    # 4) Prefetch потребители + взаимодействия за sample-а (за CBF-only)
    eval_uids = [int(x) for x in tests.keys()]
    users_by_id: dict[int, User] = {}
    for chunk in _iter_chunks(eval_uids, 900):
        for u in session.scalars(select(User).where(User.id.in_(chunk))):
            users_by_id[int(u.id)] = u
    by_user: dict[int, list[Interaction]] = defaultdict(list)
    for chunk in _iter_chunks(eval_uids, 900):
        for it in session.scalars(select(Interaction).where(Interaction.user_id.in_(chunk))):
            by_user[int(it.user_id)].append(it)

    # 5) Hybrid контекст за evaluation (без да rebuild-ваме целия build_context())
    ctx2 = HybridContext(
        books=books,
        book_index=book_index,
        internal_user_row=internal_user_row,
        vectorizer=vectorizer,
        tfidf_sparse=tfidf_sp,
        tfidf_mat=tfidf_sp,
        cf_model=cf_model,
        popular=pop_vec,
    )
    tuning = get_tuning()

    results: dict[str, list[_UserEvalMetrics]] = {
        "Popular": [],
        "CBF-only": [],
        "CF-only": [],
        "Hybrid": [],
        "Hybrid+Social": [],
    }

    for uid, (held_book, held_iid) in tests.items():
        user = users_by_id.get(int(uid))
        if user is None:
            continue
        if int(held_book) not in book_index:
            continue
        positive = {int(held_book)}
        div_kw = {
            "book_index": book_index,
            "tfidf_mat": tfidf_sp,
            "long_tail_ids": long_tail_ids,
        }

        results["Popular"].append(
            _metrics_for_ranking(pop_topk, positive, k_eval, **div_kw)
        )

        interactions_train = [it for it in by_user.get(int(uid), []) if int(it.id) != int(held_iid)]
        cbf_scores = combined_cbf_scores(user, books, book_index, tfidf_sp, interactions_train)
        cbf_topk = _top_k_book_ids_from_scores(cbf_scores, book_ids_by_index, k_eval)
        results["CBF-only"].append(
            _metrics_for_ranking(cbf_topk, positive, k_eval, **div_kw)
        )

        iu = internal_user_row.get(int(uid), -1)
        cf_scores = cf_model.predict_scores_for_user(iu, n_items)
        cf_topk = _top_k_book_ids_from_scores(cf_scores, book_ids_by_index, k_eval)
        results["CF-only"].append(
            _metrics_for_ranking(cf_topk, positive, k_eval, **div_kw)
        )

        hyb_topk = rank_books_hybrid(
            session,
            ctx2,
            user,
            k=k_eval,
            omit_interaction_ids={int(held_iid)},
            tuning=tuning,
            use_mmr=True,
            with_social=False,
            for_eval=True,
        )
        results["Hybrid"].append(
            _metrics_for_ranking(hyb_topk, positive, k_eval, **div_kw)
        )

        hyb_soc_topk = rank_books_hybrid(
            session,
            ctx2,
            user,
            k=k_eval,
            omit_interaction_ids={int(held_iid)},
            tuning=tuning,
            use_mmr=True,
            with_social=True,
            for_eval=True,
        )
        results["Hybrid+Social"].append(
            _metrics_for_ranking(hyb_soc_topk, positive, k_eval, **div_kw)
        )

    out: list[EvalResult] = []
    for name, rows in results.items():
        if not rows:
            continue
        out.append(
            EvalResult(
                method=name,
                precision_at_k=float(np.mean([x.precision_at_k for x in rows])),
                recall_at_k=float(np.mean([x.recall_at_k for x in rows])),
                ndcg_at_k=float(np.mean([x.ndcg_at_k for x in rows])),
                intra_list_similarity=float(np.mean([x.intra_list_similarity for x in rows])),
                long_tail_coverage=float(np.mean([x.long_tail_coverage for x in rows])),
            )
        )
    return out, len(tests), total_fold_users


def build_evaluation_report(session: Session, k: int, *, max_users: int | None = 120) -> EvaluationReport:
    rows_eval, n_fold, n_total = evaluate_session(session, k=k, max_users=max_users)
    rows = [
        EvaluationRow(
            method=r.method,
            precision_at_k=round(r.precision_at_k, 4),
            recall_at_k=round(r.recall_at_k, 4),
            ndcg_at_k=round(r.ndcg_at_k, 4),
            intra_list_similarity=round(r.intra_list_similarity, 4),
            long_tail_coverage=round(r.long_tail_coverage, 4),
        )
        for r in rows_eval
    ]
    note = (
        "Leave-last-out по последен положителен сигнал (like/rating≥4/finished) за всеки потребител; "
        "CF се тренира без отделените взаимодействия; CBF/hybrid за потребителя без неговия отделен ред. "
        "Intra-sim: средна двойна TF-IDF similarity в Top-K (по-ниска = по-разнообразно). "
        "Long-tail: дял препоръки извън top 20% най-популярни книги."
    )
    if max_users is not None and n_total > 0 and n_fold and n_fold < n_total:
        note = f"{note} (извадка: {n_fold}/{n_total} потребители за по-бързо UI)"
    return EvaluationReport(
        k=k,
        rows=rows,
        note=note,
        users_in_fold=n_fold,
        problem_statement=EVAL_PROBLEM_BG,
        hypothesis=EVAL_HYPOTHESIS_BG,
        methodology=EVAL_METHODOLOGY_BG,
        limitations=EVAL_LIMITATIONS_BG,
    )


def run_evaluation(k: int = 10, *, max_users: int | None = None) -> tuple[list[EvalResult], int, int]:
    init_db()
    with SessionLocal() as session:
        return evaluate_session(session, k=k, max_users=max_users)


if __name__ == "__main__":
    from book_recsys.seed import ensure_seed  # noqa: PLC0415

    ensure_seed()
    for row in run_evaluation(10, max_users=120)[0]:
        print(row)
