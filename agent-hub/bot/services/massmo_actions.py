"""
Playwright UI automation for massmo.io.

Architecture:
- UI interactions (payout flow, receipts): Playwright semantic locators
- Settings/limits/banks: direct REST API calls to findssnet.io
  Auth: JWT token from browser localStorage['token']
  Base: https://findssnet.io/api/massmo/v1/
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx
from playwright.async_api import Browser, Page

from web.models.schemas import PayoutData, WindowStatus

logger = logging.getLogger(__name__)

MASSMO_URL = "https://massmo.io/"
_API_BASE = "https://findssnet.io/api/massmo/v1"

# How long to wait after a UI interaction before assuming it saved
_UI_SETTLE = 0.3


# ------------------------------------------------------------------ page init

async def find_or_open_massmo_page(browser: Browser) -> Page:
    """Find the massmo.io tab or open a new one."""
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if "massmo.io" in pg.url:
                return pg

    ctxs = browser.contexts
    ctx = ctxs[0] if ctxs else await browser.new_context()
    pages = ctx.pages
    page = pages[0] if pages else await ctx.new_page()
    await page.goto(MASSMO_URL, wait_until="domcontentloaded", timeout=30_000)
    return page


# ------------------------------------------------------------------ state detection

async def detect_state(page: Page, text: str | None = None) -> WindowStatus:
    """Detect window state from the current massmo.io page text."""
    if text is None:
        text = await page.inner_text("body")
    text_lower = text.lower()

    # PAID — заявка оплачена, платёж прошёл проверку
    if "заявка оплачена" in text_lower or "платеж прошел проверку" in text_lower:
        return WindowStatus.PAID

    # ACTIVE_PAYOUT — highest priority
    if "заявка ожидает оплаты" in text_lower or "платеж не прошел" in text_lower or "проверьте корректность чека" in text_lower:
        return WindowStatus.ACTIVE_PAYOUT

    # DISABLED — payouter turned off on MassMO side
    if "is disabled" in text_lower or "payouter" in text_lower:
        return WindowStatus.DISABLED

    # SEARCHING — checked BEFORE idle because "получить выплату" may appear in nav during search
    if "отменить поиск" in text_lower or "идет поиск" in text_lower or "поиск выплаты" in text_lower:
        return WindowStatus.SEARCHING

    # IDLE
    if "нет активной заявки" in text_lower or "получить выплату" in text_lower:
        return WindowStatus.IDLE

    # Generic fallback
    if "поиск" in text_lower or "ожидание" in text_lower:
        return WindowStatus.SEARCHING

    return WindowStatus.SEARCHING


async def extract_payout_data(page: Page, text: str | None = None) -> PayoutData:
    """Extract payout details from an ACTIVE_PAYOUT page."""
    if text is None:
        text = await page.inner_text("body")

    amount: str | None = None
    bank: str | None = None
    recipient: str | None = None
    timer: str | None = None
    rate: str | None = None

    m = re.search(r"Переведите ровно[:\s]*([\d][\d\s]*RUB)", text)
    if m:
        amount = re.sub(r"\s+", " ", m.group(1)).strip()

    m = re.search(r"указанном банке[:\s]*\n([^\n+\d][^\n]*)", text, re.IGNORECASE)
    if m:
        bank = m.group(1).strip()

    # Phone fallback
    if not recipient:
        m = re.search(r"(\+7[\s\-\d]{9,})", text)
        if m:
            recipient = re.sub(r"\s+", " ", m.group(1)).strip()

    # Card number fallback
    if not recipient:
        m = re.search(r"(\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{4})", text)
        if m:
            recipient = m.group(1).strip()

    # Timer
    m = re.search(r"Истекает[:\s]*([\d]{2}\.[\d]{2}\.[\d]{4}\s+[\d]{2}:[\d]{2})", text)
    if m:
        timer = m.group(1).strip()

    # Rate
    m = re.search(r"Курс выплаты[^:]*:[^\d]*([\d,\.]+)", text)
    if m:
        rate = m.group(1)

    return PayoutData(amount=amount, bank=bank, recipient=recipient, timer=timer, rate=rate)


async def extract_limits(page: Page, text: str | None = None) -> tuple[int | None, int | None]:
    """Read current min/max limit values from the page text (displayed as cards)."""
    try:
        if text is None:
            text = await page.inner_text("body")
        # Normalize: replace non-breaking spaces with regular spaces
        text = text.replace("\xa0", " ")
        # Case-insensitive for ASCII part; Cyrillic written explicitly in both cases
        min_m = re.search(r"[МмMm][ИиIi][НнNn]\.?\s*сумма выплаты\s*([\d\s]+)\s*RUB", text)
        max_m = re.search(r"[МмMm][АаAa][КкKk][СсSs]\.?\s*сумма выплаты\s*([\d\s]+)\s*RUB", text)
        min_int = int(re.sub(r"\s+", "", min_m.group(1))) if min_m else None
        max_int = int(re.sub(r"\s+", "", max_m.group(1))) if max_m else None
        return min_int, max_int
    except Exception:
        logger.debug("extract_limits failed: %s", text[:200] if text else "no text")
        return None, None


# ------------------------------------------------------------------ API helpers

async def _get_token(page: Page) -> str:
    """Get JWT auth token from browser localStorage."""
    token = await page.evaluate("() => localStorage.getItem('token')")
    if not token:
        raise RuntimeError("MassMO auth token not found in localStorage")
    return token


async def _patch_executor(page: Page, **fields) -> dict:
    """
    PATCH https://findssnet.io/api/massmo/v1/executor with given fields.
    Returns updated executor data dict.
    """
    token = await _get_token(page)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.patch(f"{_API_BASE}/executor", headers=headers, json=fields)
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "ok":
            raise RuntimeError(f"API error: {body.get('errors') or body.get('message')}")
        return body["data"]


# ------------------------------------------------------------------ payout UI (semantic locators)

async def click_request_payout(page: Page) -> None:
    """Click 'Получить выплату' or 'Перейти к новой заявке' button."""
    btn = page.get_by_role("button", name=re.compile(r"получить выплату", re.I))
    if await btn.count():
        await btn.first.click()
        await asyncio.sleep(_UI_SETTLE)
        return
    # After successful payment — "Перейти к новой заявке"
    btn = page.get_by_role("button", name=re.compile(r"перейти к новой заявке", re.I))
    if not await btn.count():
        btn = page.get_by_text(re.compile(r"перейти к новой заявке", re.I))
    await btn.first.click()
    await asyncio.sleep(_UI_SETTLE)


async def cancel_payout(page: Page) -> None:
    """Click 'Отменить выплату' / 'Отменить поиск' button."""
    btn = page.get_by_role("button", name=re.compile(r"отменить", re.I))
    await btn.first.click()
    await asyncio.sleep(_UI_SETTLE)


async def upload_receipt(page: Page, file_path: str) -> None:
    """Upload a PDF receipt via the file input (bypasses OS picker)."""
    file_input = page.locator("input[type='file']").first
    await file_input.set_input_files(file_path)
    await asyncio.sleep(_UI_SETTLE)


# ------------------------------------------------------------------ limits (API)

async def update_limits(page: Page, new_min: int, new_max: int) -> None:
    """
    Update min/max payout limits via findssnet.io API.
    No navigation required — direct REST call with JWT from localStorage.
    """
    data = await _patch_executor(page, min_amount=new_min, max_amount=new_max)
    logger.info(
        "Limits updated: %d–%d (API returned min=%s max=%s)",
        new_min, new_max, data.get("min_amount"), data.get("max_amount"),
    )


# ------------------------------------------------------------------ banks (API)

# Maps dashboard bank keys → findssnet.io executor alias
_BANK_ALIAS: dict[str, str] = {
    "tinkoff":   "tinkoff",
    "тинькофф":  "tinkoff",
    "tink":      "tinkoff",
    "sber":      "sberbank",
    "сбер":      "sberbank",
    "сбербанк":  "sberbank",
    "alfa":      "alfa_bank",
    "альфа":     "alfa_bank",
    "vtb":       "vtb",
    "втб":       "vtb",
}


async def select_bank(page: Page, bank_name: str) -> None:
    """
    Filter incoming payout orders by bank via findssnet.io API.
    Sets executor bank_names to [alias] — pass empty string to clear filter.
    """
    key = bank_name.lower().strip()
    alias = _BANK_ALIAS.get(key)
    if not alias:
        raise ValueError(f"Unknown bank: {bank_name}. Known: {list(_BANK_ALIAS.keys())}")

    data = await _patch_executor(page, bank_names=[alias])
    logger.info("Bank filter set to %s (returned: %s)", alias, data.get("bank_names"))


# ------------------------------------------------------------------ toggles (API)

# Maps setting name → executor API field
_TOGGLE_FIELD: dict[str, str] = {
    "phone":    "accepts_sbp",
    "телефон":  "accepts_sbp",
    "card":     "accepts_card_to_card",
    "карта":    "accepts_card_to_card",
    "account":  "account_number_transfer_enabled",
    "счёт":     "account_number_transfer_enabled",
}


async def toggle_setting(page: Page, setting: str, enabled: bool) -> None:
    """
    Toggle phone/card/account setting via findssnet.io API.
    phone   → accepts_sbp
    card    → accepts_card_to_card
    account → account_number_transfer_enabled
    """
    field = _TOGGLE_FIELD.get(setting.lower().strip())
    if not field:
        raise ValueError(f"Unknown setting: {setting}. Known: {list(_TOGGLE_FIELD.keys())}")

    data = await _patch_executor(page, **{field: enabled})
    logger.info("Toggle %s=%s (API returned %s=%s)", setting, enabled, field, data.get(field))


# ------------------------------------------------------------------ resource blocking

_BLOCKED_TYPES = {"image", "media", "font", "stylesheet"}


async def setup_resource_blocking(page: Page) -> None:
    """Block heavy resources to reduce memory/CPU per profile tab."""
    async def _handler(route, request):
        if request.resource_type in _BLOCKED_TYPES:
            await route.abort()
        else:
            await route.continue_()

    try:
        await page.route("**/*", _handler)
        logger.info("Resource blocking enabled for %s", page.url)
    except Exception as exc:
        logger.warning("Failed to set up resource blocking: %s", exc)


# ------------------------------------------------------------------ API-based state polling

async def get_state_from_api(token: str) -> tuple[WindowStatus, int | None, int | None]:
    """
    Poll executor state via REST API (no DOM required).
    Returns (status, min_limit, max_limit).
    Raises RuntimeError("token_expired") on 401.
    """
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{_API_BASE}/executor", headers=headers)
        if r.status_code == 401:
            raise RuntimeError("token_expired")
        r.raise_for_status()
        data: dict = (r.json().get("data") or {})

    executor_state = data.get("state", "")
    payout_state = (data.get("payout_state") or "").lower()
    min_limit = data.get("min_amount")
    max_limit = data.get("max_amount")

    if executor_state == "disabled":
        return WindowStatus.DISABLED, min_limit, max_limit
    if "search" in payout_state:
        return WindowStatus.SEARCHING, min_limit, max_limit
    if payout_state == "idle":
        return WindowStatus.IDLE, min_limit, max_limit

    # Unknown payout_state — log at WARNING so it's visible, treat as SEARCHING
    logger.warning("Unknown executor payout_state=%r (state=%r) — treating as SEARCHING", payout_state, executor_state)
    return WindowStatus.SEARCHING, min_limit, max_limit


async def get_active_order(token: str) -> tuple[WindowStatus | None, PayoutData | None]:
    """
    Check for an active payout order via REST API.
    Returns (ACTIVE_PAYOUT/PAID, PayoutData) if order exists, or (None, None).
    Raises RuntimeError("token_expired") on 401.
    """
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{_API_BASE}/active", headers=headers)
        if r.status_code == 401:
            raise RuntimeError("token_expired")
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        body = r.json()

    if body.get("status") == "not_found" or not body.get("data"):
        return None, None

    data: dict = body["data"]
    logger.debug("Active order data: %s", data)

    # Determine order status
    order_status_raw = (data.get("status") or data.get("state") or "").lower()
    if "paid" in order_status_raw or "success" in order_status_raw:
        win_status = WindowStatus.PAID
    else:
        win_status = WindowStatus.ACTIVE_PAYOUT

    # Map API fields → PayoutData (try common field names)
    def _first(*keys: str) -> str | None:
        for k in keys:
            v = data.get(k)
            if v is not None:
                return str(v)
        return None

    amount = _first("amount", "payout_amount", "sum", "payment_amount")
    bank = _first("bank_name", "bank", "bank_title", "payment_method")
    recipient = _first("card_number", "phone", "account_number", "requisite", "wallet")
    timer = _first("expired_at", "expires_at", "deadline", "expire_at")
    rate = _first("rate", "exchange_rate", "course")

    return win_status, PayoutData(amount=amount, bank=bank, recipient=recipient, timer=timer, rate=rate)


# ------------------------------------------------------------------ misc

async def open_url_in_browser(ws_url: str, url: str) -> None:
    """Open a new tab at *url* inside an already-running GoLogin browser (CDP attach)."""
    from playwright.async_api import async_playwright

    try:
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(ws_url)
            ctxs = browser.contexts
            ctx = ctxs[0] if ctxs else await browser.new_context()
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            logger.info("Opened %s in browser via CDP", url)
        finally:
            await pw.stop()
    except Exception as exc:
        logger.warning("Failed to open %s in browser: %s", url, exc)


async def extract_jwt(ws_url: str) -> str | None:
    """CDP: connect to an open M-profile, read JWT from MassMO localStorage."""
    from playwright.async_api import async_playwright

    try:
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(ws_url)
            page = await find_or_open_massmo_page(browser)
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            token = await page.evaluate("localStorage.getItem('token')")
            return token or None
        finally:
            await pw.stop()  # disconnect CDP — does NOT close the GoLogin browser
    except Exception as exc:
        logger.warning("extract_jwt failed: %s", exc)
        return None
