"""Collaborative filtering via truncated SVD on a user–item matrix."""

from __future__ import annotations

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD

from book_recsys.config import CF_SVD_COMPONENTS


def interaction_weight(event_type: str, rating: float | None) -> float:
    if event_type == "dislike":
        return 0.0
    if event_type == "like":
        return 4.0
    if event_type == "to_read":
        return 3.0
    if event_type == "finished":
        return 4.5
    if event_type == "rating" and rating is not None:
        return float(rating)
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
        self._user_means: np.ndarray | None = None
        self._train_global_mean: float = 0.0

    def fit(self, R: csr_matrix) -> None:
        """R: users x items sparse matrix."""
        if R.nnz == 0:
            self._svd = None
            self._user_factors = None
            self._item_factors = None
            self._user_means = None
            self._train_global_mean = 0.0
            return

        dense = R.toarray()
        self._user_means = dense.mean(axis=1, keepdims=True)
        centered = dense - self._user_means
        self._train_global_mean = float(dense.mean())

        n_users, n_items = centered.shape
        n_comp = min(CF_SVD_COMPONENTS, n_users - 1, n_items - 1)
        if n_comp < 1:
            self._svd = None
            self._user_factors = None
            self._item_factors = None
            return

        self._svd = TruncatedSVD(n_components=n_comp, random_state=42)
        self._user_factors = self._svd.fit_transform(centered)
        self._item_factors = self._svd.components_.T

    def predict_scores_for_user(self, user_row_index: int, n_items: int) -> np.ndarray:
        if (
            self._svd is None
            or self._user_factors is None
            or self._item_factors is None
            or self._user_means is None
        ):
            return np.zeros(n_items, dtype=np.float64)
        if user_row_index < 0 or user_row_index >= self._user_factors.shape[0]:
            return np.zeros(n_items, dtype=np.float64)
        u = self._user_factors[user_row_index]
        pred_centered = u @ self._item_factors.T
        pred = pred_centered + float(self._user_means[user_row_index, 0])
        pred = np.clip(pred, 0.0, 5.0)
        m = float(pred.max())
        if m > 1e-12:
            pred = pred / m
        return pred.astype(np.float64)
