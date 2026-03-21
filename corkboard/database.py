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


async def get_db():
    async with async_session_factory() as session:
        yield session
