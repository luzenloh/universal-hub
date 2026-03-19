import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import BrowserContext, Playwright, async_playwright

logger = logging.getLogger(__name__)

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILES_DIR = Path("./browser_profiles")
BASE_PORT = 9222
STARTUP_TIMEOUT = 8.0

# ---------------------------------------------------------------------------
# Stealth script — injected into every page before any JS runs.
# Removes automation signals and fakes a normal browser environment.
# ---------------------------------------------------------------------------
STEALTH_SCRIPT = """
// 1. Hide navigator.webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });

// 2. Add window.chrome (absent in headless / automated Chrome)
if (!window.chrome) {
  Object.defineProperty(window, 'chrome', {
    writable: true, enumerable: true, configurable: false,
    value: {
      app: {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
      },
      runtime: {
        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', UPDATE: 'update' },
        PlatformOs: { MAC: 'mac', WIN: 'win', LINUX: 'linux', ANDROID: 'android' },
        PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
      }
    }
  });
}

// 3. Fix navigator.permissions.query — automation returns 'denied' for notifications
const _origPermQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
Object.defineProperty(window.navigator.permissions, 'query', {
  value: async (params) => {
    if (params.name === 'notifications') {
      return { state: window.Notification ? Notification.permission : 'denied', onchange: null };
    }
    return _origPermQuery(params);
  }
});

// 4. Fake plugins (empty in headless Chrome)
if (navigator.plugins.length === 0) {
  const fakePl = [
    { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',           description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client',      filename: 'internal-nacl-plugin',           description: '' },
  ];
  Object.defineProperty(navigator, 'plugins', {
    get: () => Object.assign(fakePl, {
      item: (i) => fakePl[i] || null,
      namedItem: (n) => fakePl.find(p => p.name === n) || null,
      refresh: () => {},
    })
  });
}

// 5. Patch navigator.webdriver inside iframes
const _iframeDesc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
  ..._iframeDesc,
  get() {
    const w = _iframeDesc.get.call(this);
    if (!w) return w;
    try { Object.defineProperty(w.navigator, 'webdriver', { get: () => undefined }); } catch (_) {}
    return w;
  }
});
"""


class BrowserService:
    _playwrights: dict[int, Playwright] = {}
    _contexts: dict[int, BrowserContext] = {}
    _ports: dict[int, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    async def launch(
        cls,
        token_id: int,
        proxy: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        """Launch Chrome for token profile. Returns WebSocket debugger URL."""
        if token_id in cls._contexts:
            try:
                _ = cls._contexts[token_id].pages  # raises if browser is gone
                return await cls._ws_url(cls._ports[token_id])
            except Exception:
                await cls._teardown(token_id)

        port = cls._next_free_port()
        profile_dir = PROFILES_DIR / f"profile_{token_id}"
        profile_dir.mkdir(parents=True, exist_ok=True)

        pw: Any = await async_playwright().start()

        launch_args: dict[str, Any] = {
            "user_data_dir": str(profile_dir.resolve()),
            "executable_path": CHROME_PATH,
            "headless": False,
            "ignore_default_args": ["--enable-automation"],
            "args": [
                f"--remote-debugging-port={port}",
                # --- Remove automation signals ---
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                # --- WebRTC: prevent IP leak through proxy ---
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--enforce-webrtc-ip-permission-check",
                # --- Misc hardening ---
                "--disable-features=IsolateOrigins,site-per-process",
                "--flag-switches-begin",
                "--flag-switches-end",
            ],
        }

        if proxy:
            launch_args["proxy"] = {"server": proxy}
            logger.info("Profile %s using proxy: %s", token_id, proxy)

        if user_agent:
            launch_args["user_agent"] = user_agent

        context: BrowserContext = await pw.chromium.launch_persistent_context(**launch_args)

        # Inject stealth script — runs before any page JS on every navigation
        await context.add_init_script(STEALTH_SCRIPT)

        cls._playwrights[token_id] = pw
        cls._contexts[token_id] = context
        cls._ports[token_id] = port

        # Wait for Chrome DevTools endpoint to become available
        deadline = asyncio.get_event_loop().time() + STARTUP_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            try:
                return await cls._ws_url(port)
            except Exception:
                await asyncio.sleep(0.3)

        raise RuntimeError(f"Chrome did not expose debugger on port {port} within {STARTUP_TIMEOUT}s")

    @classmethod
    async def stop(cls, token_id: int) -> None:
        """Close Chrome for the given token profile."""
        await cls._teardown(token_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    async def _teardown(cls, token_id: int) -> None:
        ctx = cls._contexts.pop(token_id, None)
        pw = cls._playwrights.pop(token_id, None)
        cls._ports.pop(token_id, None)
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

    @classmethod
    def _next_free_port(cls) -> int:
        used = set(cls._ports.values())
        port = BASE_PORT
        while port in used:
            port += 1
        return port

    @classmethod
    async def _ws_url(cls, port: int) -> str:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"http://localhost:{port}/json/version")
            resp.raise_for_status()
            return resp.json()["webSocketDebuggerUrl"]
