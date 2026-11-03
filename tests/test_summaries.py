import json
import re

import pytest

from app import prompts, summaries
from tests.epub_builder import marker

TOTAL = 7  # nested_zh 展平后共 7 章


@pytest.fixture
def book_id(client, epubs):
    with epubs["nested_zh"].open("rb") as f:
        return client.post("/api/books", files={"file": ("b.epub", f)}).json()["id"]


def set_progress(client, book_id, n):
    assert client.put(f"/api/books/{book_id}/progress", json={"chapter": n}).status_code == 200


def generate_all(client, book_id) -> list[dict]:
    generated = []
    while True:
        res = client.post(f"/api/books/{book_id}/summaries/next")
        assert res.status_code == 200, res.text
        body = res.json()
        if body["generated"]:
            generated.append(body["generated"])
        if body["missing"] == 0:
            return generated


def call_for(fake, number: int) -> str:
    """返回生成第 number 章时发出的用户消息。"""
    for call in fake.calls:
        body = call[-1]["content"].rsplit("原文】", 1)[-1]
        if marker(number) in body:
            return call[-1]["content"]
    raise AssertionError(f"没有生成第 {number} 章的请求")


def context_part(user_message: str) -> str:
    """用户消息里原文之前的部分（人物名单 + 最近几章摘要）。"""
    return user_message.split("【本章】", 1)[0]


# ---- 防剧透（验收标准 2、3）----


@pytest.mark.parametrize("n", [2, 4, TOTAL])
def test_requests_never_contain_unread_text_or_metadata(client, fake_llm, book_id, n):
    set_progress(client, book_id, n)
    generate_all(client, book_id)

    assert len(fake_llm.calls) == n - 1
    text = fake_llm.all_text()
    for k in range(n, TOTAL + 1):
        assert marker(k) not in text, f"请求里出现了第 {k} 章的内容"
    assert "MARKER_VOL" not in text
    assert "测试长篇" not in text
    assert "测试作者" not in text


def test_every_system_prompt_has_spoiler_guard(client, fake_llm, book_id, monkeypatch):
    monkeypatch.setattr(summaries, "MAX_CHAPTER_CHARS", 10)  # 让切块、合并请求也覆盖到
    set_progress(client, book_id, 5)
    generate_all(client, book_id)

    kinds = set()
    for call in fake_llm.calls:
        assert call[0]["role"] == "system"
        assert prompts.SPOILER_GUARD in call[0]["content"]
        kinds.add(call[0]["content"])
    assert len(kinds) == 2  # 切块 + 合并（每章都被切块）


# ---- 上下文范围（验收标准 4）----


def test_context_has_only_recent_k_summaries_and_prior_names(client, fake_llm, book_id, monkeypatch):
    monkeypatch.setenv("SUMMARY_CONTEXT_CHAPTERS", "2")
    set_progress(client, book_id, TOTAL)
    generate_all(client, book_id)

    context = context_part(call_for(fake_llm, 5))
    assert re.findall(r"SUM_CH\d+", context) == ["SUM_CH03", "SUM_CH04"]
    assert re.findall(r"NAME_CH\d+", context) == ["NAME_CH01", "NAME_CH02", "NAME_CH03", "NAME_CH04"]

    first = context_part(call_for(fake_llm, 1))
    assert "【人物名单】" not in first and "【最近几章摘要】" not in first


def test_skipped_chapters_are_not_counted_in_context(client, fake_llm, book_id, monkeypatch):
    monkeypatch.setenv("SUMMARY_CONTEXT_CHAPTERS", "2")
    set_progress(client, book_id, 6)
    client.put(f"/api/books/{book_id}/chapters/4/skipped", json={"skipped": True})
    generate_all(client, book_id)

    with pytest.raises(AssertionError):
        call_for(fake_llm, 4)
    context = context_part(call_for(fake_llm, 5))
    assert re.findall(r"SUM_CH\d+", context) == ["SUM_CH02", "SUM_CH03"]
    assert "NAME_CH04" not in context


# ---- 切块 ----


def test_long_chapter_is_chunked_then_merged(client, fake_llm, book_id, monkeypatch):
    monkeypatch.setenv("SUMMARY_CONTEXT_CHAPTERS", "2")
    set_progress(client, book_id, 4)
    generate_all(client, book_id)  # 第 1~3 章正常生成
    fake_llm.calls.clear()

    monkeypatch.setattr(summaries, "MAX_CHAPTER_CHARS", 10)
    res = client.post(f"/api/books/{book_id}/chapters/3/summary/regenerate")
    assert res.status_code == 200
    assert res.json()["summary"] == "MERGED"

    *chunk_calls, merge_call = fake_llm.calls
    assert len(chunk_calls) >= 2
    for call in chunk_calls:
        assert "SUM_CH01" in call[-1]["content"] and "SUM_CH02" in call[-1]["content"]
        assert "NAME_CH02" in call[-1]["content"]
    assert prompts.TASK_MERGE in merge_call[0]["content"]


def test_split_text():
    assert summaries.split_text("短文本", 10) == ["短文本"]
    assert summaries.split_text("aaaa\nbbbb\ncccc", 9) == ["aaaa\nbbbb", "cccc"]
    assert summaries.split_text("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]


# ---- 缓存和重新生成（验收标准 6）----


def test_cached_summaries_do_not_call_llm(client, fake_llm, book_id):
    set_progress(client, book_id, 4)
    generate_all(client, book_id)
    calls = len(fake_llm.calls)

    data = client.get(f"/api/books/{book_id}/summaries").json()
    assert data["missing"] == 0
    assert [ch["summary"]["summary"] for ch in data["chapters"]] == ["SUM_CH01", "SUM_CH02", "SUM_CH03"]
    assert "names" not in data["chapters"][0]["summary"]
    assert client.post(f"/api/books/{book_id}/summaries/next").json() == {"generated": None, "missing": 0}
    assert len(fake_llm.calls) == calls


def test_regenerate_bumps_version_and_keeps_later_chapters(client, fake_llm, book_id):
    set_progress(client, book_id, 5)
    generate_all(client, book_id)
    before = client.get(f"/api/books/{book_id}/summaries").json()["chapters"]

    fake_llm.queue.append(json.dumps({"summary": "NEW", "ending_state": "E", "names": []}))
    res = client.post(f"/api/books/{book_id}/chapters/2/summary/regenerate")
    assert res.json()["version"] == 2

    after = client.get(f"/api/books/{book_id}/summaries").json()["chapters"]
    assert after[1]["summary"]["summary"] == "NEW"
    assert after[2:] == before[2:]


def test_cannot_generate_unread_or_skipped(client, fake_llm, book_id):
    set_progress(client, book_id, 3)
    assert client.post(f"/api/books/{book_id}/chapters/3/summary/regenerate").status_code == 400
    client.put(f"/api/books/{book_id}/chapters/1/skipped", json={"skipped": True})
    assert client.post(f"/api/books/{book_id}/chapters/1/summary/regenerate").status_code == 400
    assert fake_llm.calls == []


def test_progress_rollback_hides_later_summaries(client, fake_llm, book_id):
    set_progress(client, book_id, 6)
    generate_all(client, book_id)
    set_progress(client, book_id, 3)

    data = client.get(f"/api/books/{book_id}/summaries").json()
    assert [ch["number"] for ch in data["chapters"]] == [1, 2]
    assert "SUM_CH03" not in json.dumps(data)


# ---- 模型返回异常 ----


def test_invalid_json_is_retried_once(client, fake_llm, book_id):
    set_progress(client, book_id, 2)
    fake_llm.queue.append("不是 JSON")
    generated = generate_all(client, book_id)
    assert len(fake_llm.calls) == 2
    assert generated[0]["summary"] == "SUM_CH01"


def test_invalid_twice_fails_without_caching(client, fake_llm, book_id):
    set_progress(client, book_id, 2)
    fake_llm.queue.extend(["不是 JSON", json.dumps({"summary": "", "ending_state": "", "names": []})])
    res = client.post(f"/api/books/{book_id}/summaries/next")
    assert res.status_code == 502
    assert client.get(f"/api/books/{book_id}/summaries").json()["missing"] == 1


def test_missing_api_key(client, book_id, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    set_progress(client, book_id, 2)
    res = client.post(f"/api/books/{book_id}/summaries/next")
    assert res.status_code == 503


def test_original_name_not_in_text_is_dropped(client, fake_llm, book_id):
    set_progress(client, book_id, 3)
    fake_llm.queue.append(
        json.dumps(
            {
                "summary": "S",
                "ending_state": "E",
                "names": [
                    {"name": "甲", "aliases": [], "original": "Invented Name"},
                    {"name": "乙", "aliases": [], "original": "MARKER_CH01"},
                ],
            }
        )
    )
    generate_all(client, book_id)

    context = context_part(call_for(fake_llm, 2))
    assert "Invented Name" not in context
    assert "乙（MARKER_CH01）" in context


# ---- 人物名单合并 ----


def person(name, aliases=(), original=""):
    return {"name": name, "aliases": list(aliases), "original": original}


def test_merge_names_dedupes_and_follows_renames():
    roster = summaries.merge_names(
        [
            [person("乔峰", ["乔帮主"]), person("段誉")],
            [person("乔峰"), person("伊丽莎白", original="Elizabeth")],
            [person("萧峰", ["乔峰"])],  # 文本揭示了新名字
        ]
    )
    assert roster == [
        person("萧峰", ["乔帮主", "乔峰"]),
        person("段誉"),
        person("伊丽莎白", original="Elizabeth"),
    ]


def test_merge_names_does_not_join_people_on_shared_generic_alias():
    roster = summaries.merge_names(
        [
            [person("西本雪穗", ["雪穗", "女孩"])],
            [person("川岛江利子", ["江利子", "女孩"])],
        ]
    )
    assert [p["name"] for p in roster] == ["西本雪穗", "川岛江利子"]
