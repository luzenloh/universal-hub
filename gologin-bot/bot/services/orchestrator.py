"""
Orchestrator — manages N WindowAgents for one active session.
Global singleton shared between the Telegram bot and the FastAPI web panel.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import Folder
from bot.services.gologin import GoLoginService
from bot.services.window_agent import WindowAgent
from bot.services.ws_manager import WebSocketManager
from web.models.schemas import CommandRequest, CommandResult, WindowState, WindowStatus

logger = logging.getLogger(__name__)

# Global singleton — initialized once in main.py, shared with bot handlers and web routes
_orchestrator: "Orchestrator | None" = None


def get_orchestrator() -> "Orchestrator":
    if _orchestrator is None:
        raise RuntimeError("Orchestrator not initialized. Call init_orchestrator() first.")
    return _orchestrator


def init_orchestrator(session_factory: async_sessionmaker[AsyncSession], ws_manager: WebSocketManager) -> "Orchestrator":
    global _orchestrator
    _orchestrator = Orchestrator(session_factory=session_factory, ws_manager=ws_manager)
    return _orchestrator


class Orchestrator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ws_manager: WebSocketManager,
    ) -> None:
        self._session_factory = session_factory
        self._ws = ws_manager
        self._gologin = GoLoginService()
        self._agents: dict[str, WindowAgent] = {}  # window_id → agent
        self._active_profile_ids: list[str] = []

    # ------------------------------------------------------------------ session

    async def start_session(self, token_hash: str, profile_count: int) -> list[WindowState]:
        """
        Look up folder by gologin_id, start profiles, create agents.
        Used by the web panel directly.
        token_hash = GoLogin folder UUID (Folder.gologin_id).
        """
        await self.stop_agents()  # stop agents (but don't stop profiles — web panel didn't start them)

        async with self._session_factory() as session:
            result = await session.execute(
                select(Folder).where(Folder.gologin_id == token_hash)
            )
            folder = result.scalar_one_or_none()

        if folder is None:
            raise ValueError(f"Folder not found for token_hash: {token_hash}")

        profile_ids = folder.numbered_ids[:profile_count]
        if not profile_ids:
            raise ValueError("No numbered profiles in this folder")

        logger.info("Starting %d profiles for folder %s", len(profile_ids), folder.name)
        start_results = await self._gologin.start_profiles(profile_ids)

        self._active_profile_ids = profile_ids
        states: list[WindowState] = []

        for i, (pid, res) in enumerate(zip(profile_ids, start_results)):
            label = f"M{i + 1}"
            ws_url = res.get("wsUrl") if isinstance(res, dict) else None
            if not ws_url:
                logger.warning("No wsUrl for profile %s: %s", pid, res)
                agent = WindowAgent(pid, label, "", on_state_change=self._on_state_change)
                agent._status = WindowStatus.ERROR
                agent._error_msg = f"Failed to start: {res.get('error', 'no wsUrl')}"
            else:
                agent = WindowAgent(pid, label, ws_url, on_state_change=self._on_state_change)
                agent.start()

            self._agents[pid] = agent
            states.append(agent.get_state())

        return states

    async def attach_profiles(self, entries: list[tuple[str, str, str]]) -> list[WindowState]:
        """
        Called by the Telegram bot after it already started profiles.
        entries: list of (profile_id, label, ws_url)
        Creates WindowAgents for already-running profiles — no GoLogin calls.
        """
        await self.stop_agents()

        self._active_profile_ids = [pid for pid, _, _ in entries]
        states: list[WindowState] = []

        for pid, label, ws_url in entries:
            if not ws_url:
                agent = WindowAgent(pid, label, "", on_state_change=self._on_state_change)
                agent._status = WindowStatus.ERROR
                agent._error_msg = "No wsUrl"
            else:
                agent = WindowAgent(pid, label, ws_url, on_state_change=self._on_state_change)
                agent.start()

            self._agents[pid] = agent
            states.append(agent.get_state())

        # Broadcast snapshot to any open web clients
        await self._ws.broadcast({
            "event": "state_snapshot",
            "windows": [s.model_dump() for s in states],
        })

        logger.info("Attached %d profiles from bot", len(entries))
        return states

    async def stop_agents(self) -> None:
        """Stop all window agents WITHOUT stopping GoLogin profiles."""
        if not self._agents:
            return
        for agent in list(self._agents.values()):
            await agent.stop()
        self._agents.clear()
        self._active_profile_ids = []
        logger.info("Agents stopped")

    async def stop_session(self) -> None:
        """Stop agents AND GoLogin profiles. Used by web panel Stop button."""
        profile_ids = list(self._active_profile_ids)
        await self.stop_agents()
        if profile_ids:
            await self._gologin.stop_profiles(profile_ids)
            logger.info("Session stopped (profiles killed)")

    # ------------------------------------------------------------------ state

    def get_all_states(self) -> list[WindowState]:
        return [a.get_state() for a in self._agents.values()]

    def is_active(self) -> bool:
        return bool(self._agents)

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
