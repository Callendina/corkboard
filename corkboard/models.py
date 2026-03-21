import datetime
from sqlalchemy import (
    String, Integer, DateTime, Text, Boolean, ForeignKey, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from corkboard.database import Base


class AppCounter(Base):
    __tablename__ = "app_counters"

    app_slug: Mapped[str] = mapped_column(String(100), primary_key=True)
    next_post_number: Mapped[int] = mapped_column(Integer, default=1)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    post_number: Mapped[int] = mapped_column(Integer, nullable=False)
    board_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "general" | "structured"
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    author_email: Mapped[str] = mapped_column(String(255), nullable=False)
    author_role: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    duplicate_of: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("posts.id"), nullable=True
    )
    dev_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    vote_count: Mapped[int] = mapped_column(Integer, default=0)
    scrubbed: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    comments: Mapped[list["Comment"]] = relationship(back_populates="post")
    votes: Mapped[list["Vote"]] = relationship(back_populates="post")
    tags: Mapped[list["ItemTag"]] = relationship(back_populates="post")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="post")

    __table_args__ = (
        Index("ix_post_app_number", "app_slug", "post_number", unique=True),
        Index("ix_post_app_type_status", "app_slug", "board_type", "status"),
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    author_email: Mapped[str] = mapped_column(String(255), nullable=False)
    author_role: Mapped[str] = mapped_column(String(50), nullable=False)
    is_dev_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    is_system_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    scrubbed: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    post: Mapped["Post"] = relationship(back_populates="comments")


class Vote(Base):
    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    post: Mapped["Post"] = relationship(back_populates="votes")

    __table_args__ = (
        Index("ix_vote_post_email", "post_id", "email", unique=True),
    )


class ItemTag(Base):
    __tablename__ = "item_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    tag: Mapped[str] = mapped_column(String(100), nullable=False)

    post: Mapped["Post"] = relationship(back_populates="tags")

    __table_args__ = (
        Index("ix_tag_post_tag", "post_id", "tag", unique=True),
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    uploaded_by_email: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    post: Mapped["Post"] = relationship(back_populates="attachments")
