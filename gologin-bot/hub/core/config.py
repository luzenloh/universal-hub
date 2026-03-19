from pydantic_settings import BaseSettings


class HubSettings(BaseSettings):
    bot_token: str
    admin_username: str
    hub_secret: str                # shared secret between Hub ↔ Agent
    hub_host: str = "127.0.0.1"
    hub_port: int = 8082
    database_url: str = "sqlite+aiosqlite:///./hub.db"
    gologin_api_token: str = ""

    class Config:
        env_file = ".env.hub"


settings = HubSettings()
ADMIN_USERNAME = settings.admin_username
