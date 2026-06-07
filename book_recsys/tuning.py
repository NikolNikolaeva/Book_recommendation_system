"""Hybrid hyperparameters — load/save tuned values."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from book_recsys.config import DATA_DIR

TUNING_PATH = DATA_DIR / "tuned_hybrid.json"


@dataclass
class HybridTuning:
    """Tunable hybrid + MMR parameters (defaults match original config)."""

    weight_cf: float = 0.45
    weight_cbf: float = 0.35
    weight_social: float = 0.20
    cold_weight_cf: float = 0.15
    cold_weight_cbf: float = 0.55
    cold_weight_social: float = 0.15
    social_boost_factor: float = 1.2
    social_boost_cap: float = 0.32
    social_blend_hybrid: float = 0.55
    social_blend_social: float = 0.45
    mmr_lambda: float = 0.7

    def validate(self) -> None:
        for name in ("weight_cf", "weight_cbf", "weight_social"):
            v = getattr(self, name)
            if not 0.0 <= v <= 1.0:
                msg = f"{name} out of range: {v}"
                raise ValueError(msg)
        if not 0.5 <= self.mmr_lambda <= 1.0:
            raise ValueError(f"mmr_lambda out of range: {self.mmr_lambda}")


_CACHE: HybridTuning | None = None


def default_tuning() -> HybridTuning:
    return HybridTuning()


def load_tuning(path: Path | None = None) -> HybridTuning:
    global _CACHE
    p = path or TUNING_PATH
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        t = HybridTuning(**{k: data[k] for k in asdict(HybridTuning()) if k in data})
        t.validate()
        _CACHE = t
        return t
    _CACHE = default_tuning()
    return _CACHE


def save_tuning(tuning: HybridTuning, path: Path | None = None) -> Path:
    global _CACHE
    tuning.validate()
    p = path or TUNING_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(tuning), indent=2), encoding="utf-8")
    _CACHE = tuning
    return p


def get_tuning() -> HybridTuning:
    global _CACHE
    if _CACHE is None:
        return load_tuning()
    return _CACHE


def set_active_tuning(tuning: HybridTuning) -> None:
    global _CACHE
    tuning.validate()
    _CACHE = tuning
