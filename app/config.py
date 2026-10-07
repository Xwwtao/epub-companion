import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    app_env: str
    host: str
    port: int
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    summary_context_chapters: int

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "companion.db"

    @property
    def books_dir(self) -> Path:
        return self.data_dir / "books"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"


def get_settings() -> Settings:
    # 每次调用都重新读取环境变量，测试里用 monkeypatch 改环境变量即可生效
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "./data")).resolve(),
        app_env=os.environ.get("APP_ENV", "production"),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY") or None,
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        summary_context_chapters=int(os.environ.get("SUMMARY_CONTEXT_CHAPTERS", "3")),
    )
