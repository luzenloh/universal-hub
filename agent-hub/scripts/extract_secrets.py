"""
Extracts MassMO secrets from running GoLogin profiles via CDP.
Run after launching profiles in GoLogin Desktop.

Usage:
    python3 extract_secrets.py
"""
import asyncio
import json

import httpx
from playwright.async_api import async_playwright

GOLOGIN_LOCAL = "http://localhost:36912"

# Numbered profile IDs for Token 13 (M1–M15)
PROFILES = [
    ("M1",  "67e795cb734526868e4632e3"),
    ("M2",  "67ec17c970cc8dc51410e209"),
    ("M3",  "67e795d084eaf1e6fea0423d"),
    ("M4",  "67e795d5813c7fcbd6a0dac5"),
    ("M5",  "67e795db84eaf1e6fea048a2"),
    ("M6",  "67e795e1ab6bc75d81702902"),
    ("M7",  "67e795ebfad45b9ae00d027e"),
    ("M8",  "67e795f04c8ce2345179d152"),
    ("M9",  "67e795f3813c7fcbd6a0ec51"),
    ("M10", "67e795ff73a9e9d11fe7b0cb"),
    ("M11", "69455755f4994b150de0509d"),
    ("M12", "6945575b5235b2a24777fe93"),
    ("M13", "694557614764e7c47deee8e9"),
    ("M14", "694557666a186148e63fc703"),
    ("M15", "6945576cd3e06a6888a43a30"),
]

# localStorage keys to check for the secret
SECRET_KEYS = ["secret", "massmo_secret", "auth_secret", "user_secret",
               "api_secret", "api_token", "payouter_token", "sign_in_token"]


async def get_ws_url(profile_id: str) -> str | None:
    """Start profile and get wsUrl."""
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.post(
                f"{GOLOGIN_LOCAL}/browser/start-profile",
                json={"profileId": profile_id, "sync": True},
            )
            ws = r.json().get("wsUrl", "")
            if not ws:
                # Already running — stop and restart
                await client.post(f"{GOLOGIN_LOCAL}/browser/stop-profile",
                                  json={"profileId": profile_id})
                await asyncio.sleep(4)
                r = await client.post(
                    f"{GOLOGIN_LOCAL}/browser/start-profile",
                    json={"profileId": profile_id, "sync": True},
                )
                ws = r.json().get("wsUrl", "")
            return ws or None
        except Exception as e:
            print(f"  [GoLogin error] {e}")
            return None


async def extract_secret_from_profile(pw, label: str, profile_id: str) -> str | None:
    ws_url = await get_ws_url(profile_id)
    if not ws_url:
        print(f"  {label}: ❌ could not get wsUrl")
        return None

    try:
        browser = await pw.chromium.connect_over_cdp(ws_url)
        ctx = browser.contexts[0] if browser.contexts else None
        if not ctx:
            print(f"  {label}: ❌ no browser context")
            await pw.stop()
            return None

        # Find massmo.io page or open one
        page = None
        for p in ctx.pages:
            if "massmo.io" in p.url:
                page = p
                break
        if page is None:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://massmo.io/", wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(2)

        # Dump all localStorage keys/values
        storage = await page.evaluate("""
            () => {
                const result = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    result[key] = localStorage.getItem(key);
                }
                return result;
            }
        """)

        # Look for known secret keys
        secret = None
        for key in SECRET_KEYS:
            if key in storage:
                secret = storage[key]
                print(f"  {label}: ✓ found localStorage['{key}'] = {secret[:20]}...")
                break

        if not secret:
            # Try all keys — look for 32-char hex strings (same format as known secret)
            import re
            for key, val in storage.items():
                if val and re.fullmatch(r"[0-9a-f]{32}", str(val)):
                    secret = val
                    print(f"  {label}: ✓ found hex token at localStorage['{key}'] = {secret[:20]}...")
                    break

        if not secret:
            print(f"  {label}: ❌ no secret found in localStorage")
            print(f"    Available keys: {list(storage.keys())[:10]}")

        await pw.stop()
        return secret

    except Exception as e:
        print(f"  {label}: ❌ error — {e}")
        return None


async def main():
    print("Connecting to GoLogin Desktop...")
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.get(GOLOGIN_LOCAL)
    except Exception:
        print("❌ GoLogin Desktop is not running. Please open it first.")
        return

    results: dict[str, str] = {}

    async with async_playwright() as pw:
        for label, profile_id in PROFILES:
            print(f"\nProcessing {label} ({profile_id[:8]}...)...")
            secret = await extract_secret_from_profile(pw, label, profile_id)
            if secret:
                results[label] = secret
            await asyncio.sleep(1)

    print("\n" + "=" * 60)
    print("RESULTS:")
    print("=" * 60)
    for label, secret in results.items():
        print(f"{label}: {secret}")

    if results:
        # Generate /set_secrets command
        secrets_list = [results.get(f"M{i+1}", "") for i in range(15)]
        cmd = "/set_secrets 3 " + " ".join(s for s in secrets_list if s)
        print(f"\n📋 Command to run in Telegram:\n{cmd}")

        # Save to file
        with open("/tmp/massmo_secrets.json", "w") as f:
            json.dump(results, f, indent=2)
        print("\nSaved to /tmp/massmo_secrets.json")


if __name__ == "__main__":
    asyncio.run(main())
