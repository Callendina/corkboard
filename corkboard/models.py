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
    forum_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    post_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "bug" | "feature" | "todo" | "general"
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured fields stored as JSON string (e.g. steps_to_reproduce, severity)
    fields_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_email: Mapped[str] = mapped_column(String(255), nullable=False)
    author_role: Mapped[str] = mapped_column(String(50), nullable=False)
    # Lifecycle status (only for lifecycle forums)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    duplicate_of: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("posts.id"), nullable=True
    )
    # Set when a post is moved from another forum
    moved_from_forum: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dev_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    done_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vote_count: Mapped[int] = mapped_column(Integer, default=0)
    scrubbed: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    comments: Mapped[list["Comment"]] = relationship(back_populates="post", lazy="selectin")
    votes: Mapped[list["Vote"]] = relationship(back_populates="post", lazy="selectin")
    tags: Mapped[list["ItemTag"]] = relationship(back_populates="post", lazy="selectin")

    @property
    def fields(self) -> dict:
        if self.fields_json:
            import json
            return json.loads(self.fields_json)
        return {}

    __table_args__ = (
        Index("ix_post_app_number", "app_slug", "post_number", unique=True),
        Index("ix_post_app_forum_status", "app_slug", "forum_slug", "status"),
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


