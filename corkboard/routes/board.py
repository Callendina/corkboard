"""Forum UI routes — post listing, creation, comments, voting."""
import datetime
import markdown
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from corkboard.database import get_db
from corkboard.config import CorkboardConfig, AppConfig
from corkboard.auth import RequestUser, get_current_user
from corkboard.scrub import scrub_sensitive, mask_author
from corkboard.models import Post, Comment, Vote, AppCounter

router = APIRouter()
_config: CorkboardConfig = None
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def init_board_routes(config: CorkboardConfig):
    global _config
    _config = config
    # Make mask_author available in templates
    templates.env.globals["mask_author"] = mask_author


def _get_app(request: Request) -> AppConfig:
    host = request.headers.get("host", "")
    app = _config.app_for_domain(host)
    if app is None:
        raise HTTPException(status_code=404, detail="Unknown app")
    return app


async def _next_post_number(db: AsyncSession, app_slug: str) -> int:
    """Atomically allocate the next post number for an app."""
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
    return markdown.markdown(text, extensions=["fenced_code", "tables", "nl2br"])


def _base_context(request: Request, app: AppConfig, user: RequestUser, **extra):
    ctx = {
        "request": request,
        "app": app,
        "user": user,
        "prefix": _config.mount_prefix,
    }
    ctx.update(extra)
    return ctx


# --- Routes ---


@router.get("/")
async def board_home(
    request: Request,
    board: str = "structured",
    page: int = 1,
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    app = _get_app(request)
    per_page = 20
    offset = (page - 1) * per_page

    stmt = (
        select(Post)
        .where(
            Post.app_slug == app.slug,
            Post.board_type == board,
            Post.deleted_at.is_(None),
        )
        .order_by(Post.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    result = await db.execute(stmt)
    posts = result.scalars().all()

    total = await db.scalar(
        select(func.count(Post.id)).where(
            Post.app_slug == app.slug,
            Post.board_type == board,
            Post.deleted_at.is_(None),
        )
    )

    return templates.TemplateResponse("board.html", _base_context(
        request, app, user,
        posts=posts, board=board, page=page, total=total or 0, per_page=per_page,
    ))


@router.get("/new")
async def new_post_form(
    request: Request,
    board: str = "structured",
    user: RequestUser = Depends(get_current_user),
):
    app = _get_app(request)
    if user.role == "anon" and app.anonymous_access != "post_allowed":
        return RedirectResponse(url=f"/_auth/login?next={_config.mount_prefix}/new")

    categories = app.categories.structured if board == "structured" else app.categories.general
    return templates.TemplateResponse("new_post.html", _base_context(
        request, app, user, board=board, categories=categories,
    ))


@router.post("/new")
async def create_post(
    request: Request,
    title: str = Form(...),
    body: str = Form(...),
    category: str = Form(...),
    board: str = Form("structured"),
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    app = _get_app(request)
    if user.role == "anon" and app.anonymous_access != "post_allowed":
        raise HTTPException(status_code=403, detail="Sign in to post")

    # Scrub sensitive data before anything else
    title, title_scrubbed = scrub_sensitive(title)
    body, body_scrubbed = scrub_sensitive(body)

    body_html = _render_markdown(body)
    post_number = await _next_post_number(db, app.slug)

    status = "open" if board == "structured" else None

    post = Post(
        app_slug=app.slug,
        post_number=post_number,
        board_type=board,
        category=category,
        title=title,
        body_markdown=body,
        body_html=body_html,
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

    stmt = select(Post).where(
        Post.app_slug == app.slug,
        Post.post_number == number,
    )
    result = await db.execute(stmt)
    post = result.scalar_one_or_none()
    if post is None or (post.deleted_at and not user.is_admin):
        raise HTTPException(status_code=404, detail="Post not found")

    comments_stmt = (
        select(Comment)
        .where(Comment.post_id == post.id, Comment.deleted_at.is_(None))
        .order_by(Comment.created_at.asc())
    )
    comments_result = await db.execute(comments_stmt)
    comments = comments_result.scalars().all()

    # Check if current user has voted
    user_voted = False
    if user.email:
        vote = await db.scalar(
            select(Vote.id).where(Vote.post_id == post.id, Vote.email == user.email)
        )
        user_voted = vote is not None

    return templates.TemplateResponse("post.html", _base_context(
        request, app, user,
        post=post, comments=comments, user_voted=user_voted,
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
    if user.role == "anon" and app.anonymous_access != "post_allowed":
        raise HTTPException(status_code=403, detail="Sign in to comment")

    post = await db.scalar(
        select(Post).where(Post.app_slug == app.slug, Post.post_number == number)
    )
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

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

    return RedirectResponse(url=_config.mount_prefix + "/", status_code=302)
