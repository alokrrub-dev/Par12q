from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BotSettings(Base):
    __tablename__ = "bot_settings"
    __table_args__ = (CheckConstraint("id = 1", name="settings_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    allowed_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class UserFilters(Base):
    __tablename__ = "user_filters"
    __table_args__ = (
        CheckConstraint("price_currency IN ('ANY', 'XTR', 'TON')", name="filter_currency"),
        CheckConstraint("max_price IS NULL OR max_price > 0", name="filter_positive_price"),
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 9), nullable=True)
    price_currency: Mapped[str] = mapped_column(String(3), default="ANY", nullable=False)
    selected_categories: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    parsing_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProcessedGift(Base):
    __tablename__ = "processed_gifts"
    # Unique collectible ID (StarGiftUnique.id), never the base category gift_id.
    gift_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    slug: Mapped[str] = mapped_column(String(256), nullable=False)
    recipient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(20), default="reserved", nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AlertedOwner(Base):
    __tablename__ = "alerted_owners"
    # Global owner history, independent of the gift/category/recipient.
    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    gift_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recipient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
