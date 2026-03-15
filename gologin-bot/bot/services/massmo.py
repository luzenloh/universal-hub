import asyncio
import logging
import re

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

MASSMO_URL = "https://massmo.io/"
BROWSER_LOAD_WAIT = 6  # seconds to wait for browsers to fully load after start


async def scrape_profiles(entries: list[tuple[str, str]]) -> list[dict]:
    """
    entries: list of (label, ws_url) e.g. [("M1", "ws://..."), ...]
    Returns list of result dicts.
    """
    await asyncio.sleep(BROWSER_LOAD_WAIT)

    async with async_playwright() as pw:
        tasks = [_scrape_one(pw, label, ws_url) for label, ws_url in entries]
        return list(await asyncio.gather(*tasks))


async def _scrape_one(pw, label: str, ws_url: str) -> dict:
    # NOTE: do not call browser.close() — that would kill the GoLogin profile
    try:
        browser = await pw.chromium.connect_over_cdp(ws_url)
        return await _extract(browser, label)
    except Exception as e:
        logger.error("Scrape failed for %s: %s", label, e)
        return {"label": label, "error": str(e)}


async def _extract(browser, label: str) -> dict:
    # Find MassMO tab or navigate to it
    page = None
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if "massmo.io" in pg.url:
                page = pg
                break
        if page:
            break

    if page is None:
        ctxs = browser.contexts
        ctx = ctxs[0] if ctxs else await browser.new_context()
        pages = ctx.pages
        page = pages[0] if pages else await ctx.new_page()
        await page.goto(MASSMO_URL, wait_until="domcontentloaded", timeout=30_000)

    try:
        await page.wait_for_load_state("networkidle", timeout=12_000)
    except Exception:
        pass  # proceed with whatever loaded

    text: str = await page.inner_text("body")
    text_lower = text.lower()

    if "нет активной заявки" in text_lower or "получить выплату" in text_lower:
        return {"label": label, "status": "free"}

    if "поиск выплаты" in text_lower or "идет поиск" in text_lower or "отменить поиск" in text_lower:
        return {"label": label, "status": "searching"}

    if "платеж не прошел" in text_lower or "проверьте корректность чека" in text_lower:
        result: dict = {"label": label, "status": "check_failed"}
        m = re.search(r"Переведите ровно[:\s]*([\d][\d\s]*RUB)", text)
        if m:
            result["amount"] = re.sub(r"\s+", " ", m.group(1)).strip()
        # amount from "Сумма выплаты X RUB" display card
        if "amount" not in result:
            m = re.search(r"Сумма выплаты\s+([\d][\d\s]*RUB)", text)
            if m:
                result["amount"] = re.sub(r"\s+", " ", m.group(1)).strip()
        return result

    if "заявка ожидает оплаты" not in text_lower:
        return {"label": label, "status": "unknown", "raw": text[:300]}

    result: dict = {"label": label, "status": "active"}

    # Expiry: "Истекает: 14.03.2026 13:03"
    m = re.search(r"Истекает[:\s]*([\d]{2}\.[\d]{2}\.[\d]{4}\s+[\d]{2}:[\d]{2})", text)
    if m:
        result["expires"] = m.group(1).strip()

    # Amount: "Переведите ровно: 5 000 RUB"
    m = re.search(r"Переведите ровно[:\s]*([\d][\d\s]*RUB)", text)
    if m:
        result["amount"] = re.sub(r"\s+", " ", m.group(1)).strip()

    # Bank name — line after "указанном банке:"
    m = re.search(r"указанном банке[:\s]*\n([^\n+\d][^\n]*)", text, re.IGNORECASE)
    if m:
        result["bank"] = m.group(1).strip()

    # Phone
    m = re.search(r"(\+7[\s\-\d]{9,})", text)
    if m:
        result["phone"] = re.sub(r"\s+", " ", m.group(1)).strip()

    # Exchange rate: "Курс выплаты (USDT/RUB): 81,74"
    m = re.search(r"Курс выплаты[^:]*:[^\d]*([\d,\.]+)", text)
    if m:
        result["rate"] = m.group(1)

    return result


def format_results(results: list[dict]) -> str:
    lines: list[str] = []
    for r in results:
        label = r.get("label", "?")
        if "error" in r:
            lines.append(f"<b>{label}</b>: ⚠️ ошибка подключения")
        elif r.get("status") == "free":
            lines.append(f"<b>{label}</b>: нет заявки")
        elif r.get("status") == "searching":
            lines.append(f"<b>{label}</b>: 🔍 поиск выплаты")
        elif r.get("status") == "check_failed":
            amount = r.get("amount", "—")
            lines.append(f"<b>{label}</b>: ⚠️ чек отклонён — {amount}")
        elif r.get("status") == "active":
            parts = [f"<b>{label}</b>: 💰 {r.get('amount', '—')}"]
            if "expires" in r:
                parts.append(f"  ⏰ до {r['expires']}")
            if "bank" in r:
                parts.append(f"  🏦 {r['bank']}")
            if "phone" in r:
                parts.append(f"  📞 {r['phone']}")
            if "rate" in r:
                parts.append(f"  📈 {r['rate']} USDT/RUB")
            lines.append("\n".join(parts))
        else:
            raw = r.get("raw", "")[:120].replace("\n", " ").strip()
            lines.append(f"<b>{label}</b>: ❓ не распознана — <code>{raw}</code>")

    return "\n\n".join(lines) if lines else "Нет данных"
