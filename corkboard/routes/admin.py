"""Admin routes — moderation dashboard."""
import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from corkboard.database import get_db
from corkboard.config import CorkboardConfig
from corkboard.auth import RequestUser, get_current_user
from corkboard.scrub import mask_author
from corkboard.models import Post, Comment

router = APIRouter(prefix="/admin")
_config: CorkboardConfig = None
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def init_admin_routes(config: CorkboardConfig):
    global _config
    _config = config
    templates.env.globals["mask_author"] = mask_author


def _require_admin(user: RequestUser):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


def _get_app(request: Request):
    host = request.headers.get("host", "")
    app = _config.app_for_domain(host)
    if app is None:
        raise HTTPException(status_code=404, detail="Unknown app")
    return app


@router.get("/")
async def admin_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    _require_admin(user)
    app = _get_app(request)

    # Recent posts (including soft-deleted, for moderation)
    posts_stmt = (
        select(Post)
        .where(Post.app_slug == app.slug)
        .order_by(Post.created_at.desc())
        .limit(50)
    )
    result = await db.execute(posts_stmt)
    posts = result.scalars().all()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "app": app,
        "user": user,
        "prefix": _config.mount_prefix,
        "posts": posts,
    })


@router.post("/post/{number}/delete")
async def admin_delete_post(
    request: Request,
    number: int,
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    _require_admin(user)
    app = _get_app(request)

    post = await db.scalar(
        select(Post).where(Post.app_slug == app.slug, Post.post_number == number)
    )
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    post.deleted_at = datetime.datetime.utcnow()
    await db.commit()

    return RedirectResponse(
        url=f"{_config.mount_prefix}/admin/", status_code=302,
    )


@router.post("/comment/{comment_id}/delete")
async def admin_delete_comment(
    request: Request,
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_current_user),
):
    _require_admin(user)

    comment = await db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")

    comment.deleted_at = datetime.datetime.utcnow()
    await db.commit()

    # Redirect back to the post
    post = await db.get(Post, comment.post_id)
    return RedirectResponse(
        url=f"{_config.mount_prefix}/post/{post.post_number}" if post else f"{_config.mount_prefix}/admin/",
        status_code=302,
    )
