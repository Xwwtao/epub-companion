import pytest

from app import books
from tests import epub_builder


def upload(client, path):
    with path.open("rb") as f:
        return client.post("/api/books", files={"file": (path.name, f, "application/epub+zip")})


def test_upload_list_detail_delete(client, epubs, data_dir):
    res = upload(client, epubs["nested_zh"])
    assert res.status_code == 201
    book = res.json()
    assert book["title"] == "测试长篇"
    assert book["current_chapter"] == 1
    assert book["chapter_count"] == 7
    assert book["chapters"][2] == {
        "number": 3,
        "title": "第一章 开端",
        "parent_titles": ["第一卷 起"],
        "skipped": False,
        "mentions_book_info": False,
    }
    # 详情接口不返回章节正文
    assert "text" not in book["chapters"][0]

    books = client.get("/api/books").json()
    assert [b["id"] for b in books] == [book["id"]]

    cover = client.get(f"/api/books/{book['id']}/cover")
    assert cover.status_code == 200
    assert cover.content == epub_builder.PNG

    assert len(list((data_dir / "books").iterdir())) == 1
    assert len(list((data_dir / "covers").iterdir())) == 1

    assert client.delete(f"/api/books/{book['id']}").status_code == 204
    assert client.get("/api/books").json() == []
    assert client.get(f"/api/books/{book['id']}").status_code == 404
    assert list((data_dir / "books").iterdir()) == []
    assert list((data_dir / "covers").iterdir()) == []


def test_bad_upload_is_rejected_and_cleaned_up(client, tmp_path, data_dir):
    fake = tmp_path / "fake.epub"
    fake.write_text("这不是 EPUB")

    res = upload(client, fake)
    assert res.status_code == 400
    assert "不是有效的 EPUB" in res.json()["detail"]
    assert client.get("/api/books").json() == []
    assert list((data_dir / "books").iterdir()) == []


def test_book_without_cover(client, epubs):
    book = upload(client, epubs["english"]).json()
    assert book["has_cover"] is False
    assert client.get(f"/api/books/{book['id']}/cover").status_code == 404


def test_missing_book(client):
    assert client.get("/api/books/999").status_code == 404
    assert client.delete("/api/books/999").status_code == 404


def test_index_page_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "书架" in res.text


@pytest.mark.parametrize(
    ("media_type", "suffix"),
    [("image/jpeg", ".jpg"), ("image/jpg", ".jpg"), ("IMAGE/PNG", ".png"), ("image/x-unknown", ".img")],
)
def test_cover_extension_from_media_type(tmp_path, media_type, suffix):
    # 非标准的 image/jpg 很常见；存成 .img 的话封面接口会以 application/octet-stream 返回
    path = books._save_cover((b"data", media_type), tmp_path, "stem")
    assert path.suffix == suffix
    assert path.read_bytes() == b"data"


def test_health_reports_whether_llm_is_configured(client, monkeypatch):
    assert client.get("/api/health").json() == {"status": "ok", "llm_configured": False}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert client.get("/api/health").json() == {"status": "ok", "llm_configured": True}


def test_pages_and_their_assets_are_served(client):
    # 前端没有构建步骤，页面引用的脚本和样式写错路径时只能靠这个测试发现
    import re

    for page in ("/", "/book.html", "/summary.html"):
        res = client.get(page)
        assert res.status_code == 200, page
        assets = re.findall(r'(?:src|href)="((?:js/)?[\w.-]+\.(?:js|css))"', res.text)
        assert assets, page
        for asset in assets:
            assert client.get(f"/{asset}").status_code == 200, f"{page} 引用的 {asset} 不存在"
