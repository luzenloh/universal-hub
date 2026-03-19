from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel


class WindowStatus(str, Enum):
    CONNECTING = "CONNECTING"
    IDLE = "IDLE"
    SEARCHING = "SEARCHING"
    ACTIVE_PAYOUT = "ACTIVE_PAYOUT"
    EXPIRING = "EXPIRING"                  # таймер истекает, доступно продление
    VERIFICATION = "VERIFICATION"          # чек загружен, ожидает проверки
    VERIFICATION_FAILED = "VERIFICATION_FAILED"  # чек не прошёл проверку
    PAID = "PAID"          # заявка оплачена, ожидает перехода к новой
    DISABLED = "DISABLED"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class PayoutData(BaseModel):
    amount: Optional[str] = None      # "50 000 RUB"
    bank: Optional[str] = None        # "Tinkoff"  — receiver bank
    recipient: Optional[str] = None   # phone or card number
    timer: Optional[str] = None       # "2026-03-19 15:21:13 +0300"
    rate: Optional[str] = None        # "81,74"
    sender_bank: Optional[str] = None # operator's sender bank alias (set after payment)
    order_id: Optional[str] = None    # MassMO payout UUID
    can_prolong: bool = False          # extension available right now
    attempts_left: Optional[int] = None  # how many extensions remain


class WindowState(BaseModel):
    window_id: str          # GoLogin profile ID
    label: str              # "M1", "M2"...
    status: WindowStatus
    payout: Optional[PayoutData] = None
    error_msg: Optional[str] = None
    last_updated: float
    min_limit: Optional[int] = None
    max_limit: Optional[int] = None


class CommandType(str, Enum):
    REQUEST_PAYOUT = "REQUEST_PAYOUT"
    UPLOAD_RECEIPT = "UPLOAD_RECEIPT"
    SELECT_BANK = "SELECT_BANK"
    SELECT_SENDER_BANK = "SELECT_SENDER_BANK"
    CANCEL_PAYOUT = "CANCEL_PAYOUT"
    UPDATE_LIMITS = "UPDATE_LIMITS"
    TOGGLE_SETTING = "TOGGLE_SETTING"
    REFRESH_STATE = "REFRESH_STATE"
    EXTEND_ORDER = "EXTEND_ORDER"


class CommandRequest(BaseModel):
    type: CommandType
    params: dict[str, Any] = {}


class StartSessionRequest(BaseModel):
    token_hash: str      # GoLogin folder gologin_id (UUID)
    profile_count: int   # 1–15


class ConnectEntry(BaseModel):
    label: str           # "M1", "M2", ...
    secret: str          # MassMO secret token


class ConnectRequest(BaseModel):
    profiles: list[ConnectEntry]


class WSEvent(BaseModel):
    event: str
    windows: Optional[list[WindowState]] = None
    window: Optional[WindowState] = None
    message: Optional[str] = None


class CommandResult(BaseModel):
    success: bool
    message: str = ""


# ── Hub ↔ Agent protocol schemas ──────────────────────────────────────────────

class InboundPlatformState(BaseModel):
    name: str                       # "payfast" | "montera"
    order_id: Optional[str] = None
    status: str                     # "posting" | "live" | "payment_incoming" | "cancelled" | "error"


class InboundState(BaseModel):
    window_id: str
    status: str                     # InboundController.status value
    platforms: list[InboundPlatformState]


class AgentStartRequest(BaseModel):
    """Hub → Agent: start a shift on this agent."""
    folder_gologin_id: str
    folder_name: str
    main_profile_id: str
    numbered_profile_ids: list[str]
    massmo_secrets: Union[list[str], dict[str, Any]]  # list[str] legacy OR dict with payfast/montera keys
    count: int
    notify_chat_id: int


class AgentStatus(BaseModel):
    """Agent → Hub: current session state."""
    active: bool
    windows: list[WindowState]


class RegisterPayload(BaseModel):
    """Agent → Hub: announce agent URL on startup."""
    agent_id: str
    public_url: str
    local_url: str
    owner_telegram_id: Optional[int] = None


class HeartbeatPayload(BaseModel):
    """Agent → Hub: periodic heartbeat with window states."""
    agent_id: str
    windows: list[WindowState]
