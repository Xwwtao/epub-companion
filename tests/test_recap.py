import json

import pytest

from app import prompts, recap
from tests.test_summaries import book_id, generate_all, set_progress  # noqa: F401  (book_id 是 fixture)


def get_recap(client, book_id):
    res = client.get(f"/api/books/{book_id}/recap")
    assert res.status_code == 200, res.text
    return res.json()


def recap_calls(fake):
    return [call for call in fake.calls if prompts.RECAP_JSON_FORMAT in call[0]["content"]]


# ---- 整体回顾 ----

def test_recap_uses_only_read_summaries_not_text(client, fake_llm, book_id):
    set_progress(client, book_id, 5)
    generate_all(client, book_id)

    data = get_recap(client, book_id)
    assert data["status"] == "ok"
    assert data["content"] == "RECAP SUM_CH01 SUM_CH02 SUM_CH03 SUM_CH04"

    [call] = recap_calls(fake_llm)
    text = json.dumps(call, ensure_ascii=False)
    assert "MARKER_" not in text  # 没读原文
    assert "测试长篇" not in text and "测试作者" not in text
    assert prompts.SPOILER_GUARD in call[0]["content"]


def test_recap_is_cached(client, fake_llm, book_id):
    set_progress(client, book_id, 4)
    generate_all(client, book_id)
    first = get_recap(client, book_id)
    calls = len(fake_llm.calls)

    assert get_recap(client, book_id) == first
    assert len(fake_llm.calls) == calls


def test_regenerating_a_summary_invalidates_recap(client, fake_llm, book_id):
    set_progress(client, book_id, 4)
    generate_all(client, book_id)
    get_recap(client, book_id)

    fake_llm.queue.append(json.dumps({"summary": "SUM_CH99", "ending_state": "E", "names": []}))
    client.post(f"/api/books/{book_id}/chapters/2/summary/regenerate")

    assert get_recap(client, book_id)["content"] == "RECAP SUM_CH01 SUM_CH99 SUM_CH03"
    assert len(recap_calls(fake_llm)) == 2


def test_rebuilt_summaries_invalidate_recap_even_with_same_version(client, fake_llm, book_id):
    from app import db

    set_progress(client, book_id, 3)
    generate_all(client, book_id)
    get_recap(client, book_id)

    conn = db.connect()
    conn.execute("DELETE FROM chapter_summaries")
    conn.commit()
    conn.close()
    fake_llm.queue.append(json.dumps({"summary": "SUM_CH77", "ending_state": "E", "names": []}))
    generate_all(client, book_id)  # 版本号重新从 1 开始

    assert get_recap(client, book_id)["content"] == "RECAP SUM_CH77 SUM_CH02"


def test_progress_change_invalidates_and_rollback_hides_later_content(client, fake_llm, book_id):
    set_progress(client, book_id, 6)
    generate_all(client, book_id)
    assert "SUM_CH05" in get_recap(client, book_id)["content"]

    set_progress(client, book_id, 3)
    data = get_recap(client, book_id)
    assert data["content"] == "RECAP SUM_CH01 SUM_CH02"
    assert "SUM_CH03" not in json.dumps(recap_calls(fake_llm)[-1])

    last = client.get(f"/api/books/{book_id}/last-read").json()
    assert [ch["number"] for ch in last["chapters"]] == [1, 2]

    # 回到原进度时，之前的回顾缓存仍可用
    set_progress(client, book_id, 6)
    calls = len(fake_llm.calls)
    assert "SUM_CH05" in get_recap(client, book_id)["content"]
    assert len(fake_llm.calls) == calls


def test_skipping_a_chapter_invalidates_recap(client, fake_llm, book_id):
    set_progress(client, book_id, 4)
    generate_all(client, book_id)
    get_recap(client, book_id)

    client.put(f"/api/books/{book_id}/chapters/1/skipped", json={"skipped": True})
    assert get_recap(client, book_id)["content"] == "RECAP SUM_CH02 SUM_CH03"


def test_recap_waits_for_all_summaries(client, fake_llm, book_id):
    set_progress(client, book_id, 4)
    client.post(f"/api/books/{book_id}/summaries/next")

    assert get_recap(client, book_id) == {"status": "incomplete", "missing": 2}
    assert recap_calls(fake_llm) == []


def test_recap_when_nothing_read(client, fake_llm, book_id):
    assert get_recap(client, book_id) == {"status": "empty"}
    assert fake_llm.calls == []


def test_long_input_is_grouped_then_merged(client, fake_llm, book_id, monkeypatch):
    set_progress(client, book_id, TOTAL := 7)
    generate_all(client, book_id)
    monkeypatch.setattr(recap, "RECAP_MAX_INPUT_CHARS", 30)

    data = get_recap(client, book_id)
    calls = recap_calls(fake_llm)
    assert len(calls) > 2  # 至少两组 + 最后合并
    assert all(prompts.TASK_RECAP_GROUP in c[0]["content"] for c in calls[:-1])
    assert prompts.TASK_RECAP in calls[-1][0]["content"]
    assert data["content"] == "RECAP " + " ".join(f"SUM_CH{n:02d}" for n in range(1, TOTAL))


def test_recap_group():
    items = [("a", "x" * 10), ("b", "y" * 10), ("c", "z" * 10)]
    assert recap._group(items, 25) == [items[:2], items[2:]]
    assert recap._group(items, 5) == [[i] for i in items]  # 单条超长也自成一组


# ---- 上次读到哪 ----

def test_last_read_shows_two_latest_without_llm(client, fake_llm, book_id):
    set_progress(client, book_id, 5)
    generate_all(client, book_id)
    calls = len(fake_llm.calls)

    data = client.get(f"/api/books/{book_id}/last-read").json()
    assert [(ch["number"], ch["summary"], ch["ending_state"]) for ch in data["chapters"]] == [
        (3, "SUM_CH03", "END_CH03"),
        (4, "SUM_CH04", "END_CH04"),
    ]
    assert len(fake_llm.calls) == calls


@pytest.mark.parametrize(("n", "expected"), [(1, []), (2, [1])])
def test_last_read_at_the_beginning(client, book_id, n, expected):
    set_progress(client, book_id, n)
    data = client.get(f"/api/books/{book_id}/last-read").json()
    assert [ch["number"] for ch in data["chapters"]] == expected


def test_last_read_skips_skipped_and_handles_missing_summary(client, book_id):
    set_progress(client, book_id, 5)
    client.put(f"/api/books/{book_id}/chapters/4/skipped", json={"skipped": True})

    data = client.get(f"/api/books/{book_id}/last-read").json()
    assert [(ch["number"], ch["summary"]) for ch in data["chapters"]] == [(2, None), (3, None)]
