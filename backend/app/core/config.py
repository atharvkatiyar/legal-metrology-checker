from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
	model_config = SettingsConfigDict(env_file=".env", extra="ignore")
	PROJECT_NAME: str = "Legal Metrology Compliance API"
	API_V1_STR: str = "/api/v1"
	DATABASE_URL: str = "sqlite+aiosqlite:///./compliance.db"
	MAX_IMAGES_PER_SCAN: int = 8
	UPLOAD_DIR: str = "uploads"

settings = Settings()