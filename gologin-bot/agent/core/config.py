from __future__ import annotations
from pydantic_settings import BaseSettings


class AgentSettings(BaseSettings):
    hub_url: str = "http://127.0.0.1:8082"   # Hub address
    hub_secret: str                            # shared secret with Hub
    agent_id: str = "agent-mac-1"             # unique identifier for this agent
    agent_port: int = 8081                     # Agent web panel port
    agent_host: str = "127.0.0.1"
    owner_telegram_id: int = 0                 # Telegram user ID of this Mac's operator (0 = unset)
    web_host: str = "127.0.0.1"
    web_port: int = 8081                       # same as agent_port
    gologin_local_url: str = "http://localhost:36912"  # GoLogin Desktop API

    class Config:
        env_file = ".env.agent"
        extra = "ignore"


settings = AgentSettings()
