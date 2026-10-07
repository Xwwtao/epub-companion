import re
from pathlib import Path

import pytest

from app import db
from app.readable import readable_chapters
from tests.epub_builder import marker

APP_DIR = Path(__file__).parent.parent / "app"
TOTAL = 7  # nested_zh 展平后共 7 章


@pytest.fixture
def book_id(client, epubs):
    with epubs["nested_zh"].open("rb") as f:
        return client.post("/api/books", files={"file": ("b.epub", f)}).json()["id"]


@pytest.fixture
def conn(client):
    c = db.connect()
    yield c
    c.close()


def set_progress(client, book_id, n):
    res = client.put(f"/api/books/{book_id}/progress", json={"chapter": n})
    assert res.status_code == 200, res.text


def set_skipped(client, book_id, number, skipped=True):
    res = client.put(f"/api/books/{book_id}/chapters/{number}/skipped", json={"skipped": skipped})
    assert res.status_code == 200, res.text


def all_text(chapters) -> str:
    return "\n".join(ch.text for ch in chapters)


def assert_no_marker_from(chapters, n):
    text = all_text(chapters)
    for k in range(n, TOTAL + 1):
        assert marker(k) not in text, f"出现了第 {k} 章的内容"


def test_first_chapter_means_nothing_read(conn, book_id):
    assert readable_chapters(conn, book_id) == []


def test_only_chapters_before_current(client, conn, book_id):
    set_progress(client, book_id, 5)

    chapters = readable_chapters(conn, book_id)
    assert [ch.number for ch in chapters] == [1, 2, 3, 4]
    assert [marker(ch.number) in ch.text for ch in chapters] == [True] * 4
    assert_no_marker_from(chapters, 5)


def test_current_chapter_is_never_readable(client, conn, book_id):
    set_progress(client, book_id, TOTAL)

    chapters = readable_chapters(conn, book_id)
    assert [ch.number for ch in chapters] == list(range(1, TOTAL))
    assert_no_marker_from(chapters, TOTAL)


def test_skipped_chapters_are_excluded_without_renumbering(client, conn, book_id):
    set_progress(client, book_id, 5)
    set_skipped(client, book_id, 1)  # 版权页
    set_skipped(client, book_id, 2)  # 序言

    chapters = readable_chapters(conn, book_id)
    assert [ch.number for ch in chapters] == [3, 4]
    assert [ch.title for ch in chapters] == ["第一章 开端", "第二章 相遇"]
    assert chapters[0].parent_titles == ["第一卷 起"]

    set_skipped(client, book_id, 1, skipped=False)
    assert [ch.number for ch in readable_chapters(conn, book_id)] == [1, 3, 4]


def test_moving_progress_back(client, conn, book_id):
    set_progress(client, book_id, TOTAL)
    set_progress(client, book_id, 3)

    chapters = readable_chapters(conn, book_id)
    assert [ch.number for ch in chapters] == [1, 2]
    assert_no_marker_from(chapters, 3)


def test_other_books_are_not_mixed_in(client, conn, epubs, book_id):
    with epubs["english"].open("rb") as f:
        other = client.post("/api/books", files={"file": ("e.epub", f)}).json()["id"]
    set_progress(client, other, 3)
    set_progress(client, book_id, 2)

    assert [ch.number for ch in readable_chapters(conn, book_id)] == [1]
    assert "Elizabeth" not in all_text(readable_chapters(conn, book_id))


@pytest.mark.parametrize("n", [0, TOTAL + 1, -1])
def test_progress_out_of_range(client, book_id, n):
    res = client.put(f"/api/books/{book_id}/progress", json={"chapter": n})
    assert res.status_code == 400
    assert client.get(f"/api/books/{book_id}").json()["current_chapter"] == 1


def test_progress_and_skip_on_missing_targets(client, book_id):
    assert client.put("/api/books/999/progress", json={"chapter": 1}).status_code == 404
    assert client.put(f"/api/books/{book_id}/chapters/99/skipped", json={"skipped": True}).status_code == 404
    assert client.put("/api/books/999/chapters/1/skipped", json={"skipped": True}).status_code == 404


def test_progress_and_skip_are_persisted(client, book_id):
    set_progress(client, book_id, 4)
    set_skipped(client, book_id, 2)

    book = client.get(f"/api/books/{book_id}").json()
    assert book["current_chapter"] == 4
    assert [ch["skipped"] for ch in book["chapters"]] == [False, True, False, False, False, False, False]


# ---- 边界守护：除 readable.py 外，任何代码都不得 SELECT chapters.text ----

SELECT_FROM_CHAPTERS = re.compile(r"SELECT\b(.*?)\bFROM\s+chapters\b", re.IGNORECASE | re.DOTALL)


def selects_chapter_text(source: str) -> bool:
    for match in SELECT_FROM_CHAPTERS.finditer(source):
        columns = match.group(1)
        # COUNT(*) 不算，裸的 * 或 text 列才算
        if re.search(r"\btext\b", columns, re.IGNORECASE) or re.search(r"(?<!\()\*", columns):
            return True
    return False


def test_guard_detects_violations():
    assert selects_chapter_text('conn.execute("SELECT number, text FROM chapters")')
    assert selects_chapter_text('"SELECT c.text\n FROM chapters c"')
    assert selects_chapter_text('"SELECT * FROM chapters"')
    assert not selects_chapter_text('"SELECT number, title FROM chapters"')
    assert not selects_chapter_text('"SELECT COUNT(*) FROM chapters"')
    assert not selects_chapter_text('"INSERT INTO chapters (book_id, text) VALUES (?, ?)"')


def test_only_readable_module_reads_chapter_text():
    offenders = [
        path.relative_to(APP_DIR.parent).as_posix()
        for path in APP_DIR.rglob("*.py")
        if path.name != "readable.py" and selects_chapter_text(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "章节正文只能经由 app/readable.py 的 readable_chapters 读取"
