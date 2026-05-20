"""FastAPI application: production-minded — конфиг, health, сигурност, сесии."""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session, load_only
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from book_recsys import __version__
from book_recsys import config
from book_recsys.auth_pass import hash_password, verify_password
from book_recsys.data_io import count_books
from book_recsys.database import check_db_connection, get_db, init_db
from book_recsys.evaluation import build_evaluation_report
from book_recsys.orm import Book, FriendRequest, Friendship, Interaction, User
from book_recsys.recommender.hybrid import build_context, recommend, similar_books
from book_recsys.recommender.social import friend_explanation_for_book, friend_ids_for
from book_recsys.open_library import fetch_catalog_into_db
from book_recsys.schemas import (
    BookCardOut,
    BookListResponse,
    BookStatsOut,
    BookOut,
    BookSignalsResponse,
    BookSignalOut,
    CatalogFetchIn,
    CatalogFetchOut,
    EvaluationReport,
    FriendAdd,
    FriendRequestOut,
    FriendRequestsResponse,
    FriendOut,
    InteractionCreate,
    InteractionOut,
    LoginIn,
    OnboardingCompleteIn,
    RecommendationItem,
    RegisterIn,
    ServiceMeta,
    UserMe,
)
from book_recsys.seed import ensure_seed

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
log = logging.getLogger("book_recsys")

_USER_RE = re.compile(r"^[a-zA-Z0-9_\u0400-\u04FF]{3,64}$")

_CTX_LOCK = threading.Lock()
_CTX_CACHE = None  # HybridContext
_CTX_BUILT_AT = 0.0
_CTX_DIRTY = True
_CTX_TTL_S = 180.0


def mark_recommender_context_dirty() -> None:
    global _CTX_DIRTY
    _CTX_DIRTY = True


def get_recommender_context(db: Session):
    """
    Heavy recommender context cache.

    - If nothing changed, reuse indefinitely.
    - If marked dirty, rebuild at most once per TTL to avoid huge latency spikes on every click.
    """
    global _CTX_CACHE, _CTX_BUILT_AT, _CTX_DIRTY
    now = time.time()
    if _CTX_CACHE is None:
        with _CTX_LOCK:
            if _CTX_CACHE is None:
                _CTX_CACHE = build_context(db)
                _CTX_BUILT_AT = time.time()
                _CTX_DIRTY = False
        return _CTX_CACHE

    if not _CTX_DIRTY:
        return _CTX_CACHE
    if (now - _CTX_BUILT_AT) < _CTX_TTL_S:
        return _CTX_CACHE
    with _CTX_LOCK:
        now = time.time()
        if _CTX_CACHE is not None and (not _CTX_DIRTY):
            return _CTX_CACHE
        if (now - _CTX_BUILT_AT) < _CTX_TTL_S:
            return _CTX_CACHE
        _CTX_CACHE = build_context(db)
        _CTX_BUILT_AT = time.time()
        _CTX_DIRTY = False
        return _CTX_CACHE


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(
        level=logging.DEBUG if config.DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    config.validate_production_config()
    init_db()
    if config.SEED_DEMO_DATA:
        ensure_seed()
        log.info("Демо данни заредени (SEED_DEMO_DATA=1).")
    else:
        log.info("SEED_DEMO_DATA=0 — без автоматичен сийд; импортирай каталог ръчно.")
    log.info("Услугата стартира env=%s debug=%s", config.ENV, config.DEBUG)
    yield


app = FastAPI(
    title="Book Recommender API",
    version=__version__,
    description="Препоръчваща система за книги — API и уеб UI.",
    lifespan=lifespan,
    docs_url="/api/docs" if config.DEBUG else None,
    redoc_url="/api/redoc" if config.DEBUG else None,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="recsys_session",
    same_site="lax",
    https_only=config.SESSION_SECURE_COOKIES,
)
if config.TRUSTED_HOSTS:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.TRUSTED_HOSTS)
if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if config.ENV == "production":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, StarletteHTTPException):
        return await http_exception_handler(request, exc)
    if isinstance(exc, RequestValidationError):
        return await request_validation_exception_handler(request, exc)
    log.exception("Необработена грешка")
    detail = str(exc) if config.DEBUG else "Вътрешна грешка. Опитайте отново по-късно."
    return JSONResponse(status_code=500, content={"detail": detail})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/api/ready")
def ready() -> dict:
    if not check_db_connection():
        raise HTTPException(status_code=503, detail="Базата данни не отговаря")
    return {"ready": True, "database": True}


@app.get("/api/meta", response_model=ServiceMeta)
def meta(db: Session = Depends(get_db)) -> ServiceMeta:
    return ServiceMeta(
        version=__version__,
        environment=config.ENV,
        debug=config.DEBUG,
        demo_seed_enabled=config.SEED_DEMO_DATA,
        database_ok=check_db_connection(),
        book_count=count_books(db),
        catalog_token_required=bool(config.CATALOG_ADMIN_TOKEN),
        openai_configured=bool(config.OPENAI_API_KEY),
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {"version": __version__, "environment": config.ENV},
    )


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    if uid is None:
        raise HTTPException(status_code=401, detail="Нужен е вход в системата")
    u = db.get(User, int(uid))
    if u is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Невалидна сесия")
    return u


def _user_me(u: User) -> UserMe:
    return UserMe(
        id=u.id,
        username=u.username,
        email=u.email,
        onboarding_completed=bool(u.onboarding_completed),
    )


def _catalog_admin_ok(request: Request) -> None:
    tok = config.CATALOG_ADMIN_TOKEN
    if not tok:
        return
    if request.headers.get("X-Catalog-Token") != tok:
        raise HTTPException(status_code=403, detail="Невалиден или липсващ X-Catalog-Token")


@app.get("/api/auth/me", response_model=UserMe | None)
def auth_me(request: Request, db: Session = Depends(get_db)) -> UserMe | None:
    uid = request.session.get("uid")
    if uid is None:
        return None
    u = db.get(User, int(uid))
    if u is None:
        request.session.clear()
        return None
    return _user_me(u)


@app.post("/api/auth/register", response_model=UserMe)
def auth_register(request: Request, body: RegisterIn, db: Session = Depends(get_db)) -> UserMe:
    if not _USER_RE.match(body.username):
        raise HTTPException(
            status_code=400,
            detail="Потребителско име: 3–64 символа, букви цифри _ (кирилица позволена)",
        )
    if db.scalars(select(User).where(User.username == body.username)).first():
        raise HTTPException(status_code=400, detail="Това потребителско име е заето")
    survey_payload = None
    if body.survey:
        raw_survey = body.survey.model_dump(exclude_none=True)
        if raw_survey:
            survey_payload = json.dumps(raw_survey, ensure_ascii=False)
    u = User(
        username=body.username,
        email=(body.email.strip() or None) if body.email else None,
        password_hash=hash_password(body.password),
        onboarding_completed=False,
        profile_survey_json=survey_payload,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    request.session["uid"] = u.id
    return _user_me(u)


@app.post("/api/auth/login", response_model=UserMe)
def auth_login(request: Request, body: LoginIn, db: Session = Depends(get_db)) -> UserMe:
    u = db.scalars(select(User).where(User.username == body.username)).first()
    if u is None or not u.password_hash:
        raise HTTPException(status_code=401, detail="Грешно потребителско име или парола")
    if not verify_password(body.password, u.password_hash):
        raise HTTPException(status_code=401, detail="Грешно потребителско име или парола")
    request.session["uid"] = u.id
    return _user_me(u)


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.get("/api/onboarding/taste-deck", response_model=list[BookOut])
def taste_deck(
    n: int = 12,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Book]:
    lim = max(4, min(24, n))
    seen = set(db.scalars(select(Interaction.book_id).where(Interaction.user_id == user.id)).all())
    books = list(db.scalars(select(Book)))
    pool = [b for b in books if b.id not in seen]
    random.shuffle(pool)
    return pool[:lim]


@app.post("/api/onboarding/complete")
def onboarding_complete(
    body: OnboardingCompleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not body.genres:
        raise HTTPException(status_code=400, detail="Избери поне един жанр")
    user.onboarding_genres = ",".join(g.strip() for g in body.genres if g.strip())
    user.onboarding_language = (body.language or "both").strip()[:32]
    user.onboarding_authors = ",".join(a.strip() for a in body.authors if a.strip())
    user.onboarding_completed = True
    for qr in body.quick_reactions:
        if db.get(Book, qr.book_id) is None:
            continue
        db.add(Interaction(user_id=user.id, book_id=qr.book_id, event_type=qr.reaction))
    db.commit()
    mark_recommender_context_dirty()
    return {"ok": True}


@app.post("/api/catalog/fetch-openlibrary", response_model=CatalogFetchOut)
def catalog_fetch_openlibrary(
    request: Request,
    body: CatalogFetchIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CatalogFetchOut:
    """Добавя/обновява книги от Open Library (легален публичен API, без ключ)."""
    _catalog_admin_ok(request)
    ins, upd, sk = fetch_catalog_into_db(
        db,
        body.query.strip(),
        limit=body.limit,
        fetch_descriptions=body.fetch_descriptions,
    )
    if ins or upd:
        mark_recommender_context_dirty()
    return CatalogFetchOut(
        inserted=ins,
        updated=upd,
        skipped=sk,
        total_books=count_books(db),
    )


@app.get("/api/popular", response_model=list[BookOut])
def popular(limit: int = 12, db: Session = Depends(get_db)) -> list[Book]:
    lim = max(1, min(50, limit))
    subq = (
        select(Interaction.book_id, func.count(Interaction.id).label("cnt"))
        .where(
            Interaction.event_type.in_(["like", "finished"])
            | ((Interaction.event_type == "rating") & (Interaction.rating >= 4.0))
        )
        .group_by(Interaction.book_id)
        .subquery()
    )
    stmt = select(Book).join(subq, Book.id == subq.c.book_id).order_by(subq.c.cnt.desc()).limit(lim)
    return list(db.scalars(stmt))


@app.get("/api/users", response_model=list[FriendOut])
def list_users(db: Session = Depends(get_db)) -> list[FriendOut]:
    rows = db.scalars(select(User).order_by(User.id)).all()
    return [FriendOut(id=u.id, username=u.username) for u in rows]


@app.get("/api/books", response_model=BookListResponse)
def list_books(
    q: str | None = None,
    limit: int = 80,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> BookListResponse:
    lim = max(1, min(120, limit))
    off = max(0, offset)
    needle = (q or "").strip()

    stmt = select(Book).options(
        load_only(
            Book.id,
            Book.title,
            Book.authors,
            Book.tags,
            Book.language,
            Book.year,
            Book.isbn,
            Book.cover_url,
        )
    )
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(
            Book.title.ilike(like)
            | Book.authors.ilike(like)
            | Book.tags.ilike(like)
        )

    rows = list(db.scalars(stmt.order_by(Book.title).offset(off).limit(lim + 1)).all())
    has_more = len(rows) > lim
    items = rows[:lim]
    return BookListResponse(items=[BookCardOut.model_validate(b) for b in items], has_more=has_more)


@app.get("/api/books/{book_id}", response_model=BookOut)
def get_book(book_id: int, db: Session = Depends(get_db)) -> Book:
    b = db.get(Book, book_id)
    if b is None:
        raise HTTPException(status_code=404, detail="Книгата не е намерена")
    return b


@app.get("/api/books/{book_id}/stats", response_model=BookStatsOut)
def book_stats(book_id: int, db: Session = Depends(get_db)) -> BookStatsOut:
    if db.get(Book, book_id) is None:
        raise HTTPException(status_code=404, detail="Книгата не е намерена")
    stmt = (
        select(
            func.avg(
                case(
                    (
                        (Interaction.event_type == "rating") & (Interaction.rating.is_not(None)),
                        Interaction.rating,
                    ),
                    else_=None,
                )
            ).label("avg_rating"),
            func.sum(
                case(
                    (
                        (Interaction.event_type == "rating") & (Interaction.rating.is_not(None)),
                        1,
                    ),
                    else_=0,
                )
            ).label("rating_count"),
            func.sum(case((Interaction.event_type == "like", 1), else_=0)).label("like_count"),
            func.sum(case((Interaction.event_type == "finished", 1), else_=0)).label("finished_count"),
            func.sum(case((Interaction.event_type == "to_read", 1), else_=0)).label("to_read_count"),
        )
        .where(Interaction.book_id == int(book_id))
    )
    row = db.execute(stmt).one()._mapping
    avg = row["avg_rating"]
    return BookStatsOut(
        avg_rating=(round(float(avg), 2) if avg is not None else None),
        rating_count=int(row["rating_count"] or 0),
        like_count=int(row["like_count"] or 0),
        finished_count=int(row["finished_count"] or 0),
        to_read_count=int(row["to_read_count"] or 0),
    )


@app.post("/api/interactions", response_model=InteractionOut)
def add_interaction(
    body: InteractionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Interaction:
    if db.get(Book, body.book_id) is None:
        raise HTTPException(status_code=404, detail="Книгата не е намерена")
    if body.event_type == "rating" and body.rating is None:
        raise HTTPException(status_code=400, detail="rating е задължителен при event_type=rating")

    now = datetime.utcnow()
    if body.event_type == "rating":
        existing = db.scalars(
            select(Interaction)
            .where(
                Interaction.user_id == user.id,
                Interaction.book_id == body.book_id,
                Interaction.event_type == "rating",
            )
            .order_by(Interaction.created_at.desc(), Interaction.id.desc())
        ).first()
        if existing is None:
            it = Interaction(
                user_id=user.id,
                book_id=body.book_id,
                event_type="rating",
                rating=float(body.rating) if body.rating is not None else None,
                comment=body.comment,
                created_at=now,
            )
            db.add(it)
            db.commit()
            db.refresh(it)
        else:
            existing.rating = float(body.rating) if body.rating is not None else existing.rating
            if body.comment is not None:
                existing.comment = body.comment
            existing.created_at = now
            db.commit()
            it = existing
    else:
        # Quick action = състояние (последен избор). Пазим максимум 1 такъв запис на книга за потребител.
        db.execute(
            delete(Interaction).where(
                Interaction.user_id == user.id,
                Interaction.book_id == body.book_id,
                Interaction.event_type.in_(["like", "dislike", "to_read", "finished"]),
            )
        )
        it = Interaction(
            user_id=user.id,
            book_id=body.book_id,
            event_type=body.event_type,
            rating=None,
            comment=None,
            created_at=now,
        )
        db.add(it)
        db.commit()
        db.refresh(it)

    mark_recommender_context_dirty()
    return it


@app.get("/api/me/book-signals", response_model=BookSignalsResponse)
def book_signals(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> BookSignalsResponse:
    rows = list(
        db.scalars(
            select(Interaction)
            .where(Interaction.user_id == user.id)
            .order_by(Interaction.created_at.desc())
        ).all()
    )
    by_book: dict[int, dict[str, float | str | None]] = {}
    for it in rows:
        bid = it.book_id
        if bid not in by_book:
            by_book[bid] = {"last_rating": None, "last_event": None}
        rec = by_book[bid]
        if rec["last_event"] is None:
            rec["last_event"] = it.event_type
        if it.event_type == "rating" and rec["last_rating"] is None and it.rating is not None:
            rec["last_rating"] = float(it.rating)
    out = {
        str(k): BookSignalOut(last_rating=v["last_rating"], last_event=v["last_event"]) for k, v in by_book.items()
    }
    return BookSignalsResponse(books=out)


@app.post("/api/friends")
def add_friend(
    body: FriendAdd,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Back-compat alias: изпраща покана за приятелство."""
    uname = (body.friend_username or "").strip()
    if not uname:
        raise HTTPException(status_code=400, detail="Липсва потребителско име")
    target = db.scalars(select(User).where(User.username == uname)).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Няма потребител с това име")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Не можеш да добавиш себе си")

    exists = db.scalars(select(Friendship).where(Friendship.user_id == user.id, Friendship.friend_id == target.id)).first()
    if exists:
        return {"ok": True, "already_friends": True}

    incoming = db.scalars(
        select(FriendRequest).where(
            FriendRequest.from_user_id == target.id,
            FriendRequest.to_user_id == user.id,
            FriendRequest.status == "pending",
        )
    ).first()
    if incoming:
        raise HTTPException(status_code=400, detail="Има входяща покана от този потребител — приеми я.")

    prev = db.scalars(
        select(FriendRequest).where(
            FriendRequest.from_user_id == user.id,
            FriendRequest.to_user_id == target.id,
        )
    ).first()
    if prev:
        if prev.status == "pending":
            return {"ok": True, "duplicate": True}
        prev.status = "pending"
        prev.created_at = datetime.utcnow()
        prev.responded_at = None
        db.commit()
        return {"ok": True, "request_id": prev.id, "reopened": True}

    fr = FriendRequest(from_user_id=user.id, to_user_id=target.id, status="pending")
    db.add(fr)
    db.commit()
    db.refresh(fr)
    return {"ok": True, "request_id": fr.id}


@app.get("/api/users/search", response_model=list[FriendOut])
def search_users(
    q: str,
    limit: int = 12,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FriendOut]:
    needle = (q or "").strip()
    if not needle:
        return []
    lim = max(1, min(30, limit))
    like = f"%{needle}%"
    stmt = (
        select(User)
        .where(User.id != user.id, User.username.ilike(like))
        .order_by(User.username)
        .limit(lim)
    )
    rows = db.scalars(stmt).all()
    return [FriendOut(id=u.id, username=u.username) for u in rows]


@app.get("/api/friends", response_model=list[FriendOut])
def list_friends(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[FriendOut]:
    ids = friend_ids_for(db, user.id)
    if not ids:
        return []
    rows = db.scalars(select(User).where(User.id.in_(ids)).order_by(User.username)).all()
    return [FriendOut(id=u.id, username=u.username) for u in rows]


@app.get("/api/friends/requests", response_model=FriendRequestsResponse)
def friend_requests(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FriendRequestsResponse:
    incoming = list(
        db.scalars(
            select(FriendRequest)
            .where(FriendRequest.to_user_id == user.id, FriendRequest.status == "pending")
            .order_by(FriendRequest.created_at.desc())
        ).all()
    )
    outgoing = list(
        db.scalars(
            select(FriendRequest)
            .where(FriendRequest.from_user_id == user.id, FriendRequest.status == "pending")
            .order_by(FriendRequest.created_at.desc())
        ).all()
    )

    ids = {user.id}
    for r in incoming + outgoing:
        ids.add(int(r.from_user_id))
        ids.add(int(r.to_user_id))
    users = {u.id: u.username for u in db.scalars(select(User).where(User.id.in_(ids))).all()}

    def pack(r: FriendRequest) -> FriendRequestOut:
        return FriendRequestOut(
            id=r.id,
            from_user=FriendOut(id=int(r.from_user_id), username=users.get(int(r.from_user_id), f"user_{r.from_user_id}")),
            to_user=FriendOut(id=int(r.to_user_id), username=users.get(int(r.to_user_id), f"user_{r.to_user_id}")),
            status=r.status,
            created_at=r.created_at,
        )

    return FriendRequestsResponse(incoming=[pack(r) for r in incoming], outgoing=[pack(r) for r in outgoing])


@app.post("/api/friends/requests", response_model=dict)
def send_friend_request(
    body: FriendAdd,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # Delegate to /api/friends alias logic.
    return add_friend(body, user=user, db=db)


@app.post("/api/friends/requests/{request_id}/accept")
def accept_friend_request(
    request_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    fr = db.get(FriendRequest, int(request_id))
    if fr is None or fr.status != "pending":
        raise HTTPException(status_code=404, detail="Поканата не е намерена")
    if int(fr.to_user_id) != int(user.id):
        raise HTTPException(status_code=403, detail="Нямаш права за тази покана")

    a = int(fr.from_user_id)
    b = int(fr.to_user_id)
    if not db.scalars(select(Friendship).where(Friendship.user_id == a, Friendship.friend_id == b)).first():
        db.add(Friendship(user_id=a, friend_id=b))
    if not db.scalars(select(Friendship).where(Friendship.user_id == b, Friendship.friend_id == a)).first():
        db.add(Friendship(user_id=b, friend_id=a))

    fr.status = "accepted"
    fr.responded_at = datetime.utcnow()
    # Ако има обратна чакаща покана, затваряме я.
    rev = db.scalars(
        select(FriendRequest).where(
            FriendRequest.from_user_id == b,
            FriendRequest.to_user_id == a,
            FriendRequest.status == "pending",
        )
    ).first()
    if rev:
        rev.status = "cancelled"
        rev.responded_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@app.post("/api/friends/requests/{request_id}/decline")
def decline_friend_request(
    request_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    fr = db.get(FriendRequest, int(request_id))
    if fr is None or fr.status != "pending":
        raise HTTPException(status_code=404, detail="Поканата не е намерена")
    if int(fr.to_user_id) != int(user.id):
        raise HTTPException(status_code=403, detail="Нямаш права за тази покана")
    fr.status = "rejected"
    fr.responded_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@app.post("/api/friends/requests/{request_id}/cancel")
def cancel_friend_request(
    request_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    fr = db.get(FriendRequest, int(request_id))
    if fr is None or fr.status != "pending":
        raise HTTPException(status_code=404, detail="Поканата не е намерена")
    if int(fr.from_user_id) != int(user.id):
        raise HTTPException(status_code=403, detail="Нямаш права за тази покана")
    fr.status = "cancelled"
    fr.responded_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@app.get("/api/friends/recommendations", response_model=list[RecommendationItem])
def friends_recommendations(
    limit: int = 12,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[RecommendationItem]:
    lim = max(1, min(30, limit))
    fids = friend_ids_for(db, user.id)
    if not fids:
        return []
    seen = set(db.scalars(select(Interaction.book_id).where(Interaction.user_id == user.id)).all())

    subq = (
        select(Interaction.book_id, func.count(Interaction.id).label("cnt"))
        .where(
            Interaction.user_id.in_(fids),
            or_(
                Interaction.event_type.in_(["like", "finished", "to_read"]),
                (Interaction.event_type == "rating") & (Interaction.rating >= 4.0),
            ),
        )
        .group_by(Interaction.book_id)
        .subquery()
    )
    stmt = (
        select(Book, subq.c.cnt)
        .join(subq, Book.id == subq.c.book_id)
        .order_by(subq.c.cnt.desc())
        .limit(lim * 2)
    )
    id_to_name = {u.id: u.username for u in db.scalars(select(User).where(User.id.in_(fids))).all()}
    out: list[RecommendationItem] = []
    for b, cnt in db.execute(stmt).all():
        if int(b.id) in seen:
            continue
        exp = friend_explanation_for_book(db, user.id, b, id_to_name) or "Приятели я харесват"
        out.append(RecommendationItem(book=BookOut.model_validate(b), score=float(cnt), explanation=exp))
        if len(out) >= lim:
            break
    return out


@app.get("/api/recommendations", response_model=list[RecommendationItem])
def get_recommendations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    k: int = 12,
    diversity: float = 0.72,
) -> list[RecommendationItem]:
    ctx = get_recommender_context(db)
    div = max(0.05, min(0.95, diversity))
    rec = recommend(db, ctx, user, k=k, diversity_lambda=div)
    return [
        RecommendationItem(book=BookOut.model_validate(b), score=round(s, 5), explanation=exp)
        for b, s, exp in rec
    ]


@app.get("/api/similar/{book_id}", response_model=list[BookOut])
def get_similar(book_id: int, k: int = 8, db: Session = Depends(get_db)) -> list[Book]:
    if db.get(Book, book_id) is None:
        raise HTTPException(status_code=404, detail="Книгата не е намерена")
    ctx = get_recommender_context(db)
    return similar_books(ctx, book_id, k=k)


@app.get("/api/library")
def library(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    by: dict[str, list[BookOut]] = {"to_read": [], "finished": [], "favorites": []}
    stmt = (
        select(Interaction, Book)
        .join(Book, Book.id == Interaction.book_id)
        .where(Interaction.user_id == user.id)
        .order_by(Interaction.created_at.desc())
    )
    seen: dict[int, str] = {}
    for it, b in db.execute(stmt).all():
        if it.event_type == "to_read" and b.id not in seen:
            by["to_read"].append(BookOut.model_validate(b))
            seen[b.id] = "to_read"
        if it.event_type == "finished":
            if b.id not in {x.id for x in by["finished"]}:
                by["finished"].append(BookOut.model_validate(b))
        if it.event_type in ("like", "rating") and (it.rating is None or it.rating >= 4.0):
            if b.id not in {x.id for x in by["favorites"]}:
                by["favorites"].append(BookOut.model_validate(b))
    return by


@app.get("/api/book-social/{book_id}")
def book_social(book_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    b = db.get(Book, book_id)
    if b is None:
        raise HTTPException(status_code=404, detail="Книгата не е намерена")
    names = {x.id: x.username for x in db.scalars(select(User))}
    exp = friend_explanation_for_book(db, user.id, b, names)
    return {"explanation": exp}


@app.get("/api/evaluation", response_model=EvaluationReport)
def evaluation(k: int = 10, db: Session = Depends(get_db)) -> EvaluationReport:
    kk = max(1, min(50, k))
    return build_evaluation_report(db, k=kk)
