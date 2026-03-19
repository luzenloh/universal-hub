import json
import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.models import Agent, Folder

logger = logging.getLogger(__name__)


class FolderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all_folders(self) -> list[Folder]:
        result = await self.session.execute(select(Folder).order_by(Folder.name))
        return list(result.scalars().all())

    async def get_folder_by_id(self, folder_id: int) -> Folder | None:
        result = await self.session.execute(select(Folder).where(Folder.id == folder_id))
        return result.scalar_one_or_none()

    async def get_active_folder(self, user_id: int) -> Folder | None:
        result = await self.session.execute(
            select(Folder).where(Folder.assigned_to == user_id, Folder.is_free == False)  # noqa: E712
        )
        return result.scalar_one_or_none()

    async def assign_folder(
        self, folder_id: int, user_id: int, count: int, agent_id: str
    ) -> Folder | None:
        """Atomically assign a folder to a user + agent."""
        result = await self.session.execute(
            update(Folder)
            .where(Folder.id == folder_id, Folder.is_free == True)  # noqa: E712
            .values(
                is_free=False,
                assigned_to=user_id,
                assigned_at=datetime.utcnow(),
                selected_count=count,
                assigned_agent_id=agent_id,
            )
            .returning(Folder)
        )
        await self.session.commit()
        row = result.fetchone()
        return row[0] if row else None

    async def release_folder(self, user_id: int) -> Folder | None:
        folder = await self.get_active_folder(user_id)
        if not folder:
            return None
        await self.session.execute(
            update(Folder)
            .where(Folder.assigned_to == user_id)
            .values(
                is_free=True,
                assigned_to=None,
                assigned_at=None,
                selected_count=None,
                assigned_agent_id=None,
            )
        )
        await self.session.commit()
        return folder

    async def force_release_folder(self, folder_id: int) -> bool:
        result = await self.session.execute(
            update(Folder)
            .where(Folder.id == folder_id, Folder.is_free == False)  # noqa: E712
            .values(
                is_free=True,
                assigned_to=None,
                assigned_at=None,
                selected_count=None,
                assigned_agent_id=None,
            )
        )
        await self.session.commit()
        return result.rowcount > 0

    async def set_massmo_secrets(self, folder_id: int, secrets: list[str]) -> bool:
        result = await self.session.execute(
            update(Folder)
            .where(Folder.id == folder_id)
            .values(massmo_secrets=json.dumps(secrets))
            .returning(Folder.id)
        )
        await self.session.commit()
        return result.fetchone() is not None

    async def upsert_folder(
        self,
        gologin_id: str,
        name: str,
        main_profile_id: str | None,
        numbered_profile_ids: list[str],
    ) -> None:
        result = await self.session.execute(
            select(Folder).where(Folder.gologin_id == gologin_id)
        )
        folder = result.scalar_one_or_none()
        numbered_json = json.dumps(numbered_profile_ids)
        if folder:
            folder.name = name
            folder.main_profile_id = main_profile_id
            folder.numbered_profile_ids = numbered_json
            folder.profile_count = len(numbered_profile_ids)
        else:
            self.session.add(Folder(
                gologin_id=gologin_id,
                name=name,
                main_profile_id=main_profile_id,
                numbered_profile_ids=numbered_json,
                profile_count=len(numbered_profile_ids),
            ))
        await self.session.commit()


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all_agents(self) -> list[Agent]:
        result = await self.session.execute(select(Agent).order_by(Agent.agent_id))
        return list(result.scalars().all())

    async def get_agent_by_id(self, agent_id: str) -> Agent | None:
        result = await self.session.execute(
            select(Agent).where(Agent.agent_id == agent_id)
        )
        return result.scalar_one_or_none()

    async def get_free_agent(self) -> Agent | None:
        """Return the most recently seen active agent with no assigned folder."""
        result = await self.session.execute(
            select(Agent)
            .where(Agent.is_active == True, Agent.assigned_folder_id == None)  # noqa: E712
            .order_by(Agent.last_seen.desc())
        )
        return result.scalars().first()

    async def get_free_agents(self) -> list[Agent]:
        """Return all active agents with no assigned folder, newest first."""
        result = await self.session.execute(
            select(Agent)
            .where(Agent.is_active == True, Agent.assigned_folder_id == None)  # noqa: E712
            .order_by(Agent.last_seen.desc())
        )
        return list(result.scalars().all())

    async def upsert_agent(
        self,
        agent_id: str,
        public_url: str,
        local_url: str,
        owner_telegram_id: int | None = None,
    ) -> Agent:
        agent = await self.get_agent_by_id(agent_id)
        if agent:
            agent.public_url = public_url
            agent.local_url = local_url
            agent.last_seen = datetime.utcnow()
            agent.is_active = True
            if owner_telegram_id is not None:
                agent.owner_telegram_id = owner_telegram_id
        else:
            agent = Agent(
                agent_id=agent_id,
                public_url=public_url,
                local_url=local_url,
                last_seen=datetime.utcnow(),
                is_active=True,
                owner_telegram_id=owner_telegram_id,
            )
            self.session.add(agent)
        await self.session.commit()
        await self.session.refresh(agent)
        return agent

    async def get_agent_by_owner(self, owner_telegram_id: int) -> Agent | None:
        """Return a free active agent belonging to this Telegram user.
        Skips agents that already have a folder assigned (running on another device).
        """
        result = await self.session.execute(
            select(Agent)
            .where(
                Agent.owner_telegram_id == owner_telegram_id,
                Agent.is_active == True,  # noqa: E712
                Agent.assigned_folder_id == None,  # noqa: E711  only free agents
            )
            .order_by(Agent.last_seen.desc())
        )
        return result.scalars().first()

    async def update_heartbeat(self, agent_id: str) -> None:
        await self.session.execute(
            update(Agent)
            .where(Agent.agent_id == agent_id)
            .values(last_seen=datetime.utcnow(), is_active=True)
        )
        await self.session.commit()

    async def assign_agent_to_folder(
        self, agent_id: str, folder_id: int, notify_chat_id: int
    ) -> None:
        await self.session.execute(
            update(Agent)
            .where(Agent.agent_id == agent_id)
            .values(assigned_folder_id=folder_id, notify_chat_id=notify_chat_id)
        )
        await self.session.commit()

    async def release_agent(self, agent_id: str) -> None:
        await self.session.execute(
            update(Agent)
            .where(Agent.agent_id == agent_id)
            .values(assigned_folder_id=None, notify_chat_id=None)
        )
        await self.session.commit()
