import json
import re
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
    # .env 里的真实 key 不能在测试中被用到
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    return d


@pytest.fixture
def client(data_dir) -> TestClient:
    from app.main import app

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class FakeLLM:
    """记录所有请求。默认按原文里的 MARKER_CHnn 返回 SUM_CHnn / END_CHnn / NAME_CHnn；
    合并请求返回 MERGED；回顾请求返回 "RECAP " + 输入里出现的所有 SUM_CHnn。
    queue 里放字符串可以指定接下来几次的原始返回内容。"""

    model = "fake-model"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.queue: list[str] = []

    def chat(self, messages: list[dict], **params) -> str:
        from app import prompts

        self.calls.append(messages)
        if self.queue:
            return self.queue.pop(0)
        if prompts.RECAP_JSON_FORMAT in messages[0]["content"]:
            # 回顾：列出输入里出现过的章节摘要，方便断言用了哪些章节；分组汇总时会逐层传递
            found = re.findall(r"SUM_CH\d+", messages[-1]["content"])
            return json.dumps({"recap": "RECAP " + " ".join(found)})
        if prompts.TASK_MERGE in messages[0]["content"]:
            return json.dumps({"summary": "MERGED", "ending_state": "MERGED_END", "names": []})
        body = messages[-1]["content"].rsplit("原文】", 1)[-1]
        m = re.search(r"MARKER_CH(\d+)", body)
        n = m.group(1) if m else "XX"
        return json.dumps({
            "summary": f"SUM_CH{n}",
            "ending_state": f"END_CH{n}",
            "names": [{"name": f"NAME_CH{n}", "aliases": [], "original": ""}],
        })

    def all_text(self) -> str:
        return "\n".join(m["content"] for call in self.calls for m in call)


@pytest.fixture
def fake_llm(client) -> FakeLLM:
    from app.main import app, get_llm

    fake = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: fake
    return fake
