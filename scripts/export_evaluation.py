"""Експорт на offline метрики и графики за доклада.

Употреба:
  python scripts/export_evaluation.py --tag v0
  python scripts/export_evaluation.py --tag v2 --k 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from book_recsys.database import SessionLocal, init_db
from book_recsys.data_io import count_books
from book_recsys.evaluation import build_evaluation_report
from book_recsys.orm import Friendship, Interaction, User
from sqlalchemy import func, select

FIGURES_DIR = ROOT / "docs" / "figures"
REPORTS_DIR = ROOT / "docs" / "reports"

# Технически имена на методи — на английски в графиките
METHOD_LABELS_BG = {
    "Popular": "Popular",
    "CBF-only": "CBF-only",
    "CF-only": "CF-only",
    "Hybrid": "Hybrid",
    "Hybrid+Social": "Hybrid+Social",
}


def _db_stats(session) -> dict:
    return {
        "books": count_books(session),
        "users": int(session.scalar(select(func.count()).select_from(User)) or 0),
        "interactions": int(session.scalar(select(func.count()).select_from(Interaction)) or 0),
        "friendships": int(session.scalar(select(func.count()).select_from(Friendship)) or 0),
    }


def overall_score(p: float, r: float, n: float) -> float:
    return round((p + r + n) / 3.0, 4)


def export_metrics(tag: str, k: int) -> dict:
    init_db()
    with SessionLocal() as session:
        report = build_evaluation_report(session, k=k)
        stats = _db_stats(session)

    rows = []
    for row in report.rows:
        rows.append(
            {
                "method": row.method,
                "method_bg": METHOD_LABELS_BG.get(row.method, row.method),
                "precision_at_k": row.precision_at_k,
                "recall_at_k": row.recall_at_k,
                "ndcg_at_k": row.ndcg_at_k,
                "overall": overall_score(row.precision_at_k, row.recall_at_k, row.ndcg_at_k),
            }
        )

    payload = {
        "tag": tag,
        "k": k,
        "users_in_fold": report.users_in_fold,
        "stats": stats,
        "rows": rows,
        "note": report.note,
    }
    return payload


def _style_axes(ax, title: str, ylabel: str) -> None:
    ax.set_title(title, fontsize=12, pad=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.tick_params(axis="x", rotation=18)


def plot_charts(payload: dict, tag: str) -> list[Path]:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    rows = payload["rows"]
    if not rows:
        return []

    methods = [r["method_bg"] for r in rows]
    precision = [r["precision_at_k"] for r in rows]
    recall = [r["recall_at_k"] for r in rows]
    ndcg = [r["ndcg_at_k"] for r in rows]
    overall = [r["overall"] for r in rows]

    colors = ["#4C78A8", "#F58518", "#E45756", "#72B7B2", "#54A24B"][: len(methods)]
    paths: list[Path] = []

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(methods, precision, color=colors)
    _style_axes(ax, f"Precision@{payload['k']} по методи", "Precision")
    fig.tight_layout()
    p1 = FIGURES_DIR / f"{tag}_precision_at_k.png"
    fig.savefig(p1, dpi=150)
    plt.close(fig)
    paths.append(p1)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(methods, ndcg, color=colors)
    _style_axes(ax, f"NDCG@{payload['k']} по методи", "NDCG")
    fig.tight_layout()
    p2 = FIGURES_DIR / f"{tag}_ndcg_at_k.png"
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    paths.append(p2)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(methods, overall, color=colors)
    _style_axes(ax, f"Общ резултат (средно P/R/NDCG @ {payload['k']})", "Общ индекс")
    fig.tight_layout()
    p3 = FIGURES_DIR / f"{tag}_overall_score.png"
    fig.savefig(p3, dpi=150)
    plt.close(fig)
    paths.append(p3)

    # Radar chart за общ визуален ефект
    if len(rows) >= 3:
        labels = ["Precision", "Recall", "NDCG"]
        angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
        angles += angles[:1]
        fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
        for i, row in enumerate(rows):
            vals = [row["precision_at_k"], row["recall_at_k"], row["ndcg_at_k"]]
            vals += vals[:1]
            ax.plot(angles, vals, "o-", linewidth=1.5, label=row["method_bg"], color=colors[i])
            ax.fill(angles, vals, alpha=0.08, color=colors[i])
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels)
        ax.set_title(f"Сравнение на методи @ K={payload['k']}", pad=20)
        ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1), fontsize=8)
        fig.tight_layout()
        p4 = FIGURES_DIR / f"{tag}_radar.png"
        fig.savefig(p4, dpi=150, bbox_inches="tight")
        plt.close(fig)
        paths.append(p4)

    # Групирана диаграма P/R/NDCG
    x = np.arange(len(methods))
    w = 0.25
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(x - w, precision, w, label="Precision", color="#4C78A8")
    ax.bar(x, recall, w, label="Recall", color="#F58518")
    ax.bar(x + w, ndcg, w, label="NDCG", color="#54A24B")
    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=18)
    ax.set_title(f"Метрики @ K={payload['k']}")
    ax.legend()
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    fig.tight_layout()
    p5 = FIGURES_DIR / f"{tag}_metrics_grouped.png"
    fig.savefig(p5, dpi=150)
    plt.close(fig)
    paths.append(p5)

    return paths


def plot_evolution(versions: list[dict], method: str = "Hybrid+Social") -> Path | None:
    """Линейна/стълбова еволюция на общ индекс O за избран метод."""
    if len(versions) < 2:
        return None
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    tags, scores, users, books = [], [], [], []
    for v in versions:
        row = next((r for r in v.get("rows", []) if r["method"] == method), None)
        if row is None:
            continue
        tags.append(v["tag"])
        scores.append(row["overall"])
        users.append(v["stats"].get("users", 0))
        books.append(v["stats"].get("books", 0))
    if len(tags) < 2:
        return None
    fig, ax1 = plt.subplots(figsize=(10, 5))
    x = np.arange(len(tags))
    bars = ax1.bar(x, scores, color="#4C78A8", width=0.55)
    ax1.set_xticks(x)
    ax1.set_xticklabels(tags)
    ax1.set_ylabel("Общ индекс O")
    ax1.set_title(f"Еволюция: {method} (O = средно P/R/NDCG)")
    ax1.grid(axis="y", alpha=0.3, linestyle="--")
    for bar, sc in zip(bars, scores):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.002, f"{sc:.3f}", ha="center", fontsize=8)
    ax2 = ax1.twinx()
    ax2.plot(x, users, "o--", color="#E45756", label="потребители")
    ax2.plot(x, books, "s--", color="#54A24B", label="книги")
    ax2.set_ylabel("Брой")
    ax2.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    safe = method.replace("+", "_").replace(" ", "_").lower()
    path = FIGURES_DIR / f"evolution_{safe}.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_all_methods_evolution(versions: list[dict]) -> Path | None:
    """Групирани стълбове: всички методи за финалната версия vs база."""
    if not versions:
        return None
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    baseline = versions[0]
    final = versions[-1]
    if not baseline.get("rows") or not final.get("rows"):
        return None
    methods = [r["method"] for r in baseline["rows"]]
    x = np.arange(len(methods))
    w = 0.8 / len(versions)
    fig, ax = plt.subplots(figsize=(11, 5))
    colors = ["#A0A0A0", "#72B7B2", "#4C78A8", "#E45756"]
    for i, v in enumerate(versions):
        scores = []
        for m in methods:
            row = next((r for r in v["rows"] if r["method"] == m), None)
            scores.append(row["overall"] if row else 0)
        offset = (i - len(versions) / 2 + 0.5) * w
        ax.bar(x + offset, scores, w, label=v["tag"], color=colors[i % len(colors)])
    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=15)
    ax.set_ylabel("Общ индекс O")
    ax.set_title("Еволюция на методите по версии на данните")
    ax.legend()
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    fig.tight_layout()
    path = FIGURES_DIR / "evolution_all_methods.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_comparison(baseline: dict, improved: dict, out_tag: str = "comparison") -> Path | None:
    if not baseline.get("rows") or not improved.get("rows"):
        return None
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    methods = [r["method_bg"] for r in baseline["rows"]]
    b_scores = [r["overall"] for r in baseline["rows"]]
    i_scores = [r["overall"] for r in improved["rows"]]
    x = np.arange(len(methods))
    w = 0.35
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(x - w / 2, b_scores, w, label=baseline["tag"], color="#A0A0A0")
    ax.bar(x + w / 2, i_scores, w, label=improved["tag"], color="#4C78A8")
    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=18)
    ax.set_ylabel("Общ индекс")
    ax.set_title("Общ резултат: преди и след подобрения")
    ax.legend()
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    fig.tight_layout()
    path = FIGURES_DIR / f"{out_tag}_baseline_vs_final.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="v0", help="Етикет на версията (v0, v1, v2)")
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()

    payload = export_metrics(args.tag, args.k)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"{args.tag}_metrics.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    paths = plot_charts(payload, args.tag)
    print(f"tag={args.tag} k={args.k} users_in_fold={payload['users_in_fold']}")
    print(f"stats={payload['stats']}")
    for row in payload["rows"]:
        print(
            f"  {row['method']}: P={row['precision_at_k']} R={row['recall_at_k']} "
            f"NDCG={row['ndcg_at_k']} O={row['overall']}"
        )
    print(f"json={json_path}")
    for p in paths:
        print(f"figure={p}")


if __name__ == "__main__":
    main()
