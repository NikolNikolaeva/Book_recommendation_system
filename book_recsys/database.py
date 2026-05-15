from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from book_recsys.config import DATA_DIR, DATABASE_URL
from book_recsys.orm import Base

DATA_DIR.mkdir(parents=True, exist_ok=True)

_engine_kwargs: dict = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _sqlite_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {str(r[1]) for r in rows}


def migrate_sqlite() -> None:
    """Добавя липсващи колони при стари БД (SQLite)."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.begin() as conn:
        ucols = _sqlite_columns(conn, "users")
        if "password_hash" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(256)"))
        if "email" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(256)"))
        if "onboarding_authors" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN onboarding_authors TEXT"))
        if "onboarding_completed" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN onboarding_completed BOOLEAN DEFAULT 0"))
        if "profile_survey_json" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN profile_survey_json TEXT"))

        conn.execute(
            text(
                "UPDATE users SET onboarding_completed = 0 WHERE onboarding_completed IS NULL"
            )
        )
        bcols = _sqlite_columns(conn, "books")
        if "isbn" not in bcols:
            conn.execute(text("ALTER TABLE books ADD COLUMN isbn VARCHAR(32)"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    migrate_sqlite()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def check_db_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
