import json
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column


from hub.db.base import Base


class Folder(Base):
    """GoLogin folder (папка) — occupies the role of a 'shift slot'."""
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gologin_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    main_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    numbered_profile_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    profile_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    massmo_secrets: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # Occupation state
    is_free: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    selected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_agent_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def numbered_ids(self) -> list[str]:
        return json.loads(self.numbered_profile_ids)

    @property
    def massmo_secrets_list(self) -> list[str]:
        return json.loads(self.massmo_secrets)


class User(Base):
    """Registered Telegram user (auto-created on /start)."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Schedule(Base):
    """Weekly shift schedule submitted by a user."""
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    # JSON: {"2026-03-24": {"shift": "day"|"night"|"off", "direction": "pay_out"|"pay_in"|"matching"|null}, ...}
    days: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    submitted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("telegram_id", "week_start", name="uq_schedule_user_week"),)

    @property
    def days_dict(self) -> dict:
        return json.loads(self.days)


class Agent(Base):
    """Registered local agent instance."""
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    public_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    local_url: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_folder_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notify_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_telegram_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Pinned Telegram message (Feature 5)
    pinned_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pinned_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Session statistics (Feature 6)
    last_payout_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    session_payout_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_payout_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    searching_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AgentSetupToken(Base):
    """One-time setup token that lets an agent installer claim its .env.agent config."""
    __tablename__ = "agent_setup_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 32-char random hex — acts as the opaque secret shared with the installer
    jti: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    owner_telegram_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
