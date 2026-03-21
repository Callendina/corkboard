"""Developer API — lifecycle management, export, tagging.

Authenticated via X-Corkboard-Dev-Key header (per-app secret).
Not exposed to end users.
"""
import datetime
import markdown
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from corkboard.database import get_db
from corkboard.config import CorkboardConfig, AppConfig
from corkboard.models import Post, Comment, ItemTag

router = APIRouter(prefix="/api/dev")
_config: CorkboardConfig = None

VALID_STATUSES = {"open", "acknowledged", "in_progress", "done", "wont_fix", "duplicate"}


def init_dev_api_routes(config: CorkboardConfig):
    global _config
    _config = config


def _auth_dev(request: Request) -> AppConfig:
    """Authenticate via X-Corkboard-Dev-Key and return the app config."""
    host = request.headers.get("host", "")
    app = _config.app_for_domain(host)
    if app is None:
        raise HTTPException(status_code=404, detail="Unknown app")

    key = request.headers.get("x-corkboard-dev-key", "")
    if not key or not app.dev_api_key or key != app.dev_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing dev API key")

    return app


def _post_to_dict(post: Post, include_comments: bool = False) -> dict:
    d = {
        "number": post.post_number,
        "title": post.title,
        "category": post.category,
        "status": post.status,
        "author_email": post.author_email,
        "vote_count": post.vote_count,
        "dev_note": post.dev_note,
        "tags": [t.tag for t in post.tags] if post.tags else [],
        "created_at": post.created_at.isoformat() + "Z" if post.created_at else None,
        "updated_at": post.updated_at.isoformat() + "Z" if post.updated_at else None,
    }
    if include_comments:
        d["body_markdown"] = post.body_markdown
        d["comments"] = [
            {
                "id": c.id,
                "author_email": c.author_email,
                "body_markdown": c.body_markdown,
                "is_dev_comment": c.is_dev_comment,
                "is_system_comment": c.is_system_comment,
                "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
            }
            for c in (post.comments or [])
            if c.deleted_at is None
        ]
    return d


async def _get_post(db: AsyncSession, app: AppConfig, number: int) -> Post:
    stmt = select(Post).where(
        Post.app_slug == app.slug,
        Post.post_number == number,
        Post.board_type == "structured",
        Post.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return post


@router.get("/items")
async def list_items(
    request: Request,
    status: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    page: int = 1,
    per_page: int = 50,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)

    stmt = select(Post).where(
        Post.app_slug == app.slug,
        Post.board_type == "structured",
        Post.deleted_at.is_(None),
    )
    if status:
        stmt = stmt.where(Post.status == status)
    if category:
        stmt = stmt.where(Post.category == category)

    stmt = stmt.order_by(Post.created_at.desc())

    # Count total before pagination
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = await db.scalar(count_stmt) or 0

    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    posts = result.scalars().all()

    # Filter by tag in Python (simpler than a join for small datasets)
    if tag:
        posts = [p for p in posts if any(t.tag == tag for t in (p.tags or []))]

    return JSONResponse({
        "items": [_post_to_dict(p) for p in posts],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@router.get("/items/export")
async def export_items(
    request: Request,
    status: str = "open",
    format: str = "markdown",
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)

    stmt = (
        select(Post)
        .where(
            Post.app_slug == app.slug,
            Post.board_type == "structured",
            Post.deleted_at.is_(None),
            Post.status == status,
        )
        .order_by(Post.vote_count.desc(), Post.created_at.asc())
    )
    result = await db.execute(stmt)
    posts = result.scalars().all()

    if format == "json":
        return JSONResponse({
            "items": [_post_to_dict(p, include_comments=True) for p in posts],
        })

    # Markdown export
    bugs = [p for p in posts if p.category.lower() in ("bug", "bug report")]
    features = [p for p in posts if p.category.lower() in ("feature", "feature request")]
    other = [p for p in posts if p not in bugs and p not in features]

    lines = []
    for label, items in [("Bugs", bugs), ("Feature requests", features), ("Other", other)]:
        if items:
            lines.append(f"## {label} ({len(items)})")
            for p in items:
                tags = ", ".join(t.tag for t in (p.tags or []))
                tag_str = f" [{tags}]" if tags else ""
                votes = f" ({p.vote_count} votes)" if p.vote_count else ""
                lines.append(f"- [ ] #{p.post_number}: {p.title}{votes}{tag_str}")
            lines.append("")

    return PlainTextResponse("\n".join(lines) if lines else "No items found.\n")


@router.get("/items/{number}")
async def get_item(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)
    post = await _get_post(db, app, number)
    return JSONResponse({"item": _post_to_dict(post, include_comments=True)})


@router.patch("/items/{number}")
async def update_item(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)
    post = await _get_post(db, app, number)
    body = await request.json()

    new_status = body.get("status")
    dev_note = body.get("dev_note")

    if new_status:
        if new_status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")
        old_status = post.status
        post.status = new_status

        # Auto-generate system comment for status change
        msg = f"Status changed from **{old_status}** to **{new_status}**."
        if dev_note:
            msg += f"\n\n{dev_note}"

        comment = Comment(
            post_id=post.id,
            body_markdown=msg,
            body_html=markdown.markdown(msg),
            author_email="system",
            author_role="admin",
            is_system_comment=True,
        )
        db.add(comment)

    if dev_note:
        post.dev_note = dev_note

    post.updated_at = datetime.datetime.utcnow()
    await db.commit()

    return JSONResponse({"item": _post_to_dict(post)})


@router.patch("/items/bulk")
async def bulk_update_items(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)
    body = await request.json()
    numbers = body.get("numbers", [])
    new_status = body.get("status")
    dev_note = body.get("dev_note", "")

    if not numbers or not new_status:
        raise HTTPException(status_code=400, detail="numbers and status required")
    if new_status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")

    updated = 0
    for num in numbers:
        post = await db.scalar(
            select(Post).where(
                Post.app_slug == app.slug,
                Post.post_number == num,
                Post.board_type == "structured",
                Post.deleted_at.is_(None),
            )
        )
        if post:
            old_status = post.status
            post.status = new_status
            post.updated_at = datetime.datetime.utcnow()
            if dev_note:
                post.dev_note = dev_note

            msg = f"Status changed from **{old_status}** to **{new_status}**."
            if dev_note:
                msg += f"\n\n{dev_note}"
            comment = Comment(
                post_id=post.id,
                body_markdown=msg,
                body_html=markdown.markdown(msg),
                author_email="system",
                author_role="admin",
                is_system_comment=True,
            )
            db.add(comment)
            updated += 1

    await db.commit()
    return JSONResponse({"updated": updated})


@router.post("/items/{number}/comment")
async def dev_comment(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)
    post = await _get_post(db, app, number)
    body = await request.json()
    text = body.get("body", "")
    if not text:
        raise HTTPException(status_code=400, detail="body is required")

    comment = Comment(
        post_id=post.id,
        body_markdown=text,
        body_html=markdown.markdown(text),
        author_email="developer",
        author_role="admin",
        is_dev_comment=True,
    )
    db.add(comment)
    await db.commit()

    return JSONResponse({
        "id": comment.id,
        "body_markdown": comment.body_markdown,
        "created_at": comment.created_at.isoformat() + "Z",
    })


@router.get("/tags")
async def list_tags(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)

    stmt = (
        select(ItemTag.tag, func.count(ItemTag.id))
        .join(Post, ItemTag.post_id == Post.id)
        .where(Post.app_slug == app.slug, Post.deleted_at.is_(None))
        .group_by(ItemTag.tag)
        .order_by(func.count(ItemTag.id).desc())
    )
    result = await db.execute(stmt)
    tags = [{"tag": row[0], "count": row[1]} for row in result.all()]

    return JSONResponse({"tags": tags})


@router.post("/items/{number}/tags")
async def set_tags(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)
    post = await _get_post(db, app, number)
    body = await request.json()
    new_tags = body.get("tags", [])

    # Remove existing tags
    for tag in list(post.tags or []):
        await db.delete(tag)

    # Add new tags
    for tag_name in new_tags:
        db.add(ItemTag(post_id=post.id, tag=tag_name.strip()))

    await db.commit()

    return JSONResponse({"tags": new_tags})
