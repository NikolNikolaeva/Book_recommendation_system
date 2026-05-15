from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EventTypeLiteral = Literal["rating", "like", "dislike", "to_read", "finished"]


class BookOut(BaseModel):
    id: int
    title: str
    authors: str
    description: str
    tags: str
    language: str
    year: int | None = None
    isbn: str | None = None
    cover_url: str | None = None

    class Config:
        from_attributes = True


class RegisterSurveyIn(BaseModel):
    """Лека анкета при регистрация — подобрява cold start преди пълния onboarding."""

    reading_pace: Literal["light", "steady", "avid"] | None = None
    moods: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=420)

    @field_validator("moods", "formats")
    @classmethod
    def _cap_list(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for x in v[:10]:
            s = (x or "").strip()
            if s:
                out.append(s[:72])
        return out

    @field_validator("note")
    @classmethod
    def _note_trim(cls, v: str | None) -> str | None:
        if not v:
            return None
        s = v.strip()
        return s[:420] if s else None


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: str | None = Field(default=None, max_length=256)
    survey: RegisterSurveyIn | None = None


class LoginIn(BaseModel):
    username: str
    password: str


class UserMe(BaseModel):
    id: int
    username: str
    email: str | None
    onboarding_completed: bool


class QuickReaction(BaseModel):
    book_id: int
    reaction: Literal["like", "dislike", "finished"]


class OnboardingCompleteIn(BaseModel):
    """Пълен cold start: жанрове, език, любими автори, бързи Like/Dislike/Чел(а) съм."""

    genres: list[str]
    language: str
    authors: list[str] = Field(default_factory=list)
    quick_reactions: list[QuickReaction] = Field(default_factory=list)


class InteractionCreate(BaseModel):
    book_id: int
    event_type: EventTypeLiteral
    rating: float | None = Field(default=None, ge=1, le=5)
    comment: str | None = None


class InteractionOut(BaseModel):
    id: int
    user_id: int
    book_id: int
    event_type: str
    rating: float | None
    comment: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class BookSignalOut(BaseModel):
    """Последни сигнали за една книга — за UI (звезди, активен бърз бутон)."""

    last_rating: float | None = None
    last_event: str | None = None


class BookSignalsResponse(BaseModel):
    books: dict[str, BookSignalOut]


class OnboardingIn(BaseModel):
    genres: list[str]
    language: str  # "bg" | "en" | "both"


class FriendAdd(BaseModel):
    friend_username: str


class FriendOut(BaseModel):
    id: int
    username: str


class RecommendationItem(BaseModel):
    book: BookOut
    score: float
    explanation: str


class EvaluationRow(BaseModel):
    method: str
    precision_at_k: float
    recall_at_k: float
    ndcg_at_k: float


class EvaluationReport(BaseModel):
    k: int
    rows: list[EvaluationRow]
    note: str
    users_in_fold: int = Field(default=0, description="Брой потребители в offline fold (има скрит последен положителен сигнал).")
    problem_statement: str = ""
    hypothesis: str = ""
    methodology: str = ""
    limitations: str = ""


class ServiceMeta(BaseModel):
    service: str = "book-recsys"
    version: str
    environment: str
    debug: bool
    demo_seed_enabled: bool
    database_ok: bool
    book_count: int = 0
    catalog_token_required: bool = False
    openai_configured: bool = False


class CatalogFetchIn(BaseModel):
    """Импорт от Open Library Search API (без ключ)."""

    query: str = Field(min_length=2, max_length=240)
    limit: int = Field(default=25, ge=1, le=80)
    fetch_descriptions: bool = Field(
        default=False,
        description="Още HTTP заявки към works.json за първите заглавия — по-богато описание.",
    )


class CatalogFetchOut(BaseModel):
    inserted: int
    updated: int
    skipped: int
    total_books: int
