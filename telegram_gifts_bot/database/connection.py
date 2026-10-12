from contextlib import asynccontextmanager
import ssl

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import AlertedOwner, Base, BotSettings, ProcessedGift, UserFilters


def database_arguments(dsn: str):
    url = make_url(dsn)
    if url.get_backend_name() not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL должен указывать на PostgreSQL")
    query = dict(url.query)
    mode = query.pop("sslmode", "require")
    query.pop("channel_binding", None)
    if mode not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("DATABASE_URL должен использовать TLS: sslmode=require/verify-full")
    # Verified TLS is stricter than sslmode=require and works with Neon certificates.
    arguments = {"ssl": ssl.create_default_context(), "command_timeout": 20,
                 "timeout": 20, "statement_cache_size": 0}
    return url.set(drivername="postgresql+asyncpg", query=query), arguments


class Database:
    def __init__(self, dsn: str):
        url, arguments = database_arguments(dsn)
        self.engine = create_async_engine(
            url, connect_args=arguments, pool_size=4, max_overflow=2,
            pool_pre_ping=True, pool_recycle=300,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self):
        async with self.engine.begin() as connection:
            # Serialize schema creation during overlapping deploys.
            await connection.execute(text("SELECT pg_advisory_xact_lock(71499317)"))
            await connection.run_sync(Base.metadata.create_all)
            # create_all does not alter an existing Render/Neon table after a deploy.
            await connection.execute(text(
                "ALTER TABLE user_filters ADD COLUMN IF NOT EXISTS "
                "parsing_enabled BOOLEAN NOT NULL DEFAULT FALSE"
            ))
        async with self.sessions.begin() as session:
            await session.execute(insert(BotSettings).values(id=1, revision=0)
                                  .on_conflict_do_nothing(index_elements=[BotSettings.id]))

    @asynccontextmanager
    async def locked_settings(self):
        async with self.sessions.begin() as session:
            settings = (await session.execute(
                select(BotSettings).where(BotSettings.id == 1).with_for_update()
            )).scalar_one()
            yield session, settings

    async def filters(self, session: AsyncSession, user_id: int) -> UserFilters:
        await session.execute(insert(UserFilters).values(
            user_id=user_id, price_currency="ANY", selected_categories=[], alerts_enabled=True,
        ).on_conflict_do_nothing(index_elements=[UserFilters.user_id]))
        return (await session.execute(select(UserFilters).where(
            UserFilters.user_id == user_id))).scalar_one()

    async def snapshot(self, admin_id: int):
        async with self.sessions.begin() as session:
            settings = await session.get(BotSettings, 1)
            user_id = settings.allowed_user_id or admin_id
            filters = await self.filters(session, user_id)
            return user_id, settings.revision, filters

    async def set_allowed_user(self, user_id: int | None):
        async with self.locked_settings() as (session, settings):
            settings.allowed_user_id = user_id
            settings.revision += 1
            return settings.revision

    async def access_status(self, admin_id: int):
        async with self.sessions() as session:
            settings = await session.get(BotSettings, 1)
            return settings.allowed_user_id, settings.revision

    async def update_filters(self, user_id: int, **values):
        async with self.sessions.begin() as session:
            filters = await self.filters(session, user_id)
            for key, value in values.items():
                setattr(filters, key, value)

    async def known(self, gift_ids: list[int]) -> set[int]:
        if not gift_ids:
            return set()
        async with self.sessions() as session:
            result = await session.scalars(select(ProcessedGift.gift_id).where(
                ProcessedGift.gift_id.in_(gift_ids)))
            return set(result)

    async def recent_ids(self) -> list[int]:
        async with self.sessions() as session:
            result = await session.scalars(select(ProcessedGift.gift_id).order_by(
                ProcessedGift.detected_at.desc()).limit(2000))
            return list(result)

    async def claim(self, gift, recipient_id: int, status: str = "reserved") -> bool:
        # Separate transaction: durable reservation MUST commit before sendMessage.
        async with self.sessions.begin() as session:
            result = await session.execute(insert(ProcessedGift).values(
                gift_id=gift.id, category_id=gift.category_id, slug=gift.slug,
                recipient_id=recipient_id, status=status,
            ).on_conflict_do_nothing(index_elements=[ProcessedGift.gift_id])
              .returning(ProcessedGift.gift_id))
            return result.scalar_one_or_none() is not None

    async def claim_many(self, gifts, recipient_id: int, status: str = "baseline"):
        rows = [{
            "gift_id": gift.id,
            "category_id": gift.category_id,
            "slug": gift.slug,
            "recipient_id": recipient_id,
            "status": status,
        } for gift in gifts]
        if not rows:
            return
        async with self.sessions.begin() as session:
            await session.execute(insert(ProcessedGift).values(rows)
                                  .on_conflict_do_nothing(index_elements=[ProcessedGift.gift_id]))

    async def claim_owner(self, owner_id: int, gift_id: int, recipient_id: int) -> bool:
        # Commit before Telegram sendMessage. The primary key prevents two workers
        # or restarts from sending different gifts belonging to the same owner.
        async with self.sessions.begin() as session:
            result = await session.execute(insert(AlertedOwner).values(
                owner_id=owner_id, gift_id=gift_id, recipient_id=recipient_id,
            ).on_conflict_do_nothing(index_elements=[AlertedOwner.owner_id])
              .returning(AlertedOwner.owner_id))
            return result.scalar_one_or_none() is not None

    async def finish(self, gift_id: int, status: str, message_id: int | None = None):
        async with self.sessions.begin() as session:
            await session.execute(update(ProcessedGift).where(ProcessedGift.gift_id == gift_id)
                                  .values(status=status, message_id=message_id))

    async def close(self):
        await self.engine.dispose()
