from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    onboarding_genres: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated
    onboarding_authors: Mapped[str | None] = mapped_column(Text, nullable=True)  # comma-separated
    onboarding_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_survey_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    interactions: Mapped[list[Interaction]] = relationship(
        "Interaction", back_populates="user", cascade="all, delete-orphan"
    )
    friendships_out: Mapped[list[Friendship]] = relationship(
        "Friendship",
        foreign_keys="Friendship.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    authors: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")  # comma-separated genres/tags
    language: Mapped[str] = mapped_column(String(32), default="en")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    isbn: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    cover_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    interactions: Mapped[list[Interaction]] = relationship(
        "Interaction", back_populates="book", cascade="all, delete-orphan"
    )


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user: Mapped[User] = relationship("User", back_populates="interactions")
    book: Mapped[Book] = relationship("Book", back_populates="interactions")


class Friendship(Base):
    __tablename__ = "friendships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    friend_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    since: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship("User", foreign_keys=[user_id], back_populates="friendships_out")

    __table_args__ = (UniqueConstraint("user_id", "friend_id", name="uq_friend_pair"),)


class FriendRequest(Base):
    __tablename__ = "friend_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    from_user: Mapped[User] = relationship("User", foreign_keys=[from_user_id])
    to_user: Mapped[User] = relationship("User", foreign_keys=[to_user_id])

    __table_args__ = (
        UniqueConstraint("from_user_id", "to_user_id", name="uq_friend_request_pair"),
    )
