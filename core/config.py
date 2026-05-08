from pydantic_settings import BaseSettings, SettingsConfigDict # type: ignore
from pathlib import Path

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent

    GROQ_API_KEY: str = "GROQ_API_KEY" # should be set in .env file or environment variable
    # Recommended models (free tier, fast):
    #   llama-3.3-70b-versatile   — best quality
    #   llama-3.1-8b-instant      — fastest
    #   mixtral-8x7b-32768        — good balance
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_EMBED_MODEL: str = "nomic-embed-text"

    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE_MB: int = 50
    CORS_ORIGINS: str = "*" # currently set to allow all origins, but should be restricted in production

settings = Settings()
