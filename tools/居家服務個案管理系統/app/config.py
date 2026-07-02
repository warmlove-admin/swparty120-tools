from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    secret_key: str = "dev-only-change-me"
    database_url: str = "sqlite:///./data/app.db"
    line_channel_secret: str = ""
    line_channel_access_token: str = ""
    google_maps_api_key: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
