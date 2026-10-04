from pydantic_settings import BaseSettings, SettingsConfigDict # type: ignore
from pathlib import Path

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent

    GROQ_API_KEY: str = "GROQ_API_KEY" # should be set in .env file or environment variable
    # Recommended models (free tier, fast):
    #   llama-3.3-70b-versatile   — best quality
    #   llama-3.1-8b-instant      — fastest
    #   mixtral-8x7b-32768        — good balance
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_EMBED_MODEL: str = "nomic-embed-text"

    UPLOAD_DIR: str = "./data/uploads"
    MAX_FILE_SIZE_MB: int = 50
    CORS_ORIGINS: str = "http://localhost:4200" # comma-separated; never "*" because auth uses cookies
    LOG_LEVEL: str = "INFO"               # DEBUG | INFO | WARNING | ERROR

    # ── database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://spl3:spl3@localhost:5432/spl3"

    # ── auth ──────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = ""              # empty → the server refuses to start
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    COOKIE_SECURE: bool = False           # set true when served over HTTPS
    FRONTEND_URL: str = "http://localhost:4200"

    # Google OAuth2 (authorization-code flow). Both empty → Google login disabled.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # ── per-case index cache ──────────────────────────────────────────────────
    INDEX_CACHE_MAX_CASES: int = 4

    @property
    def google_enabled(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET)

    @property
    def google_redirect_url(self) -> str:
        # Google sends the user back to the SPA, which forwards code+state to the API.
        return f"{self.FRONTEND_URL.rstrip('/')}/auth/google/callback"

settings = Settings()
