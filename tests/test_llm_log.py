import json

import pytest

SECRET = "sk-test-secret-should-never-be-logged"


@pytest.fixture
def book_id(client, epubs, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    with epubs["nested_zh"].open("rb") as f:
        book_id = client.post("/api/books", files={"file": ("b.epub", f)}).json()["id"]
    client.put(f"/api/books/{book_id}/progress", json={"chapter": 3})
    return book_id


def test_development_logs_every_request_without_key(client, fake_llm, book_id, data_dir, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    fake_llm.queue.append("不是 JSON")  # 重试也算一次请求
    client.post(f"/api/books/{book_id}/summaries/next")
    client.post(f"/api/books/{book_id}/summaries/next")

    log = data_dir / "logs" / "llm_requests.jsonl"
    content = log.read_text(encoding="utf-8")
    records = [json.loads(line) for line in content.splitlines()]
    assert len(records) == len(fake_llm.calls) == 3
    assert [(r["kind"], r["chapter"]) for r in records] == [("chapter", 1), ("chapter", 1), ("chapter", 2)]
    assert records[0]["book_id"] == book_id
    assert records[0]["model"] == "fake-model"
    assert records[0]["messages"] == fake_llm.calls[0]
    assert SECRET not in content


def test_production_does_not_log(client, fake_llm, book_id, data_dir):
    client.post(f"/api/books/{book_id}/summaries/next")
    assert len(fake_llm.calls) == 1
    assert not (data_dir / "logs").exists()
