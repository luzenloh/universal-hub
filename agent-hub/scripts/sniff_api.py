"""
One-shot script: connects to a GoLogin profile, intercepts all findssnet.io
network requests, performs login + payout flow, prints captured API calls.

Usage:
    python3 sniff_api.py
"""
import asyncio
import json
import sys

import httpx
from playwright.async_api import async_playwright

GOLOGIN_LOCAL = "http://localhost:36912"
MASSMO_URL = "https://massmo.io/"
FINDSSNET = "findssnet.io"

# First profile from token 13 that's likely to connect
PROFILE_ID = "67e795cb734526868e4632e3"


async def start_profile(profile_id: str) -> str | None:
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.post(
                f"{GOLOGIN_LOCAL}/browser/start-profile",
                json={"profileId": profile_id, "sync": True},
            )
            data = r.json()
            ws = data.get("wsUrl") or ""
            if not ws:
                # Already running — stop and restart
                await client.post(f"{GOLOGIN_LOCAL}/browser/stop-profile", json={"profileId": profile_id})
                await asyncio.sleep(5)
                r = await client.post(
                    f"{GOLOGIN_LOCAL}/browser/start-profile",
                    json={"profileId": profile_id, "sync": True},
                )
                ws = r.json().get("wsUrl") or ""
            return ws or None
        except Exception as e:
            print(f"[GoLogin] Failed: {e}")
            return None


async def sniff(ws_url: str) -> None:
    captured: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(ws_url)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()

        # Find or open massmo page
        page = None
        for p in ctx.pages:
            if "massmo.io" in p.url:
                page = p
                break
        if page is None:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(MASSMO_URL, wait_until="domcontentloaded", timeout=30_000)

        # ── Intercept all findssnet.io requests ──────────────────────────────
        response_bodies: dict[str, str] = {}

        async def on_request(request):
            if FINDSSNET not in request.url:
                return
            body = ""
            try:
                body = request.post_data or ""
            except Exception:
                pass
            print(f"\n→ {request.method} {request.url}")
            if body:
                print(f"   body: {body[:500]}")

        async def on_response(response):
            if FINDSSNET not in response.url:
                return
            try:
                body = await response.text()
            except Exception:
                body = "<unreadable>"
            print(f"← {response.status} {response.url}")
            print(f"   resp: {body[:500]}")
            captured.append({
                "url": response.url,
                "status": response.status,
                "body": body[:2000],
            })

        page.on("request", on_request)
        page.on("response", on_response)

        # ── Get JWT from localStorage ─────────────────────────────────────────
        token = await page.evaluate("() => localStorage.getItem('token')")
        print(f"\n[JWT] token present: {bool(token)}")

        # ── Capture login endpoint by triggering a fresh login ────────────────
        print("\n[*] Navigating to massmo.io to capture initial API calls...")
        await page.goto(MASSMO_URL, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(2)

        # ── Click 'Получить выплату' to capture start-search endpoint ─────────
        print("\n[*] Clicking 'Получить выплату'...")
        try:
            import re
            btn = page.get_by_role("button", name=re.compile(r"получить выплату", re.I))
            if await btn.count():
                await btn.first.click()
                await asyncio.sleep(3)
                print("[*] Clicked. Waiting for search to start...")
            else:
                print("[!] Button not found — page state might not be IDLE")
        except Exception as e:
            print(f"[!] Click error: {e}")

        # ── Click 'Отменить' to capture dequeue endpoint ──────────────────────
        await asyncio.sleep(2)
        print("\n[*] Clicking 'Отменить поиск'...")
        try:
            btn_cancel = page.get_by_role("button", name=re.compile(r"отменить", re.I))
            if await btn_cancel.count():
                await btn_cancel.first.click()
                await asyncio.sleep(3)
                print("[*] Clicked cancel.")
            else:
                print("[!] Cancel button not found")
        except Exception as e:
            print(f"[!] Cancel click error: {e}")

        await asyncio.sleep(2)

        # ── Summary ───────────────────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("CAPTURED FINDSSNET.IO CALLS:")
        print("=" * 60)
        unique_urls = {}
        for c in captured:
            key = c["url"].split("?")[0]
            if key not in unique_urls:
                unique_urls[key] = c
        for url, c in unique_urls.items():
            print(f"\n[{c['status']}] {url}")
            try:
                parsed = json.loads(c["body"])
                print(json.dumps(parsed, indent=2, ensure_ascii=False)[:600])
            except Exception:
                print(c["body"][:300])

        # Save full log
        with open("/tmp/sniff_results.json", "w") as f:
            json.dump(captured, f, ensure_ascii=False, indent=2)
        print("\n[*] Full log saved to /tmp/sniff_results.json")

        await pw.stop()


async def main():
    print(f"[*] Starting profile {PROFILE_ID}...")
    ws_url = await start_profile(PROFILE_ID)
    if not ws_url:
        print("[!] Could not get wsUrl. Is GoLogin Desktop running?")
        sys.exit(1)
    print(f"[*] wsUrl: {ws_url[:60]}...")
    await sniff(ws_url)


if __name__ == "__main__":
    asyncio.run(main())
