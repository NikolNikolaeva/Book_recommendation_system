"""Grid search за hybrid тегла и MMR λ. Записва data/tuned_hybrid.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from book_recsys.database import SessionLocal, init_db
from book_recsys.tuning import load_tuning, save_tuning, set_active_tuning
from book_recsys.tuning_search import grid_search


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Hyperparameter tuning за hybrid модела.")
    p.add_argument("--k", type=int, default=10)
    args = p.parse_args()

    init_db()
    with SessionLocal() as session:
        best, meta = grid_search(session, k=args.k)

    path = save_tuning(best)
    set_active_tuning(best)
    load_tuning(path)

    out = ROOT / "docs/reports/tuning_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tuning": best.__dict__, "meta": meta, "path": str(path)}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"tuning saved: {path}")
    print(f"baseline NDCG combined: {meta['baseline_combined_ndcg']:.4f}")
    print(f"best NDCG combined:     {meta['best_combined_ndcg']:.4f}")
    print(f"improvement:            {meta['improvement']:.4f}")
    print(f"weights: CF={best.weight_cf} CBF={best.weight_cbf} social={best.weight_social}")
    print(f"cold:    CF={best.cold_weight_cf} CBF={best.cold_weight_cbf} social={best.cold_weight_social}")
    print(f"MMR λ={best.mmr_lambda} blend={best.social_blend_hybrid}/{best.social_blend_social}")


if __name__ == "__main__":
    main()
