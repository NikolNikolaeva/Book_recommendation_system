"""Collaborative filtering via truncated SVD on a user–item matrix."""

from __future__ import annotations

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD

from book_recsys.config import CF_SVD_COMPONENTS


def interaction_weight(event_type: str, rating: float | None) -> float:
    """
    Тегло за CF (implicit Top‑N).

    Важно: ниски оценки НЕ са положителен сигнал — за Top‑N третираме като 0.
    """
    if event_type == "dislike":
        return 0.0
    if event_type == "like":
        return 1.0
    if event_type == "finished":
        return 1.0
    if event_type == "to_read":
        return 0.7
    if event_type == "rating" and rating is not None:
        r = float(rating)
        if r >= 4.0:
            return 1.0 + 0.2 * (r - 4.0)  # 4★→1.0, 5★→1.2
        return 0.0
    return 0.0


def build_user_item_matrix(
    n_users: int,
    n_items: int,
    user_ids: list[int],
    item_indices: list[int],
    weights: list[float],
) -> csr_matrix:
    rows = np.array(user_ids, dtype=np.int32)
    cols = np.array(item_indices, dtype=np.int32)
    data = np.array(weights, dtype=np.float64)
    return csr_matrix((data, (rows, cols)), shape=(n_users, n_items))


class CFModel:
    def __init__(self) -> None:
        self._svd: TruncatedSVD | None = None
        self._user_factors: np.ndarray | None = None
        self._item_factors: np.ndarray | None = None

    def fit(self, R: csr_matrix) -> None:
        """R: users x items sparse matrix."""
        if R.nnz == 0:
            self._svd = None
            self._user_factors = None
            self._item_factors = None
            return

        n_users, n_items = R.shape
        if n_users < 2 or n_items < 2:
            self._svd = None
            self._user_factors = None
            self._item_factors = None
            return

        # TruncatedSVD работи директно със sparse матрица (implicit матрица от положителни сигнали).
        mat = R
        n_comp = min(CF_SVD_COMPONENTS, n_users - 1, n_items - 1)
        if n_comp < 1:
            self._svd = None
            self._user_factors = None
            self._item_factors = None
            return

        self._svd = TruncatedSVD(n_components=n_comp, random_state=42)
        self._user_factors = self._svd.fit_transform(mat)
        self._item_factors = self._svd.components_.T

    def predict_scores_for_user(self, user_row_index: int, n_items: int) -> np.ndarray:
        if (
            self._svd is None
            or self._user_factors is None
            or self._item_factors is None
        ):
            return np.zeros(n_items, dtype=np.float64)
        if user_row_index < 0 or user_row_index >= self._user_factors.shape[0]:
            return np.zeros(n_items, dtype=np.float64)
        u = self._user_factors[user_row_index]
        pred = u @ self._item_factors.T
        pred = np.maximum(pred, 0.0)
        m = float(pred.max())
        if m > 1e-12:
            pred = pred / m
        return pred.astype(np.float64)
