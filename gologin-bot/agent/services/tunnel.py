from __future__ import annotations
"""Cloudflare Tunnel management — starts cloudflared subprocess, parses public URL."""
import asyncio
import logging
import re

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")
_STARTUP_TIMEOUT = 30  # seconds to wait for URL to appear


async def start_tunnel(port: int) -> tuple[str, asyncio.subprocess.Process]:
    """
    Start `cloudflared tunnel --url http://localhost:{port}`.
    Returns (public_url, process).
    Raises RuntimeError if URL not found within timeout.
    """
    proc = await asyncio.create_subprocess_exec(
        "cloudflared", "tunnel", "--url", f"http://localhost:{port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    public_url = await _wait_for_url(proc)
    logger.info("Cloudflare Tunnel started: %s", public_url)
    return public_url, proc


async def _wait_for_url(proc: asyncio.subprocess.Process) -> str:
    """Read stderr lines until we find the tunnel URL or timeout."""
    deadline = asyncio.get_event_loop().time() + _STARTUP_TIMEOUT

    async def _readline() -> bytes:
        assert proc.stderr is not None
        try:
            return await asyncio.wait_for(
                proc.stderr.readline(),
                timeout=max(0.1, deadline - asyncio.get_event_loop().time()),
            )
        except asyncio.TimeoutError:
            return b""

    while asyncio.get_event_loop().time() < deadline:
        line_bytes = await _readline()
        if not line_bytes:
            break
        line = line_bytes.decode(errors="replace")
        logger.debug("cloudflared: %s", line.rstrip())
        match = _URL_RE.search(line)
        if match:
            return match.group()

    raise RuntimeError(
        f"cloudflared did not produce a trycloudflare.com URL within {_STARTUP_TIMEOUT}s"
    )


async def keep_tunnel_alive(
    port: int,
    on_new_url: "Callable | None" = None,
) -> None:
    """
    Supervisor loop: restart tunnel if it dies, call on_new_url with new URL.
    on_new_url can be sync or async. Run as a background asyncio.Task.
    """
    from typing import Callable  # local import to avoid circular

    while True:
        try:
            public_url, proc = await start_tunnel(port)
            if on_new_url:
                result = on_new_url(public_url)
                if asyncio.iscoroutine(result):
                    await result
            await proc.wait()
            logger.warning("cloudflared exited, restarting in 5s…")
        except Exception as exc:
            logger.error("Tunnel error: %s — restarting in 10s", exc)
            await asyncio.sleep(10)
            continue
        await asyncio.sleep(5)
