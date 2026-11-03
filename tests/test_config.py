from pathlib import Path

from app.config import get_settings


def test_empty_values_fall_back_to_defaults(monkeypatch):
    # 复制 .env.example 后留空的项不能变成空字符串：DATA_DIR 会落到当前目录，PORT 会解析失败
    for name in ("DATA_DIR", "APP_ENV", "PORT", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "SUMMARY_CONTEXT_CHAPTERS"):
        monkeypatch.setenv(name, "")

    settings = get_settings()
    assert settings.data_dir == Path("./data").resolve()
    assert settings.app_env == "production"
    assert settings.port == 8000
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-chat"
    assert settings.summary_context_chapters == 3


def test_values_are_read_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("APP_ENV", "development")

    settings = get_settings()
    assert settings.data_dir == tmp_path.resolve()
    assert settings.port == 9000
    assert settings.is_development


def test_cors_origins_are_parsed(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " https://a.example.com/ , ,http://localhost:5173")
    assert get_settings().cors_origins == ("https://a.example.com", "http://localhost:5173")
    monkeypatch.setenv("CORS_ORIGINS", "")
    assert get_settings().cors_origins == ()
