import json
import logging
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hub.db.models import Agent, AgentSetupToken, Folder, Schedule, User

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
        return await self.update_folder_secrets(folder_id, "massmo", secrets)

    async def update_folder_secrets(self, folder_id: int, key: str, value: object) -> bool:
        """Merge a single key into the folder's secrets dict (creates dict if legacy list)."""
        result = await self.session.execute(select(Folder).where(Folder.id == folder_id))
        folder = result.scalar_one_or_none()
        if not folder:
            return False
        d = folder.secrets_dict
        d[key] = value
        folder.massmo_secrets = json.dumps(d)
        await self.session.commit()
        return True

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

    async def get_stuck_agent_by_owner(self, owner_telegram_id: int) -> Agent | None:
        """Return active agent that has a stuck folder assignment (previous session didn't clean up)."""
        result = await self.session.execute(
            select(Agent)
            .where(
                Agent.owner_telegram_id == owner_telegram_id,
                Agent.is_active == True,  # noqa: E712
                Agent.assigned_folder_id != None,  # noqa: E711
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

    async def update_pinned_message(self, agent_id: str, message_id: int, chat_id: int) -> None:
        await self.session.execute(
            update(Agent)
            .where(Agent.agent_id == agent_id)
            .values(pinned_message_id=message_id, pinned_chat_id=chat_id)
        )
        await self.session.commit()

    async def clear_pinned_message(self, agent_id: str) -> None:
        await self.session.execute(
            update(Agent)
            .where(Agent.agent_id == agent_id)
            .values(pinned_message_id=None, pinned_chat_id=None)
        )
        await self.session.commit()

    async def update_agent_stats(
        self, agent_id: str, active_count: int, searching_count: int, new_paid_count: int
    ) -> None:
        values: dict = {
            "active_payout_count": active_count,
            "searching_count": searching_count,
        }
        if new_paid_count > 0:
            # Fetch current count to increment
            result = await self.session.execute(
                select(Agent).where(Agent.agent_id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if agent:
                values["session_payout_count"] = (agent.session_payout_count or 0) + new_paid_count
                values["last_payout_at"] = datetime.utcnow()
        await self.session.execute(
            update(Agent).where(Agent.agent_id == agent_id).values(**values)
        )
        await self.session.commit()

    async def reset_session_stats(self, agent_id: str) -> None:
        await self.session.execute(
            update(Agent)
            .where(Agent.agent_id == agent_id)
            .values(session_payout_count=0, last_payout_at=None)
        )
        await self.session.commit()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, telegram_id: int, username: str | None, first_name: str | None) -> None:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user:
            user.username = username
            user.first_name = first_name
        else:
            self.session.add(User(telegram_id=telegram_id, username=username, first_name=first_name))
        await self.session.commit()

    async def get_by_username(self, username: str) -> User | None:
        result = await self.session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()


class AgentSetupTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, jti: str, agent_id: str, owner_telegram_id: int, expires_at: datetime) -> AgentSetupToken:
        token = AgentSetupToken(
            jti=jti,
            agent_id=agent_id,
            owner_telegram_id=owner_telegram_id,
            expires_at=expires_at,
        )
        self.session.add(token)
        await self.session.commit()
        await self.session.refresh(token)
        return token

    async def get_valid(self, jti: str) -> AgentSetupToken | None:
        """Return token if it exists and has not expired (multi-use within expiry window)."""
        result = await self.session.execute(
            select(AgentSetupToken).where(
                AgentSetupToken.jti == jti,
                AgentSetupToken.expires_at > datetime.utcnow(),
            )
        )
        return result.scalar_one_or_none()

    async def mark_used(self, jti: str) -> None:
        await self.session.execute(
            update(AgentSetupToken)
            .where(AgentSetupToken.jti == jti)
            .values(used_at=datetime.utcnow())
        )
        await self.session.commit()

    async def revoke_for_agent(self, agent_id: str) -> int:
        """Mark all unused tokens for agent_id as used. Returns count revoked."""
        result = await self.session.execute(
            update(AgentSetupToken)
            .where(AgentSetupToken.agent_id == agent_id, AgentSetupToken.used_at == None)  # noqa: E711
            .values(used_at=datetime.utcnow())
        )
        await self.session.commit()
        return result.rowcount


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        telegram_id: int,
        display_name: str | None,
        week_start: date,
        days: dict,
    ) -> None:
        result = await self.session.execute(
            select(Schedule).where(
                Schedule.telegram_id == telegram_id,
                Schedule.week_start == week_start,
            )
        )
        schedule = result.scalar_one_or_none()
        days_json = json.dumps(days, ensure_ascii=False)
        if schedule:
            schedule.days = days_json
            schedule.display_name = display_name
            schedule.submitted_at = datetime.utcnow()
        else:
            self.session.add(
                Schedule(
                    telegram_id=telegram_id,
                    display_name=display_name,
                    week_start=week_start,
                    days=days_json,
                )
            )
        await self.session.commit()

    async def get(self, telegram_id: int, week_start: date) -> Schedule | None:
        result = await self.session.execute(
            select(Schedule).where(
                Schedule.telegram_id == telegram_id,
                Schedule.week_start == week_start,
            )
        )
        return result.scalar_one_or_none()

    async def get_team(self, week_start: date) -> list[Schedule]:
        result = await self.session.execute(
            select(Schedule)
            .where(Schedule.week_start == week_start)
            .order_by(Schedule.submitted_at)
        )
        return list(result.scalars().all())
