from __future__ import annotations
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
AsyncEngine,
AsyncSession,
async_sessionmaker,
create_async_engine,
)
from app.core.config import settings

engine: AsyncEngine = create_async_engine(
settings.DATABASE_URL,
echo=False,
future=True,
)
async_session_maker = async_sessionmaker(
bind=engine,
class_=AsyncSession,
expire_on_commit=False,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
	async with async_session_maker() as session:
		try:
			yield session
		finally:
			await session.close()