from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class WindowStatus(str, Enum):
    CONNECTING = "CONNECTING"
    IDLE = "IDLE"
    SEARCHING = "SEARCHING"
    ACTIVE_PAYOUT = "ACTIVE_PAYOUT"
    DISABLED = "DISABLED"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class PayoutData(BaseModel):
    amount: str | None = None      # "50 000 RUB"
    bank: str | None = None        # "Tinkoff"
    recipient: str | None = None   # phone or card number
    timer: str | None = None       # "14.03.2026 13:03"
    rate: str | None = None        # "81,74"


class WindowState(BaseModel):
    window_id: str          # GoLogin profile ID
    label: str              # "M1", "M2"...
    status: WindowStatus
    payout: PayoutData | None = None
    error_msg: str | None = None
    last_updated: float
    min_limit: int | None = None
    max_limit: int | None = None


class CommandType(str, Enum):
    REQUEST_PAYOUT = "REQUEST_PAYOUT"
    UPLOAD_RECEIPT = "UPLOAD_RECEIPT"
    SELECT_BANK = "SELECT_BANK"
    CANCEL_PAYOUT = "CANCEL_PAYOUT"
    UPDATE_LIMITS = "UPDATE_LIMITS"
    TOGGLE_SETTING = "TOGGLE_SETTING"
    REFRESH_STATE = "REFRESH_STATE"


class CommandRequest(BaseModel):
    type: CommandType
    params: dict[str, Any] = {}


class StartSessionRequest(BaseModel):
    token_hash: str      # GoLogin folder gologin_id (UUID)
    profile_count: int   # 1–15


class WSEvent(BaseModel):
    event: str
    windows: list[WindowState] | None = None
    window: WindowState | None = None
    message: str | None = None


class CommandResult(BaseModel):
    success: bool
    message: str = ""
