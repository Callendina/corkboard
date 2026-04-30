"""Developer API — lifecycle management, export, tagging, move posts.

Authentication (checked in order):
1. X-Gatekeeper-* headers with admin role (Caddy forward_auth)
2. X-API-Key header matching the app's dev_api_key (from YAML config)
"""
import datetime
import json
import secrets

import cyclops

from corkboard.rendering import render_markdown
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from corkboard.database import get_db
from corkboard.config import CorkboardConfig, AppConfig, POST_TYPE_FIELDS, POST_TYPE_STATUSES, POST_TYPE_INITIAL_STATUS
from corkboard.models import Post, Comment, ItemTag

router = APIRouter(prefix="/api/dev")
_config: CorkboardConfig = None

def _valid_statuses_for(post_type: str) -> set[str]:
    return POST_TYPE_STATUSES.get(post_type, POST_TYPE_STATUSES["bug"])


def init_dev_api_routes(config: CorkboardConfig):
    global _config
    _config = config


def _auth_dev(request: Request) -> AppConfig:
    host = request.headers.get("host", "")
    app = _config.app_for_domain(host)
    if app is None:
        raise HTTPException(status_code=404, detail="Unknown app")

    # 1. Check if already authenticated via Caddy forward_auth (admin)
    gk_role = request.headers.get("x-gatekeeper-role", "")
    gk_admin = request.headers.get("x-gatekeeper-system-admin", "") == "true"
    if gk_role == "admin" or gk_admin:
        return app

    # 2. Check X-API-Key against the app's configured dev_api_key
    api_key = request.headers.get("x-api-key", "")
    if api_key and app.dev_api_key and secrets.compare_digest(api_key, app.dev_api_key):
        return app

    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _post_to_dict(post: Post, include_comments: bool = False) -> dict:
    d = {
        "number": post.post_number,
        "forum": post.forum_slug,
        "post_type": post.post_type,
        "title": post.title,
        "status": post.status,
        "author_email": post.author_email,
        "vote_count": post.vote_count,
        "dev_note": post.dev_note,
        "done_version": post.done_version,
        "related_to": post.related_to,
        "blocked_by": post.blocked_by,
        "fields": post.fields,
        "tags": [t.tag for t in post.tags] if post.tags else [],
        "moved_from_forum": post.moved_from_forum,
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


async def _get_post(db: AsyncSession, app: AppConfig, number: int,
                    lifecycle_only: bool = False) -> Post:
    stmt = select(Post).where(
        Post.app_slug == app.slug,
        Post.post_number == number,
        Post.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if lifecycle_only:
        forum = app.get_forum(post.forum_slug)
        if not forum or forum.forum_type != "lifecycle":
            raise HTTPException(status_code=400, detail="Post is not in a lifecycle forum")
    return post


# --- Items (lifecycle posts) ---


@router.get("/items")
async def list_items(
    request: Request,
    forum: str = "",
    status: str | None = None,
    post_type: str | None = None,
    tag: str | None = None,
    page: int = 1,
    per_page: int = 50,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)

    # Get lifecycle forum slugs
    lifecycle_slugs = [f.slug for f in app.forums if f.forum_type == "lifecycle"]
    if not lifecycle_slugs:
        return JSONResponse({"items": [], "total": 0, "page": page, "per_page": per_page})

    stmt = select(Post).where(
        Post.app_slug == app.slug,
        Post.deleted_at.is_(None),
    )
    if forum:
        stmt = stmt.where(Post.forum_slug == forum)
    else:
        stmt = stmt.where(Post.forum_slug.in_(lifecycle_slugs))

    if status:
        stmt = stmt.where(Post.status == status)
    if post_type:
        stmt = stmt.where(Post.post_type == post_type)

    stmt = stmt.order_by(Post.created_at.desc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = await db.scalar(count_stmt) or 0

    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(stmt)
    posts = result.scalars().all()

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
    forum: str = "",
    status: str = "open",
    format: str = "markdown",
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)

    lifecycle_slugs = [f.slug for f in app.forums if f.forum_type == "lifecycle"]
    stmt = (
        select(Post)
        .where(
            Post.app_slug == app.slug,
            Post.deleted_at.is_(None),
            Post.status == status,
        )
    )
    if forum:
        stmt = stmt.where(Post.forum_slug == forum)
    else:
        stmt = stmt.where(Post.forum_slug.in_(lifecycle_slugs))

    stmt = stmt.order_by(Post.vote_count.desc(), Post.created_at.asc())
    result = await db.execute(stmt)
    posts = result.scalars().all()

    if format == "json":
        return JSONResponse({
            "items": [_post_to_dict(p, include_comments=True) for p in posts],
        })

    # Markdown export — group by post_type
    groups = {}
    for p in posts:
        groups.setdefault(p.post_type, []).append(p)

    type_labels = {"bug": "Bugs", "feature": "Feature requests", "todo": "Todo", "general": "Other"}
    lines = []
    for pt in ["bug", "feature", "todo", "general"]:
        items = groups.get(pt, [])
        if items:
            label = type_labels.get(pt, pt)
            lines.append(f"## {label} ({len(items)})")
            for p in items:
                tags = ", ".join(t.tag for t in (p.tags or []))
                tag_str = f" [{tags}]" if tags else ""
                votes = f" ({p.vote_count} votes)" if p.vote_count else ""
                forum_label = f" @{p.forum_slug}" if forum == "" else ""
                lines.append(f"- [ ] #{p.post_number}: {p.title}{votes}{tag_str}{forum_label}")
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


@router.delete("/items/{number}")
async def delete_item(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)
    post = await _get_post(db, app, number)
    post.deleted_at = datetime.datetime.utcnow()
    post.updated_at = post.deleted_at
    await db.commit()

    cyclops.event(
        "corkboard.post.deleted",
        post_app=app.slug,
        forum=post.forum_slug,
        post_type=post.post_type,
        post_number=number,
        source_kind="dev_api",
    )

    return JSONResponse({"deleted": number})


@router.patch("/items/{number}")
async def update_item(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
):
    app = _auth_dev(request)
    post = await _get_post(db, app, number, lifecycle_only=True)
    body = await request.json()

    new_status = body.get("status")
    dev_note = body.get("dev_note")
    done_version = body.get("done_version")

    if new_status:
        valid = _valid_statuses_for(post.post_type)
        if new_status not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{new_status}' for {post.post_type}. Valid: {sorted(valid)}"
            )
        old_status = post.status
        post.status = new_status

        msg = f"Status changed from **{old_status}** to **{new_status}**."
        if dev_note:
            msg += f"\n\n{dev_note}"
        if new_status == "done" and done_version:
            msg += f"\n\nIncluded in version **{done_version}**."

        comment = Comment(
            post_id=post.id,
            body_markdown=msg,
            body_html=render_markdown(msg),
            author_email="system",
            author_role="admin",
            is_system_comment=True,
        )
        db.add(comment)

    if dev_note:
        post.dev_note = dev_note
    if done_version:
        post.done_version = done_version
    if "blocked_by" in body:
        val = body["blocked_by"]
        post.blocked_by = int(val) if val else None
    if "fields" in body and isinstance(body["fields"], dict):
        existing = post.fields
        existing.update(body["fields"])
        post.fields_json = json.dumps(existing) if existing else None

    post.updated_at = datetime.datetime.utcnow()
    await db.commit()
    await db.refresh(post)

    if new_status:
        cyclops.event(
            "corkboard.post.status_changed",
            post_app=app.slug,
            forum=post.forum_slug,
            post_type=post.post_type,
            post_number=number,
            old_status=old_status,
            new_status=new_status,
            done_version=done_version or "",
        )

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

    done_version = body.get("done_version", "")

    if not numbers or not new_status:
        raise HTTPException(status_code=400, detail="numbers and status required")

    updated = 0
    for num in numbers:
        post = await db.scalar(
            select(Post).where(
                Post.app_slug == app.slug,
                Post.post_number == num,
                Post.deleted_at.is_(None),
            )
        )
        if post and post.status is not None:  # only lifecycle posts
            valid = _valid_statuses_for(post.post_type)
            if new_status not in valid:
                continue  # skip posts where status doesn't apply

            old_status = post.status
            post.status = new_status
            post.updated_at = datetime.datetime.utcnow()
            if dev_note:
                post.dev_note = dev_note
            if done_version and new_status == "done":
                post.done_version = done_version

            msg = f"Status changed from **{old_status}** to **{new_status}**."
            if dev_note:
                msg += f"\n\n{dev_note}"
            if new_status == "done" and done_version:
                msg += f"\n\nIncluded in version **{done_version}**."
            comment = Comment(
                post_id=post.id,
                body_markdown=msg,
                body_html=render_markdown(msg),
                author_email="system",
                author_role="admin",
                is_system_comment=True,
            )
            db.add(comment)
            updated += 1

    await db.commit()
    return JSONResponse({"updated": updated})


@router.post("/items/{number}/move")
async def move_item(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
):
    """Move a post to a different forum, optionally changing its post_type."""
    app = _auth_dev(request)
    post = await _get_post(db, app, number)
    body = await request.json()

    target_forum_slug = body.get("forum")
    new_post_type = body.get("post_type")

    if not target_forum_slug:
        raise HTTPException(status_code=400, detail="forum is required")

    target_forum = app.get_forum(target_forum_slug)
    if target_forum is None:
        raise HTTPException(status_code=404, detail=f"Forum '{target_forum_slug}' not found")

    old_forum = post.forum_slug
    post.moved_from_forum = old_forum
    post.forum_slug = target_forum_slug

    if new_post_type:
        if new_post_type not in target_forum.post_types:
            raise HTTPException(
                status_code=400,
                detail=f"Post type '{new_post_type}' not allowed in forum '{target_forum_slug}'"
            )
        post.post_type = new_post_type

    # If moving to a lifecycle forum and post has no status, set to open
    if target_forum.forum_type == "lifecycle" and post.status is None:
        post.status = "open"

    # System comment recording the move
    msg = f"Moved from **{old_forum}** to **{target_forum_slug}**."
    if new_post_type:
        msg += f" Post type changed to **{new_post_type}**."
    comment = Comment(
        post_id=post.id,
        body_markdown=msg,
        body_html=render_markdown(msg),
        author_email="system",
        author_role="admin",
        is_system_comment=True,
    )
    db.add(comment)

    post.updated_at = datetime.datetime.utcnow()
    await db.commit()
    await db.refresh(post)

    return JSONResponse({"item": _post_to_dict(post)})


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
        body_html=render_markdown(text),
        author_email=body.get("author", "developer"),
        author_role="admin",
        is_dev_comment=True,
    )
    db.add(comment)
    await db.commit()

    cyclops.event(
        "corkboard.comment.added",
        post_app=app.slug,
        forum=post.forum_slug,
        post_number=number,
        comment_kind="dev",
        masked_author=cyclops.redact_email(comment.author_email or "developer"),
    )

    return JSONResponse({
        "id": comment.id,
        "body_markdown": comment.body_markdown,
        "created_at": comment.created_at.isoformat() + "Z",
    })


@router.post("/items/create")
async def create_todo(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a post via the dev API (typically a todo item)."""
    app = _auth_dev(request)
    body = await request.json()

    forum_slug = body.get("forum", "")
    forum = app.get_forum(forum_slug)
    if not forum:
        raise HTTPException(status_code=400, detail=f"Forum '{forum_slug}' not found")

    post_type = body.get("post_type", "todo")
    if post_type not in forum.post_types:
        raise HTTPException(
            status_code=400,
            detail=f"Post type '{post_type}' not allowed in forum '{forum_slug}'"
        )

    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    body_text = body.get("body", "")
    fields_data = body.get("fields", {})
    related_to = body.get("related_to")
    blocked_by = body.get("blocked_by")

    from corkboard.routes.board import _next_post_number
    post_number = await _next_post_number(db, app.slug)

    post = Post(
        app_slug=app.slug,
        post_number=post_number,
        forum_slug=forum_slug,
        post_type=post_type,
        title=title,
        body_markdown=body_text,
        body_html=render_markdown(body_text) if body_text else "",
        fields_json=json.dumps(fields_data) if fields_data else None,
        author_email=body.get("author", "developer"),
        author_role="admin",
        status=POST_TYPE_INITIAL_STATUS.get(post_type) if forum.forum_type == "lifecycle" else None,
        related_to=int(related_to) if related_to else None,
        blocked_by=int(blocked_by) if blocked_by else None,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    cyclops.event(
        "corkboard.post.created",
        post_app=app.slug,
        forum=forum_slug,
        post_type=post_type,
        post_number=post.post_number,
        masked_author=cyclops.redact_email(post.author_email or "developer"),
        source_kind="dev_api",
    )

    return JSONResponse({"item": _post_to_dict(post)})


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

    for tag in list(post.tags or []):
        await db.delete(tag)

    for tag_name in new_tags:
        db.add(ItemTag(post_id=post.id, tag=tag_name.strip()))

    await db.commit()

    return JSONResponse({"tags": new_tags})


@router.get("/forums")
async def list_forums(request: Request):
    """List all forums for this app."""
    app = _auth_dev(request)
    return JSONResponse({"forums": [
        {
            "slug": f.slug,
            "name": f.name,
            "type": f.forum_type,
            "post_types": f.post_types,
            "read_roles": f.read_roles,
            "post_roles": f.post_roles,
        }
        for f in app.forums
    ]})
