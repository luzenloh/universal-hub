"""
Playwright UI automation for massmo.io.
Extends the regex patterns from massmo.py.
All functions receive a Playwright Page object.
"""
from __future__ import annotations

import asyncio
import logging
import re

from playwright.async_api import Browser, Page

from web.models.schemas import PayoutData, WindowStatus

logger = logging.getLogger(__name__)

MASSMO_URL = "https://massmo.io/"

# How long to wait after a UI interaction before assuming it saved
_UI_SETTLE = 0.3


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


async def detect_state(page: Page) -> WindowStatus:
    """Detect window state from the current massmo.io page text."""
    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass

    text: str = await page.inner_text("body")
    text_lower = text.lower()

    if "нет активной заявки" in text_lower or "получить выплату" in text_lower:
        return WindowStatus.IDLE
    if "заявка ожидает оплаты" in text_lower:
        return WindowStatus.ACTIVE_PAYOUT
    if "поиск" in text_lower or "ожидание" in text_lower:
        return WindowStatus.SEARCHING
    # If we see payout data without "ожидает оплаты" text — treat as searching
    return WindowStatus.SEARCHING


async def extract_payout_data(page: Page) -> PayoutData:
    """Extract payout details from an ACTIVE_PAYOUT page."""
    text: str = await page.inner_text("body")

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

    # Try CSS selectors for cleaner extraction
    try:
        amount_el = page.locator(".amount-text").first
        if await amount_el.count() > 0:
            amount = (await amount_el.inner_text()).strip()
    except Exception:
        pass

    try:
        bank_el = page.locator(".bank-name").first
        if await bank_el.count() > 0:
            bank = (await bank_el.inner_text()).strip()
    except Exception:
        pass

    try:
        card_el = page.locator(".card-number").first
        if await card_el.count() > 0:
            recipient = (await card_el.inner_text()).strip()
    except Exception:
        pass

    # Phone fallback
    if not recipient:
        m = re.search(r"(\+7[\s\-\d]{9,})", text)
        if m:
            recipient = re.sub(r"\s+", " ", m.group(1)).strip()

    # Timer
    m = re.search(r"Истекает[:\s]*([\d]{2}\.[\d]{2}\.[\d]{4}\s+[\d]{2}:[\d]{2})", text)
    if m:
        timer = m.group(1).strip()

    # Rate
    m = re.search(r"Курс выплаты[^:]*:[^\d]*([\d,\.]+)", text)
    if m:
        rate = m.group(1)

    return PayoutData(amount=amount, bank=bank, recipient=recipient, timer=timer, rate=rate)


async def click_request_payout(page: Page) -> None:
    """Click 'Получить выплату' button to start searching for a payout."""
    btn = page.get_by_role("button", name="Получить выплату")
    await btn.click()
    await asyncio.sleep(_UI_SETTLE)


async def cancel_payout(page: Page) -> None:
    """Click 'Отменить выплату' button."""
    btn = page.get_by_role("button", name="Отменить выплату")
    await btn.click()
    await asyncio.sleep(_UI_SETTLE)


_BANK_CHIP_MAP: dict[str, str] = {
    "tinkoff": ".bank-chip.tinkoff",
    "sber": ".bank-chip.sber",
    "sberbank": ".bank-chip.sber",
    "alfa": ".bank-chip.alfa",
    "альфа": ".bank-chip.alfa",
    "vtb": ".bank-chip.vtb",
    "втб": ".bank-chip.vtb",
}


async def select_bank(page: Page, bank_name: str) -> None:
    """Click the bank chip for the specified bank."""
    key = bank_name.lower().strip()
    selector = _BANK_CHIP_MAP.get(key)
    if not selector:
        # Try partial match
        for k, v in _BANK_CHIP_MAP.items():
            if k in key or key in k:
                selector = v
                break
    if not selector:
        raise ValueError(f"Unknown bank: {bank_name}")
    await page.click(selector)
    await asyncio.sleep(_UI_SETTLE)


async def upload_receipt(page: Page, file_path: str) -> None:
    """Upload a PDF receipt via the file input (bypasses OS picker)."""
    file_input = page.locator("input[type='file']").first
    await file_input.set_input_files(file_path)
    await asyncio.sleep(_UI_SETTLE)


async def _set_input_field(page: Page, selector: str, value: str) -> None:
    """Click → select all → fill → Tab to trigger onBlur save."""
    field = page.locator(selector).first
    await field.click()
    await page.keyboard.press("Control+A")
    await field.fill(value)
    await page.keyboard.press("Tab")
    await asyncio.sleep(_UI_SETTLE)


async def update_limits(page: Page, new_min: int, new_max: int) -> None:
    """
    Update min/max limits safely.
    Invariant: max > min must hold at all times.

    If new_max >= current_max: update max first, then min.
    Otherwise: update min first, then max.
    """
    # Read current values
    min_field = page.locator("input[name='min']").first
    max_field = page.locator("input[name='max']").first

    try:
        current_min = int(await min_field.input_value())
        current_max = int(await max_field.input_value())
    except Exception:
        current_min = 0
        current_max = 9999999

    if new_max >= current_max:
        # Safe to update max first
        await _set_input_field(page, "input[name='max']", str(new_max))
        await _set_input_field(page, "input[name='min']", str(new_min))
    else:
        # Update min first
        await _set_input_field(page, "input[name='min']", str(new_min))
        await _set_input_field(page, "input[name='max']", str(new_max))

    logger.info("Limits updated: %d–%d (was %d–%d)", new_min, new_max, current_min, current_max)


_TOGGLE_SELECTOR_MAP: dict[str, str] = {
    "phone": "label:has-text('телефон') input",
    "телефон": "label:has-text('телефон') input",
    "card": "label:has-text('карта') input",
    "карта": "label:has-text('карта') input",
    "account": "label:has-text('счёт') input",
    "счёт": "label:has-text('счёт') input",
}


async def toggle_setting(page: Page, setting: str, enabled: bool) -> None:
    """Toggle phone/card/account setting."""
    key = setting.lower().strip()
    selector = _TOGGLE_SELECTOR_MAP.get(key)
    if not selector:
        raise ValueError(f"Unknown setting: {setting}")

    checkbox = page.locator(selector).first
    is_checked = await checkbox.is_checked()
    if is_checked != enabled:
        await checkbox.click()
        await asyncio.sleep(_UI_SETTLE)


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
            # pw.stop() disconnects playwright without closing the browser process
            await pw.stop()
    except Exception as exc:
        logger.warning("Failed to open %s in browser: %s", url, exc)
