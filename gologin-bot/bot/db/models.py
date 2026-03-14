import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base


class Token(Base):
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy: Mapped[str | None] = mapped_column(Text, nullable=True)       # http://[user:pass@]host:port or socks5://...
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)  # custom User-Agent string
    is_free: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Folder(Base):
    """GoLogin folder (папка) — occupies the role of a 'shift slot'."""
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gologin_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # GoLogin folder UUID
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # ТМ глав profile id — None if not identified
    main_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON list of GoLogin profile IDs for numbered profiles M1...M15 (sorted)
    numbered_profile_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    profile_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Occupation state
    is_free: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    selected_count: Mapped[int | None] = mapped_column(Integer, nullable=True)  # how many M profiles were launched

    @property
    def numbered_ids(self) -> list[str]:
        return json.loads(self.numbered_profile_ids)
