"""Content-based filtering: TF-IDF over book text + user profile vector."""

from __future__ import annotations

import json
import re

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from book_recsys.orm import Book, Interaction, User


def _book_document(book: Book) -> str:
    parts = [book.title, book.authors, book.description, book.tags.replace(",", " ")]
    return " \n ".join(parts)


def build_cbf_matrix(books: list[Book]) -> tuple[TfidfVectorizer, np.ndarray]:
    docs = [_book_document(b) for b in books]
    vectorizer = TfidfVectorizer(max_features=6000, min_df=1, ngram_range=(1, 2))
    mat = vectorizer.fit_transform(docs)
    return vectorizer, mat


def user_profile_vector(
    book_index: dict[int, int],
    tfidf_mat,
    interactions: list[Interaction],
) -> np.ndarray | None:
    """Weighted average of TF-IDF rows from positive/negative behavioral signals."""
    if tfidf_mat.shape[0] == 0:
        return None

    vec = np.zeros((1, tfidf_mat.shape[1]), dtype=np.float64)
    weight_sum = 0.0

    for it in interactions:
        idx = book_index.get(it.book_id)
        if idx is None:
            continue
        row = tfidf_mat.getrow(idx)
        w = 0.0
        if it.event_type == "dislike":
            w = -1.2
        elif it.event_type == "like":
            w = 1.5
        elif it.event_type == "to_read":
            w = 0.9
        elif it.event_type == "finished":
            w = 1.2
        elif it.event_type == "rating" and it.rating is not None:
            w = (float(it.rating) - 3.0) / 2.0
        if w == 0.0:
            continue
        vec += w * row.toarray()
        weight_sum += abs(w)

    if weight_sum < 1e-9:
        return None

    vec /= weight_sum
    n = np.linalg.norm(vec)
    if n > 1e-12:
        vec /= n
    return vec


def onboarding_genre_scores(user: User, books: list[Book]) -> np.ndarray:
    """Soft prior from onboarding genre selections (normalized to [0,1])."""
    n = len(books)
    out = np.zeros(n, dtype=np.float64)
    if not user.onboarding_genres:
        return out
    prefs = {g.strip().lower() for g in user.onboarding_genres.split(",") if g.strip()}
    if not prefs:
        return out
    for i, b in enumerate(books):
        btxt = (b.tags + " " + b.title + " " + b.description).lower()
        hits = sum(1 for g in prefs if g in btxt)
        out[i] = hits / max(len(prefs), 1)
    m = float(out.max())
    if m > 1e-12:
        out = out / m
    return out


def onboarding_author_scores(user: User, books: list[Book]) -> np.ndarray:
    """Cold start: любими автори от въпросника — съвпадение в полето authors."""
    n = len(books)
    out = np.zeros(n, dtype=np.float64)
    if not user.onboarding_authors:
        return out
    prefs = [a.strip().lower() for a in user.onboarding_authors.split(",") if a.strip()]
    if not prefs:
        return out
    for i, b in enumerate(books):
        al = b.authors.lower()
        hits = sum(1 for p in prefs if len(p) >= 2 and p in al)
        out[i] = hits / max(len(prefs), 1)
    m = float(out.max())
    if m > 1e-12:
        out = out / m
    return out


def survey_preference_scores(user: User, books: list[Book]) -> np.ndarray:
    """Сигнал от кратката анкета при регистрация (жанрови думи + темпо на четене)."""
    n = len(books)
    out = np.zeros(n, dtype=np.float64)
    if not user.profile_survey_json:
        return out
    try:
        data = json.loads(user.profile_survey_json)
    except json.JSONDecodeError:
        return out
    prefs: set[str] = set()
    for k in ("moods", "formats"):
        for x in data.get(k) or []:
            if isinstance(x, str) and x.strip():
                prefs.add(x.strip().lower())
    note = str(data.get("note") or "")
    for tok in re.findall(r"[\w\u0400-\u04FF]{3,}", note.lower()):
        prefs.add(tok)
    pace = str(data.get("reading_pace") or "").lower()
    if pace == "light":
        prefs.update({"story", "tale", "lyrical", "humor", "essay"})
    elif pace == "avid":
        prefs.update({"epic", "saga", "history", "series", "biography"})
    if not prefs:
        return out
    for i, b in enumerate(books):
        btxt = (b.tags + " " + b.title + " " + b.description + " " + b.authors).lower()
        hits = sum(1 for p in prefs if len(p) >= 2 and p in btxt)
        out[i] = hits / max(len(prefs), 1)
    m = float(out.max())
    if m > 1e-12:
        out = out / m
    return out


def survey_explain_fragment(user: User, book: Book) -> str | None:
    if not user.profile_survey_json:
        return None
    try:
        data = json.loads(user.profile_survey_json)
    except json.JSONDecodeError:
        return None
    prefs: list[str] = []
    for k in ("moods", "formats"):
        for x in data.get(k) or []:
            if isinstance(x, str) and x.strip():
                prefs.append(x.strip().lower())
    note = str(data.get("note") or "").lower()
    prefs.extend(w for w in re.findall(r"[\w\u0400-\u04FF]{3,}", note) if len(w) >= 3)
    if not prefs:
        return None
    btxt = (book.tags + book.title + book.description + book.authors).lower()
    hits = [p for p in prefs if len(p) >= 2 and p in btxt][:4]
    if not hits:
        return None
    return "анкета: " + ", ".join(hits)


def cbf_scores_from_profile(user_vec: np.ndarray | None, tfidf_mat) -> np.ndarray:
    if user_vec is None:
        return np.zeros(tfidf_mat.shape[0], dtype=np.float64)
    sims = cosine_similarity(user_vec, tfidf_mat).ravel()
    sims = np.maximum(sims, 0.0)
    m = float(sims.max())
    if m > 1e-12:
        sims = sims / m
    return sims.astype(np.float64)


def combined_cbf_scores(
    user: User,
    books: list[Book],
    book_index: dict[int, int],
    tfidf_mat,
    interactions: list[Interaction],
    onboarding_weight: float = 0.55,
) -> np.ndarray:
    """Blend history similarity with onboarding (жанр + автори)."""
    prof = user_profile_vector(book_index, tfidf_mat, interactions)
    hist = cbf_scores_from_profile(prof, tfidf_mat)
    onb_g = onboarding_genre_scores(user, books)
    onb_a = onboarding_author_scores(user, books)
    onb_survey = survey_preference_scores(user, books)
    onb = onb_g + onb_a
    if float(onb_survey.sum()) > 1e-12:
        onb = np.clip(onb + 0.5 * onb_survey, 0.0, 1.0)
    m = float(onb.max())
    if m > 1e-12:
        onb = onb / m
    if float(onb.sum()) < 1e-12 and prof is None:
        return hist
    if prof is None:
        return onb
    return np.clip(
        (1.0 - onboarding_weight) * hist + onboarding_weight * onb,
        0.0,
        1.0,
    )
