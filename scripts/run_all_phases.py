"""Изпълнява всички фази от плана за документация."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from book_recsys.seed import ensure_seed
from book_recsys.synthetic_import import expand_synthetic_wave2, generate_and_import
from scripts.export_evaluation import (
    export_metrics,
    plot_all_methods_evolution,
    plot_comparison,
    plot_evolution,
)
from scripts.phase_b_improvements import run_phase_b


def _run(cmd: list[str]) -> None:
    import os

    print(f"\n>>> {' '.join(cmd)}")
    env = os.environ.copy()
    env.setdefault("MPLCONFIGDIR", str(ROOT / ".mplconfig"))
    subprocess.run(cmd, cwd=ROOT, check=True, env=env)


def main() -> None:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--skip-ol", action="store_true", help="Без Open Library (offline)")
    p.add_argument("--reset", action="store_true", help="Пълен ресийд преди фазите")
    args = p.parse_args()

    py = sys.executable

    if args.reset:
        ensure_seed(force=True)
    else:
        ensure_seed()

    print("=== Фаза A: базова оценка (v0) ===")
    v0 = export_metrics("v0", 10)
    _run([py, "scripts/export_evaluation.py", "--tag", "v0"])

    print("\n=== Фаза B: подобрения ===")
    b_result = run_phase_b(skip_ol=args.skip_ol)
    (ROOT / "docs/reports/phase_b.json").write_text(
        json.dumps(b_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(b_result)
    v1 = export_metrics("v1", 10)
    _run([py, "scripts/export_evaluation.py", "--tag", "v1"])

    print("\n=== Експеримент 1a: синтетични данни (вълна 1, подобни на API/демо) ===")
    c1_result = generate_and_import(
        n_users=35,
        min_interactions=14,
        max_interactions=18,
        save_json=ROOT / "docs/reports/synthetic_wave1.json",
    )
    (ROOT / "docs/reports/phase_c_wave1.json").write_text(
        json.dumps(c1_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(c1_result)
    v2 = export_metrics("v2", 10)
    _run([py, "scripts/export_evaluation.py", "--tag", "v2"])

    print("\n=== Експеримент 1b: разширяване на синтетични данни (вълна 2) ===")
    c2_result = expand_synthetic_wave2(
        n_new_users=20,
        save_json=ROOT / "docs/reports/synthetic_wave2.json",
    )
    (ROOT / "docs/reports/phase_c_wave2.json").write_text(
        json.dumps(c2_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(c2_result)
    v3 = export_metrics("v3", 10)
    _run([py, "scripts/export_evaluation.py", "--tag", "v3"])

    print("\n=== Оптимизация 3: hyperparameter tuning (hybrid + MMR) ===")
    _run([py, "scripts/tune_hybrid.py", "--k", "10"])
    from book_recsys.tuning import load_tuning

    load_tuning()
    v4 = export_metrics("v4", 10)
    _run([py, "scripts/export_evaluation.py", "--tag", "v4"])

    versions = [v0, v1, v2, v3, v4]
    for p in (
        plot_comparison(v0, v4, "comparison"),
        plot_evolution(versions, "Hybrid+Social"),
        plot_evolution(versions, "Hybrid"),
        plot_all_methods_evolution(versions),
    ):
        if p:
            print(f"figure={p}")

    print("\n=== Фаза D: PDF ===")
    _run([py, "scripts/build_pdf.py"])

    tuning_meta = {}
    tr = ROOT / "docs/reports/tuning_result.json"
    if tr.is_file():
        tuning_meta = json.loads(tr.read_text(encoding="utf-8"))

    summary = {
        "v0": v0,
        "v1": v1,
        "v2": v2,
        "v3": v3,
        "v4": v4,
        "phase_b": b_result,
        "phase_c_wave1": c1_result,
        "phase_c_wave2": c2_result,
        "tuning": tuning_meta,
        "pdf": str(ROOT / "docs/Доклад_Препоръчваща_система.pdf"),
    }
    out = ROOT / "docs/reports/pipeline_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nГотово. Обобщение: {out}")


if __name__ == "__main__":
    main()
