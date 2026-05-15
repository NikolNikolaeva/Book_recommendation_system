"""Конфигурация като при реална услуга: .env, режим development/production, пътища."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _truthy(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


ENV = os.environ.get("ENV", "development").strip().lower()
DEBUG = _truthy("DEBUG", "1" if ENV != "production" else "0")

# Сесии: в production задължителен уникален SECRET
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip() or (
    "dev-recsys-change-me" if DEBUG else ""
)
SESSION_SECURE_COOKIES = _truthy("SESSION_SECURE_COOKIES", "1" if ENV == "production" else "0")

# Демо каталог + потребители при празна БД (изключи в реален deploy)
SEED_DEMO_DATA = _truthy("SEED_DEMO_DATA", "1")

_data_dir = os.environ.get("DATA_DIR", "").strip()
DATA_DIR = Path(_data_dir) if _data_dir else (BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

_db_url = os.environ.get("DATABASE_URL", "").strip()
if _db_url:
    DATABASE_URL = _db_url
else:
    DATABASE_URL = f"sqlite:///{DATA_DIR / 'recsys.db'}"

_raw_hosts = os.environ.get("TRUSTED_HOSTS", "").strip()
TRUSTED_HOSTS: list[str] = [h.strip() for h in _raw_hosts.split(",") if h.strip()]
# при празно — без restrictive middleware (удобно за локална разработка)

_cors = os.environ.get("CORS_ORIGINS", "").strip()
CORS_ORIGINS: list[str] = [o.strip() for o in _cors.split(",") if o.strip()]

# Hybrid weights (used when user has enough history); cold-start overrides in hybrid.py
WEIGHT_CF = 0.45
WEIGHT_CBF = 0.35
WEIGHT_SOCIAL = 0.20

COLD_START_MAX_INTERACTIONS = 6
CF_SVD_COMPONENTS = 12
MMR_DEFAULT_LAMBDA = 0.7

# Разширяване на каталога през API: ако е зададено, заглавката X-Catalog-Token трябва да съвпада.
CATALOG_ADMIN_TOKEN = os.environ.get("CATALOG_ADMIN_TOKEN", "").strip()

# Опционално: обогатяване на тегове (не се изисква за основния поток).
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()


def validate_production_config() -> None:
    if ENV == "production":
        if not SESSION_SECRET or SESSION_SECRET == "dev-recsys-change-me":
            msg = "ENV=production: задай силен SESSION_SECRET в .env или средата."
            raise RuntimeError(msg)
        if not SESSION_SECURE_COOKIES:
            import warnings

            warnings.warn(
                "SESSION_SECURE_COOKIES=0 в production: cookie без Secure е риск зад HTTPS.",
                stacklevel=2,
            )
