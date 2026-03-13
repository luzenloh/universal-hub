from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./gologin.db"

    class Config:
        env_file = ".env"


settings = Settings()
