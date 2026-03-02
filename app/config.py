from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    football_data_key: str = ""
    groq_api_key: str = ""
    current_season: int = 2025

    # Les 5 grands championnats (codes football-data.org)
    SUPPORTED_LEAGUES: dict[str, str] = {
        "PL": "Premier League",
        "PD": "La Liga",
        "BL1": "Bundesliga",
        "SA": "Serie A",
        "FL1": "Ligue 1",
    }

    FOOTBALL_DATA_BASE_URL: str = "https://api.football-data.org/v4"


settings = Settings()
