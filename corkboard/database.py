from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

engine = None
async_session_factory = None


class Base(DeclarativeBase):
    pass


async def init_db(database_url: str):
    global engine, async_session_factory
    engine = create_async_engine(database_url, echo=False)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    from corkboard import models  # noqa: F401 — ensure all models are registered

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add columns that may not exist on older databases
        await _add_column_if_missing(conn, "posts", "blocked_by", "INTEGER")


async def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    """SQLite-safe: add a column if it doesn't already exist."""
    from sqlalchemy import text
    cols = await conn.execute(text(f"PRAGMA table_info({table})"))
    existing = {row[1] for row in cols}
    if column not in existing:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))


async def get_db():
    async with async_session_factory() as session:
        yield session
