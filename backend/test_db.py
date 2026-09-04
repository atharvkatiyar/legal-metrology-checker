"""
test_db.py — Recreates compliance.db from the current SQLAlchemy models
and seeds one demo officer account so /login works immediately after
a schema wipe.

Run from the backend/ directory:
    python3 test_db.py
"""
import asyncio
import uuid

from app.core.database import engine, async_session_maker
from app.models.schema import Base, User


DEMO_OFFICER_ID = "DEMO-LMO-40812"
DEMO_PASSWORD = "demo1234"  # plaintext match against hashed_password, per current /login logic
DEMO_ROLE = "Government Field Officer"


async def recreate_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_demo_user() -> None:
    async with async_session_maker() as session:
        demo_user = User(
            id=uuid.uuid4(),
            officer_id=DEMO_OFFICER_ID,
            hashed_password=DEMO_PASSWORD,
            role=DEMO_ROLE,
            region="Sandbox City",
            is_active=True,
        )
        session.add(demo_user)
        await session.commit()
        print(f"Seeded demo user: officer_id={DEMO_OFFICER_ID!r} password={DEMO_PASSWORD!r} id={demo_user.id}")


async def main() -> None:
    await recreate_schema()
    print("Schema created (compliance.db).")
    await seed_demo_user()


if __name__ == "__main__":
    asyncio.run(main())