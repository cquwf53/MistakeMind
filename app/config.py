from functools import lru_cache
from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"


def load_env_file() -> None:
    if os.getenv("APP_SKIP_ENV_FILE"):
        return
    env_path = BASE_DIR / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=True)
        return
    except ImportError:
        pass
    if not env_path.exists():
        return
    content = env_path.read_text(encoding="utf-8-sig")  # utf-8-sig 自动去 BOM
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip("\"'")


load_env_file()


class Settings:
    app_name = "考研错题归因系统"
    database_url = os.getenv("APP_DATABASE_URL", "sqlite:///./storage/mistakes.db")
    ai_base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    ai_api_key = os.getenv("AI_API_KEY", "")
    ai_model = os.getenv("AI_MODEL", "auto").strip() or "auto"
    ai_vision_model = os.getenv("AI_VISION_MODEL", ai_model).strip() or "auto"
    secret = os.getenv("APP_SECRET", "dev-only-change-me")
    scheduler_enabled = os.getenv("APP_SCHEDULER_ENABLED", "1").strip() in ("1", "true", "yes")
    scheduler_tomorrow_time = os.getenv("APP_SCHEDULER_TOMORROW_TIME", "22:00").strip()
    scheduler_weekly_time = os.getenv("APP_SCHEDULER_WEEKLY_TIME", "mon 09:00").strip()

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()
