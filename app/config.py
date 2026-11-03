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
    cors_origins: tuple[str, ...] = ()

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


def _env(name: str, default: str) -> str:
    # .env 里写了 "PORT=" 却没填值时，os.environ 得到的是空字符串而不是缺失。
    # 空值一律按未设置处理，否则 DATA_DIR 会变成当前目录、PORT 会解析失败
    return os.environ.get(name) or default


def get_settings() -> Settings:
    # 每次调用都重新读取环境变量，测试里用 monkeypatch 改环境变量即可生效
    return Settings(
        data_dir=Path(_env("DATA_DIR", "./data")).resolve(),
        app_env=_env("APP_ENV", "production"),
        host=_env("HOST", "127.0.0.1"),
        port=int(_env("PORT", "8000")),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY") or None,
        deepseek_base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=_env("DEEPSEEK_MODEL", "deepseek-chat"),
        summary_context_chapters=int(_env("SUMMARY_CONTEXT_CHAPTERS", "3")),
        cors_origins=tuple(o.strip().rstrip("/") for o in _env("CORS_ORIGINS", "").split(",") if o.strip()),
    )
