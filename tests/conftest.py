from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import epub_builder


@pytest.fixture(scope="session")
def epubs(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("epubs")
    return {
        "nested_zh": epub_builder.nested_zh(d / "nested_zh.epub"),
        "anchors": epub_builder.anchors(d / "anchors.epub"),
        "no_toc": epub_builder.no_toc(d / "no_toc.epub"),
        "english": epub_builder.english(d / "english.epub"),
    }


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(d))
    monkeypatch.setenv("APP_ENV", "production")
    return d


@pytest.fixture
def client(data_dir) -> TestClient:
    from app.main import app

    with TestClient(app) as c:
        yield c
