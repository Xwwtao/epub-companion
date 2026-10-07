import sqlite3

from app import db
from app.books import mentions_book_info
from tests import epub_builder


def test_copyright_page_is_skipped_by_default(client, tmp_path):
    path = epub_builder.with_book_info(tmp_path / "info.epub")
    with path.open("rb") as f:
        book = client.post("/api/books", files={"file": ("info.epub", f)}).json()

    flags = [(ch["title"], ch["mentions_book_info"], ch["skipped"]) for ch in book["chapters"]]
    assert flags == [
        ("版权信息", True, True),
        ("第一章", False, False),
        ("第二章", True, False),  # 太长，可能是正文，只提醒
        ("第三章", False, False),
    ]

    # 用户可以取消默认的跳过，提醒标记保留
    res = client.put(f"/api/books/{book['id']}/chapters/1/skipped", json={"skipped": False})
    assert res.json()["chapters"][0] == {
        "number": 1, "title": "版权信息", "parent_titles": [], "skipped": False, "mentions_book_info": True,
    }


def test_mentions_book_info():
    text = "书 名：白夜行\n作 者：〔日〕东野圭吾\n译 者：刘姿君"
    assert mentions_book_info(text, "白夜行", "(日)东野圭吾")
    assert mentions_book_info(text, "白夜行", "东野圭吾 著")
    assert not mentions_book_info(text, "白夜行", None)
    assert not mentions_book_info("白夜行里没有作者名", "白夜行", "东野圭吾")
    assert not mentions_book_info("东野圭吾", "白夜行", "东野圭吾")


def test_old_database_gets_new_column(data_dir):
    data_dir.mkdir(parents=True)
    old = sqlite3.connect(data_dir / "companion.db")
    old.execute(
        "CREATE TABLE chapters (id INTEGER PRIMARY KEY, book_id INTEGER, number INTEGER, title TEXT,"
        " parent_titles TEXT, start_href TEXT, skipped INTEGER, text TEXT)"
    )
    old.close()

    db.init_db()
    conn = db.connect()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(chapters)")}
    conn.close()
    assert "mentions_book_info" in columns
