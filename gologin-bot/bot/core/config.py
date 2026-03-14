from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str
    admin_username: str
    database_url: str = "sqlite+aiosqlite:///./gologin.db"
    gologin_api_token: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8080

    class Config:
        env_file = ".env"


settings = Settings()
ADMIN_USERNAME = settings.admin_username
