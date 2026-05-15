"""Offline evaluation: leave-last-out, Precision/Recall/NDCG @ K."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from book_recsys.database import SessionLocal, init_db
from book_recsys.orm import Book, Interaction, User
from book_recsys.recommender.cf_model import CFModel, build_user_item_matrix, interaction_weight
from book_recsys.recommender.hybrid import HybridContext, build_context, hybrid_scores
from book_recsys.recommender.social import social_scores_vector
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
    "всички книги. Отчитат се средни Precision@K, Recall@K и NDCG@K по потребители в този fold."
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


def _train_cf_without_interactions(
    session: Session,
    skip_ids: set[int],
) -> tuple[CFModel, dict[int, int], dict[int, int], int, int]:
    users = list(session.scalars(select(User).order_by(User.id)))
    books = list(session.scalars(select(Book).order_by(Book.id)))
    internal_user_row = {u.id: i for i, u in enumerate(users)}
    book_index = {b.id: i for i, b in enumerate(books)}
    n_users, n_items = len(users), len(books)
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for it in session.scalars(select(Interaction)):
        if it.id in skip_ids:
            continue
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
    R = build_user_item_matrix(n_users, n_items, rows, cols, vals)
    cf = CFModel()
    cf.fit(R)
    return cf, internal_user_row, book_index, n_users, n_items


def _popular_ranking(session: Session, book_index: dict[int, int], n_items: int) -> np.ndarray:
    scores = np.zeros(n_items, dtype=np.float64)
    for it in session.scalars(select(Interaction)):
        bi = book_index.get(it.book_id)
        if bi is None:
            continue
        if it.event_type in ("like", "finished") or (
            it.event_type == "rating" and it.rating is not None and it.rating >= 4.0
        ):
            scores[bi] += 1.0
    return np.argsort(-scores)


def evaluate_session(session: Session, k: int = 10) -> tuple[list[EvalResult], int]:
    """Връща (методи с усреднени метрики, брой потребители във fold-а)."""
    users = list(session.scalars(select(User)))
    if not users:
        return [], 0

    # group interactions by user, sort by time, hold out last relevant event per user
    tests: dict[int, tuple[int, int]] = {}  # user_id -> (held book id, held interaction id)
    skip_interaction_ids: set[int] = set()

    for u in users:
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
        skip_interaction_ids.add(last_it.id)

    if not tests:
        return [], 0

    books = list(session.scalars(select(Book).order_by(Book.id)))
    book_index = {b.id: i for i, b in enumerate(books)}
    n_items = len(books)

    cf_model, internal_user_row, _, _, _ = _train_cf_without_interactions(session, skip_interaction_ids)
    full_ctx = build_context(session)

    results: dict[str, list[tuple[float, float, float]]] = {
        "Popular": [],
        "CBF-only": [],
        "CF-only": [],
        "Hybrid": [],
        "Hybrid+Social": [],
    }

    for uid, (held_book, held_iid) in tests.items():
        user = session.get(User, uid)
        if user is None:
            continue
        positive = {held_book}

        pop_order = _popular_ranking(session, book_index, n_items)
        ranked_ids = [books[i].id for i in pop_order.tolist()]
        results["Popular"].append(_precision_recall_ndcg(ranked_ids, positive, k))

        # CBF-only: use full_ctx hybrid with CF weight 0 manually
        from book_recsys.recommender.cbf import build_cbf_matrix, combined_cbf_scores  # noqa: PLC0415

        _, tfidf_sp = build_cbf_matrix(books)
        interactions_train = [
            it
            for it in session.scalars(select(Interaction).where(Interaction.user_id == uid))
            if it.id != held_iid
        ]
        cbf = combined_cbf_scores(user, books, book_index, tfidf_sp, interactions_train)
        order = np.argsort(-cbf)
        ranked_ids = [books[i].id for i in order.tolist()]
        results["CBF-only"].append(_precision_recall_ndcg(ranked_ids, positive, k))

        # CF-only
        iu = internal_user_row.get(uid, -1)
        cf_scores = cf_model.predict_scores_for_user(iu, n_items)
        order = np.argsort(-cf_scores)
        ranked_ids = [books[i].id for i in order.tolist()]
        results["CF-only"].append(_precision_recall_ndcg(ranked_ids, positive, k))

        # Hybrid: patch ctx with reduced CF
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
        scores_h, _, _, _ = hybrid_scores(session, ctx2, user, omit_interaction_ids={held_iid})
        order = np.argsort(-scores_h)
        ranked_ids = [books[i].id for i in order.tolist()]
        results["Hybrid"].append(_precision_recall_ndcg(ranked_ids, positive, k))

        soc = social_scores_vector(session, uid, book_index, n_items)
        comb = 0.55 * scores_h + 0.45 * soc if float(soc.max()) > 0 else scores_h
        order = np.argsort(-comb)
        ranked_ids = [books[i].id for i in order.tolist()]
        results["Hybrid+Social"].append(_precision_recall_ndcg(ranked_ids, positive, k))

    out: list[EvalResult] = []
    for name, rows in results.items():
        if not rows:
            continue
        p = float(np.mean([x[0] for x in rows]))
        r = float(np.mean([x[1] for x in rows]))
        n = float(np.mean([x[2] for x in rows]))
        out.append(EvalResult(name, p, r, n))
    return out, len(tests)


def build_evaluation_report(session: Session, k: int) -> EvaluationReport:
    rows_eval, n_fold = evaluate_session(session, k=k)
    rows = [
        EvaluationRow(
            method=r.method,
            precision_at_k=round(r.precision_at_k, 4),
            recall_at_k=round(r.recall_at_k, 4),
            ndcg_at_k=round(r.ndcg_at_k, 4),
        )
        for r in rows_eval
    ]
    note = (
        "Leave-last-out по последен положителен сигнал (like/rating≥4/finished) за всеки потребител; "
        "CF се тренира без отделените взаимодействия; CBF/hybrid за потребителя без неговия отделен ред."
    )
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


def run_evaluation(k: int = 10) -> tuple[list[EvalResult], int]:
    init_db()
    with SessionLocal() as session:
        return evaluate_session(session, k=k)


if __name__ == "__main__":
    from book_recsys.seed import ensure_seed  # noqa: PLC0415

    ensure_seed()
    for row in run_evaluation(10)[0]:
        print(row)
