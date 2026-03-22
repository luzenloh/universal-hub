"""
Orchestrator — manages N WindowAgents for one active session.
Global singleton shared between the Agent web panel and the FastAPI dashboard.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from bot.services.inbound_controller import InboundController, InboundStatus
from bot.services.window_agent import WindowAgent
from bot.services.ws_manager import WebSocketManager
from web.models.schemas import CommandRequest, CommandResult, InboundState, InboundPlatformState, WindowState, WindowStatus

_CACHE_FILE = Path(__file__).parent.parent.parent / "massmo_jwt_cache.json"
_SECRETS_CACHE_FILE = Path(__file__).parent.parent.parent / "shift_secrets_cache.json"

logger = logging.getLogger(__name__)

_orchestrator: "Orchestrator | None" = None


def get_orchestrator() -> "Orchestrator":
    if _orchestrator is None:
        raise RuntimeError("Orchestrator not initialized. Call init_orchestrator() first.")
    return _orchestrator


def init_orchestrator(ws_manager: WebSocketManager) -> "Orchestrator":
    global _orchestrator
    _orchestrator = Orchestrator(ws_manager=ws_manager)
    return _orchestrator


class Orchestrator:
    def __init__(self, ws_manager: WebSocketManager) -> None:
        self._ws = ws_manager
        self._agents: dict[str, WindowAgent] = {}  # window_id → agent
        self._profile_map: dict[str, str] = {}  # label → GoLogin profile_id
        self._loading_progress: dict | None = None  # {"current", "total", "label"}
        self._cache_lock = asyncio.Lock()
        self._inbound_controllers: dict[str, InboundController] = {}  # window_id → controller
        self._shift_secrets: dict | None = None  # payfast/montera config for current shift
        self._folder_name: str = ""

    # ------------------------------------------------------------------ folder info

    def set_folder_name(self, name: str) -> None:
        self._folder_name = name or ""

    def get_folder_name(self) -> str:
        return self._folder_name

    # ------------------------------------------------------------------ shift secrets (inbound)

    def set_shift_secrets(self, secrets: dict) -> None:
        """Store payfast/montera config for the current shift and persist to disk."""
        self._shift_secrets = secrets
        try:
            _SECRETS_CACHE_FILE.write_text(json.dumps(secrets))
        except Exception as exc:
            logger.warning("Failed to persist shift secrets: %s", exc)
        logger.info("Shift secrets set: platforms=%s", list(secrets.keys()))

    def get_inbound_states(self) -> list[InboundState]:
        states: list[InboundState] = []
        for window_id, ic in self._inbound_controllers.items():
            states.append(InboundState(
                window_id=window_id,
                status=ic.status.value,
                platforms=[
                    InboundPlatformState(**p) for p in ic.get_platform_states()
                ],
            ))
        return states

    # ------------------------------------------------------------------ loading UI

    async def set_profile_map(self, profile_map: dict[str, str]) -> None:
        self._profile_map = profile_map
        await self._save_profile_map(profile_map)

    def get_loading_progress(self) -> dict | None:
        return self._loading_progress

    async def update_loading(self, current: int, total: int, label: str) -> None:
        self._loading_progress = {"current": current, "total": total, "label": label}
        await self._ws.broadcast({"event": "loading_progress", **self._loading_progress})

    async def clear_loading(self) -> None:
        self._loading_progress = None
        await self._ws.broadcast({"event": "loading_done"})

    def get_available_labels(self) -> list[str]:
        return sorted(
            [label for label in self._profile_map if label not in self._agents],
            key=lambda x: int(x[1:]) if x[1:].isdigit() else 99,
        )

    # ------------------------------------------------------------------ JWT cache

    async def _save_jwt(self, label: str, jwt: str) -> None:
        async with self._cache_lock:
            try:
                cache: dict = json.loads(_CACHE_FILE.read_text()) if _CACHE_FILE.exists() else {}
                cache[label] = {"jwt": jwt}
                _CACHE_FILE.write_text(json.dumps(cache, indent=2))
            except Exception as exc:
                logger.warning("Failed to save JWT cache for %s: %s", label, exc)

    async def _save_profile_map(self, profile_map: dict[str, str]) -> None:
        async with self._cache_lock:
            try:
                cache: dict = json.loads(_CACHE_FILE.read_text()) if _CACHE_FILE.exists() else {}
                cache["_profile_map"] = profile_map
                _CACHE_FILE.write_text(json.dumps(cache, indent=2))
            except Exception as exc:
                logger.warning("Failed to save profile map: %s", exc)

    # ------------------------------------------------------------------ session

    async def attach_profiles_jwt(
        self, entries: list[tuple[str, str, str]]
    ) -> list[WindowState]:
        """Create JWT-only agents. entries: (window_id, label, jwt)."""
        await self.stop_agents()

        states: list[WindowState] = []
        for window_id, label, jwt in entries:
            agent = WindowAgent(window_id, label, "", cached_jwt=jwt,
                                on_state_change=self._on_state_change)
            self._agents[window_id] = agent
            agent.start()
            await self._save_jwt(label, jwt)
            states.append(agent.get_state())

        await self._ws.broadcast({
            "event": "state_snapshot",
            "windows": [s.model_dump() for s in states],
        })

        logger.info("Attached %d JWT-agents", len(entries))
        return states

    async def restore_from_cache(self) -> int:
        """On startup: re-connect profiles from saved JWT cache. Returns count started."""
        # Restore shift secrets
        if _SECRETS_CACHE_FILE.exists():
            try:
                self._shift_secrets = json.loads(_SECRETS_CACHE_FILE.read_text())
                logger.info("Restored shift secrets from cache: %s", list(self._shift_secrets.keys()))
            except Exception as exc:
                logger.warning("Failed to load shift secrets cache: %s", exc)

        if not _CACHE_FILE.exists():
            return 0
        try:
            cache: dict = json.loads(_CACHE_FILE.read_text())
        except Exception as exc:
            logger.warning("Failed to load JWT cache: %s", exc)
            return 0
        if not cache:
            return 0

        self._profile_map = cache.get("_profile_map") or {}

        entries = sorted(
            [(k, v) for k, v in cache.items() if k != "_profile_map"],
            key=lambda x: int(x[0][1:]) if x[0][1:].isdigit() else 99,
        )
        for label, entry in entries:
            jwt = entry.get("jwt") if isinstance(entry, dict) else None
            if not jwt:
                continue
            agent = WindowAgent(label, label, "", cached_jwt=jwt,
                                on_state_change=self._on_state_change)
            self._agents[label] = agent
            agent.start()

        logger.info("Restored %d agents from JWT cache, profile_map=%s",
                    len(self._agents), list(self._profile_map.keys()))
        return len(self._agents)

    async def add_profile_by_label(self, label: str) -> WindowState:
        """Launch GoLogin browser → extract JWT via CDP → stop browser → create agent."""
        from bot.services.gologin import GoLoginService
        from bot.services.massmo_actions import extract_jwt

        profile_id = self._profile_map.get(label)
        if not profile_id:
            raise ValueError(
                f"Профиль {label} не найден в текущей папке. "
                "Запустите смену через Telegram чтобы обновить маппинг."
            )
        if label in self._agents:
            return self._agents[label].get_state()

        service = GoLoginService()
        result = await service.start_profile(profile_id)
        ws_url = result.get("wsUrl") if isinstance(result, dict) else None
        if not ws_url:
            raise RuntimeError(f"GoLogin не вернул wsUrl для {label}")

        await asyncio.sleep(8)
        jwt = await extract_jwt(ws_url)
        await service.stop_profile(profile_id)

        if not jwt:
            raise RuntimeError(
                f"Не удалось извлечь JWT для {label}. "
                "Убедитесь что MassMO открыт и вы авторизованы в профиле."
            )

        agent = WindowAgent(label, label, "", cached_jwt=jwt,
                            on_state_change=self._on_state_change)
        self._agents[label] = agent
        agent.start()
        await self._save_jwt(label, jwt)
        state = agent.get_state()
        await self._ws.broadcast({"event": "window_update", "window": state.model_dump()})
        logger.info("Added profile %s via GoLogin CDP", label)
        return state

    async def begin_fresh_session(self) -> None:
        """Clear old session. Call before sequential add_agent_jwt()."""
        await self.stop_agents()

    async def add_agent_jwt(self, label: str, jwt: str) -> WindowState:
        """Add a single agent with cached JWT without stopping existing agents."""
        if label in self._agents:
            return self._agents[label].get_state()
        agent = WindowAgent(label, label, "", cached_jwt=jwt,
                            on_state_change=self._on_state_change)
        self._agents[label] = agent
        agent.start()
        await self._save_jwt(label, jwt)
        state = agent.get_state()
        await self._ws.broadcast({"event": "window_update", "window": state.model_dump()})
        logger.info("Added agent %s (sequential JWT)", label)
        return state

    async def remove_agent(self, window_id: str) -> None:
        """Stop and remove a single agent, update JWT cache."""
        agent = self._agents.pop(window_id, None)
        if agent:
            await agent.stop()
        async with self._cache_lock:
            try:
                if _CACHE_FILE.exists():
                    cache: dict = json.loads(_CACHE_FILE.read_text())
                    cache.pop(window_id, None)
                    _CACHE_FILE.write_text(json.dumps(cache, indent=2))
            except Exception as exc:
                logger.warning("Failed to update JWT cache after remove %s: %s", window_id, exc)
        await self._ws.broadcast({"event": "window_removed", "window_id": window_id})
        logger.info("Removed agent %s", window_id)

    async def stop_agents(self) -> None:
        """Stop all window agents (logout from MassMO API)."""
        # Stop inbound controllers first
        for ic in list(self._inbound_controllers.values()):
            asyncio.create_task(ic.stop())
        self._inbound_controllers.clear()
        self._shift_secrets = None
        try:
            _SECRETS_CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass

        if not self._agents:
            return
        for agent in list(self._agents.values()):
            await agent.stop()
        self._agents.clear()
        async with self._cache_lock:
            try:
                if _CACHE_FILE.exists():
                    _CACHE_FILE.unlink()
            except Exception as exc:
                logger.warning("JWT cache unlink failed: %s", exc)
        logger.info("All agents stopped")

    async def stop_session(self) -> None:
        await self.stop_agents()

    # ------------------------------------------------------------------ state

    def get_all_states(self) -> list[WindowState]:
        return [a.get_state() for a in self._agents.values()]

    def is_active(self) -> bool:
        return bool(self._agents)

    async def add_profile(self, window_id: str, label: str, secret: str) -> WindowState:
        """Add or replace a single profile agent (manual connect via secret)."""
        if window_id in self._agents:
            await self._agents[window_id].stop()
        agent = WindowAgent(window_id, label, secret, on_state_change=self._on_state_change)
        agent.start()
        self._agents[window_id] = agent
        state = agent.get_state()
        await self._ws.broadcast({"event": "window_update", "window": state.model_dump()})
        logger.info("Added agent %s", label)
        return state

    # ------------------------------------------------------------------ commands

    async def send_command(self, window_id: str, cmd: CommandRequest) -> CommandResult:
        agent = self._agents.get(window_id)
        if agent is None:
            return CommandResult(success=False, message=f"Window {window_id} not found")
        return await agent.enqueue_command(cmd)

    # ------------------------------------------------------------------ internal

    async def _on_state_change(self, state: WindowState) -> None:
        await self._ws.broadcast({
            "event": "window_update",
            "window": state.model_dump(),
        })

        ic = self._inbound_controllers.get(state.window_id)
        secrets = self._shift_secrets

        if state.status == WindowStatus.ACTIVE_PAYOUT and state.payout:
            # New order or different order_id → create/replace controller
            if secrets and ("payfast" in secrets or "montera" in secrets):
                if ic is None or ic.payout.order_id != state.payout.order_id:
                    if ic is not None:
                        asyncio.create_task(ic.stop())
                    new_ic = InboundController(state.window_id, state.payout, secrets, self)
                    self._inbound_controllers[state.window_id] = new_ic
                    asyncio.create_task(new_ic.start())

        elif state.status == WindowStatus.EXPIRING:
            if ic and ic.status == InboundStatus.LIVE:
                asyncio.create_task(ic.handle_expiring())

        elif state.status in {
            WindowStatus.PAID,
            WindowStatus.IDLE,
            WindowStatus.STOPPED,
            WindowStatus.ERROR,
            WindowStatus.DISABLED,
        }:
            if ic:
                asyncio.create_task(ic.stop())
                del self._inbound_controllers[state.window_id]
