"""Forum UI routes — forum listing, posts, comments, voting."""
import datetime
import json
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from corkboard.database import get_db
from corkboard.config import CorkboardConfig, AppConfig, ForumConfig, POST_TYPE_FIELDS
from corkboard.auth import RequestUser, get_current_user
from corkboard.scrub import scrub_sensitive, mask_author
from corkboard.rendering import render_markdown
from corkboard.theme import theme_css_override, theme_meta
from corkboard.rate_limit import check_post_rate, check_comment_rate
from corkboard.models import Post, Comment, Vote, AppCounter

router = APIRouter()
_config: CorkboardConfig = None
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def init_board_routes(config: CorkboardConfig):
    global _config
    _config = config
    templates.env.globals["mask_author"] = mask_author
    templates.env.globals["POST_TYPE_FIELDS"] = POST_TYPE_FIELDS


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


def _get_app(request: Request) -> AppConfig:
    host = request.headers.get("host", "")
    app = _config.app_for_domain(host)
    if app is None:
        raise HTTPException(status_code=404, detail="Unknown app")
    return app


def _get_forum(app: AppConfig, forum_slug: str, user: RequestUser) -> ForumConfig:
    forum = app.get_forum(forum_slug)
    if forum is None:
        raise HTTPException(status_code=404, detail="Forum not found")
    if user.role not in forum.read_roles:
        raise HTTPException(status_code=403, detail="Access denied")
    return forum


async def _next_post_number(db: AsyncSession, app_slug: str) -> int:
    counter = await db.get(AppCounter, app_slug)
    if counter is None:
        counter = AppCounter(app_slug=app_slug, next_post_number=1)
        db.add(counter)
        await db.flush()

    number = counter.next_post_number
    counter.next_post_number = number + 1
    await db.flush()
    return number


def _render_markdown(text: str) -> str:
    return render_markdown(text)


def _base_context(request: Request, app: AppConfig, user: RequestUser, **extra):
    ctx = {
        "request": request,
        "app": app,
        "user": user,
        "prefix": _config.mount_prefix,
        "visible_forums": app.forums_visible_to(user.role),
        "theme_css": theme_css_override(app.theme_file),
        "theme": theme_meta(app.theme_file),
    }
    ctx.update(extra)
    return ctx


# --- Routes ---


@router.get("/")
async def forum_index(
    request: Request,
    user: RequestUser = Depends(get_current_user),
):
    """List all forums the user can see."""
    app = _get_app(request)
    forums = app.forums_visible_to(user.role)
    if len(forums) == 1:
        return RedirectResponse(
            url=f"{_config.mount_prefix}/f/{forums[0].slug}", status_code=302
        )
    return templates.TemplateResponse("forum_index.html", _base_context(
        request, app, user, forums=forums,
    ))


@router.get("/f/{forum_slug}")
async def forum_view(
    request: Request,
    forum_slug: str,
    page: int = 1,
    status: str = "",
    post_type: str = "",
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    """List posts in a forum."""
    app = _get_app(request)
    forum = _get_forum(app, forum_slug, user)

    per_page = 20
    offset = (page - 1) * per_page

    stmt = (
        select(Post)
        .where(
            Post.app_slug == app.slug,
            Post.forum_slug == forum_slug,
            Post.deleted_at.is_(None),
        )
    )
    if status:
        stmt = stmt.where(Post.status == status)
    if post_type:
        stmt = stmt.where(Post.post_type == post_type)

    stmt = stmt.order_by(Post.created_at.desc()).offset(offset).limit(per_page)
    result = await db.execute(stmt)
    posts = result.scalars().all()

    total = await db.scalar(
        select(func.count(Post.id)).where(
            Post.app_slug == app.slug,
            Post.forum_slug == forum_slug,
            Post.deleted_at.is_(None),
        )
    )

    return templates.TemplateResponse("board.html", _base_context(
        request, app, user,
        forum=forum, posts=posts, page=page, total=total or 0, per_page=per_page,
        filter_status=status, filter_post_type=post_type,
    ))


@router.get("/f/{forum_slug}/new")
async def new_post_form(
    request: Request,
    forum_slug: str,
    post_type: str = "",
    user: RequestUser = Depends(get_current_user),
):
    app = _get_app(request)
    forum = _get_forum(app, forum_slug, user)

    if user.role not in forum.post_roles:
        return RedirectResponse(url=f"/_auth/login?next={_config.mount_prefix}/f/{forum_slug}/new")

    # If forum allows multiple post types and none selected, show type picker
    if not post_type and len(forum.post_types) > 1:
        return templates.TemplateResponse("new_post_type_picker.html", _base_context(
            request, app, user, forum=forum,
        ))

    pt = post_type or forum.post_types[0]
    if pt not in forum.post_types:
        raise HTTPException(status_code=400, detail=f"Post type '{pt}' not allowed in this forum")

    type_fields = POST_TYPE_FIELDS.get(pt, {})

    return templates.TemplateResponse("new_post.html", _base_context(
        request, app, user, forum=forum, post_type=pt, type_fields=type_fields,
    ))


@router.post("/f/{forum_slug}/new")
async def create_post(
    request: Request,
    forum_slug: str,
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    app = _get_app(request)
    forum = _get_forum(app, forum_slug, user)

    if user.role not in forum.post_roles:
        raise HTTPException(status_code=403, detail="Not authorised to post in this forum")

    identifier = user.email or _get_client_ip(request)
    if not check_post_rate(identifier, app.rate_limits.posts_per_hour):
        raise HTTPException(status_code=429, detail="Post rate limit exceeded. Try again later.")

    form = await request.form()
    title = form.get("title", "").strip()
    body = form.get("body", "").strip()
    post_type = form.get("post_type", forum.post_types[0])

    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    if post_type not in forum.post_types:
        raise HTTPException(status_code=400, detail=f"Post type '{post_type}' not allowed")

    # Scrub title and body
    title, title_scrubbed = scrub_sensitive(title)
    body, body_scrubbed = scrub_sensitive(body)

    # Collect structured fields
    type_fields = POST_TYPE_FIELDS.get(post_type, {})
    fields_data = {}
    for field_name, field_def in type_fields.items():
        val = form.get(field_name, "").strip()
        if val:
            val, _ = scrub_sensitive(val)
        fields_data[field_name] = val

    body_html = _render_markdown(body) if body else ""
    post_number = await _next_post_number(db, app.slug)
    status = "open" if forum.forum_type == "lifecycle" else None

    post = Post(
        app_slug=app.slug,
        post_number=post_number,
        forum_slug=forum_slug,
        post_type=post_type,
        title=title,
        body_markdown=body,
        body_html=body_html,
        fields_json=json.dumps(fields_data) if fields_data else None,
        author_email=user.email or "anonymous",
        author_role=user.role,
        status=status,
        scrubbed=title_scrubbed or body_scrubbed,
    )
    db.add(post)
    await db.commit()

    return RedirectResponse(
        url=f"{_config.mount_prefix}/post/{post_number}",
        status_code=302,
    )


@router.get("/post/{number}")
async def view_post(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    app = _get_app(request)

    post = await db.scalar(
        select(Post).where(
            Post.app_slug == app.slug,
            Post.post_number == number,
        )
    )
    if post is None or (post.deleted_at and not user.is_admin):
        raise HTTPException(status_code=404, detail="Post not found")

    # Check forum read access
    forum = app.get_forum(post.forum_slug)
    if forum and user.role not in forum.read_roles:
        raise HTTPException(status_code=403, detail="Access denied")

    comments = (await db.execute(
        select(Comment)
        .where(Comment.post_id == post.id, Comment.deleted_at.is_(None))
        .order_by(Comment.created_at.asc())
    )).scalars().all()

    user_voted = False
    if user.email:
        user_voted = await db.scalar(
            select(Vote.id).where(Vote.post_id == post.id, Vote.email == user.email)
        ) is not None

    type_fields = POST_TYPE_FIELDS.get(post.post_type, {})

    return templates.TemplateResponse("post.html", _base_context(
        request, app, user,
        post=post, forum=forum, comments=comments,
        user_voted=user_voted, type_fields=type_fields,
    ))


@router.post("/post/{number}/comment")
async def add_comment(
    request: Request,
    number: int,
    body: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    app = _get_app(request)

    post = await db.scalar(
        select(Post).where(Post.app_slug == app.slug, Post.post_number == number)
    )
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    forum = app.get_forum(post.forum_slug)
    if forum and user.role not in forum.post_roles:
        raise HTTPException(status_code=403, detail="Sign in to comment")

    identifier = user.email or _get_client_ip(request)
    if not check_comment_rate(identifier, app.rate_limits.comments_per_hour):
        raise HTTPException(status_code=429, detail="Comment rate limit exceeded. Try again later.")

    body, was_scrubbed = scrub_sensitive(body)
    body_html = _render_markdown(body)

    comment = Comment(
        post_id=post.id,
        body_markdown=body,
        body_html=body_html,
        author_email=user.email or "anonymous",
        author_role=user.role,
        scrubbed=was_scrubbed,
    )
    db.add(comment)
    await db.commit()

    return RedirectResponse(
        url=f"{_config.mount_prefix}/post/{number}",
        status_code=302,
    )


@router.post("/post/{number}/vote")
async def toggle_vote(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    if not user.email:
        raise HTTPException(status_code=403, detail="Sign in to vote")

    app = _get_app(request)
    post = await db.scalar(
        select(Post).where(Post.app_slug == app.slug, Post.post_number == number)
    )
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    existing = await db.scalar(
        select(Vote).where(Vote.post_id == post.id, Vote.email == user.email)
    )
    if existing:
        await db.delete(existing)
        post.vote_count = max(0, post.vote_count - 1)
    else:
        db.add(Vote(post_id=post.id, email=user.email))
        post.vote_count = post.vote_count + 1

    await db.commit()

    return RedirectResponse(
        url=f"{_config.mount_prefix}/post/{number}",
        status_code=302,
    )


@router.post("/post/{number}/delete")
async def delete_post(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    app = _get_app(request)
    post = await db.scalar(
        select(Post).where(Post.app_slug == app.slug, Post.post_number == number)
    )
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if not user.is_admin and user.email != post.author_email:
        raise HTTPException(status_code=403, detail="Not authorised")

    post.deleted_at = datetime.datetime.utcnow()
    await db.commit()

    return RedirectResponse(
        url=f"{_config.mount_prefix}/f/{post.forum_slug}", status_code=302
    )
