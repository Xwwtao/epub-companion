import json
import mimetypes
import re
import sqlite3
import uuid
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings
from app.db import DEFAULT_USER_ID
from app.epub_parser import ParsedBook, ParsedChapter, parse_epub

# 正文同时含书名和作者、且不超过这个字数的页面（版权页、扉页之类）导入时默认跳过，
# 避免书名随正文发给模型（spec §2）。更长的页面只在界面上提醒。
AUTO_SKIP_MAX_CHARS = 3000

# EPUB 里常见、但 mimetypes 不认识的非标准写法。扩展名决定了封面接口返回的 Content-Type
NONSTANDARD_IMAGE_TYPES = {"image/jpg": ".jpg", "image/pjpeg": ".jpg", "image/x-png": ".png"}


def mentions_book_info(text: str, title: str, author: str | None) -> bool:
    # 要求书名和作者同时出现：只出现书名的正文很常见（人物提到书名、章节标题重复），不该被标记
    if not author:
        return False
    # 去掉国籍、"著"之类的修饰，如 "(日)东野圭吾 著" → "东野圭吾"
    core_author = re.sub(r"[(（〔\[【][^)）〕\]】]*[)）〕\]】]|\s*[著译编]$", "", author).strip()
    return bool(title) and bool(core_author) and title in text and core_author in text


def import_book(conn: sqlite3.Connection, upload: BinaryIO) -> int:
    """保存上传的 EPUB 并解析入库，返回 book_id。

    任何一步失败都回滚数据库并删掉已写出的文件，不留下半本书。
    """
    settings = get_settings()
    stem = uuid.uuid4().hex  # 文件名不用书名：书名可能含路径字符，也可能重名
    epub_path = settings.books_dir / f"{stem}.epub"
    _save_upload(upload, epub_path)

    cover_path: Path | None = None
    try:
        parsed = parse_epub(epub_path)
        cover_path = _save_cover(parsed.cover, settings.covers_dir, stem)
        book_id = _insert_book(conn, parsed, epub_path, cover_path)
        conn.commit()
    except BaseException:
        conn.rollback()
        epub_path.unlink(missing_ok=True)
        if cover_path:
            cover_path.unlink(missing_ok=True)
        raise
    return book_id


def _save_upload(upload: BinaryIO, path: Path) -> None:
    # 分块写盘，避免整本书读进内存
    with path.open("wb") as f:
        while chunk := upload.read(1024 * 1024):
            f.write(chunk)


def _save_cover(cover: tuple[bytes, str] | None, covers_dir: Path, stem: str) -> Path | None:
    if not cover:
        return None
    content, media_type = cover
    media_type = media_type.lower()
    ext = mimetypes.guess_extension(media_type) or NONSTANDARD_IMAGE_TYPES.get(media_type, ".img")
    path = covers_dir / f"{stem}{ext}"
    path.write_bytes(content)
    return path


def _insert_book(conn: sqlite3.Connection, parsed: ParsedBook, epub_path: Path, cover_path: Path | None) -> int:
    data_dir = get_settings().data_dir
    # 库里存相对 DATA_DIR 的路径，数据目录整体搬家后仍然可用
    cur = conn.execute(
        "INSERT INTO books (user_id, title, author, file_path, cover_path) VALUES (?, ?, ?, ?, ?)",
        (
            DEFAULT_USER_ID,
            parsed.title,
            parsed.author,
            str(epub_path.relative_to(data_dir)),
            str(cover_path.relative_to(data_dir)) if cover_path else None,
        ),
    )
    book_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO chapters (book_id, number, title, parent_titles, start_href,"
        " mentions_book_info, skipped, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [_chapter_row(book_id, number, ch, parsed) for number, ch in enumerate(parsed.chapters, start=1)],
    )
    return book_id


def _chapter_row(book_id: int, number: int, ch: ParsedChapter, parsed: ParsedBook) -> tuple:
    mentions = mentions_book_info(ch.text, parsed.title, parsed.author)
    skipped = mentions and len(ch.text) <= AUTO_SKIP_MAX_CHARS
    parent_titles = json.dumps(ch.parent_titles, ensure_ascii=False)
    return (book_id, number, ch.title, parent_titles, ch.start_href, int(mentions), int(skipped), ch.text)


# 书架和详情共用的书籍字段。章节数用子查询现算，不单独存，删改章节时不会对不上
_BOOK_SELECT = """
    SELECT b.id, b.title, b.author, b.current_chapter, b.cover_path IS NOT NULL AS has_cover,
           (SELECT COUNT(*) FROM chapters c WHERE c.book_id = b.id) AS chapter_count
    FROM books b
"""


def list_books(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        _BOOK_SELECT + " WHERE b.user_id = ? ORDER BY b.created_at DESC, b.id DESC",
        (DEFAULT_USER_ID,),
    ).fetchall()
    return [_book_dict(row) for row in rows]


def get_book(conn: sqlite3.Connection, book_id: int) -> dict | None:
    row = conn.execute(_BOOK_SELECT + " WHERE b.id = ? AND b.user_id = ?", (book_id, DEFAULT_USER_ID)).fetchone()
    if row is None:
        return None
    book = _book_dict(row)
    # 章节列表不带正文
    chapters = conn.execute(
        "SELECT number, title, parent_titles, skipped, mentions_book_info"
        " FROM chapters WHERE book_id = ? ORDER BY number",
        (book_id,),
    ).fetchall()
    book["chapters"] = [
        {
            "number": ch["number"],
            "title": ch["title"],
            "parent_titles": json.loads(ch["parent_titles"]),
            "skipped": bool(ch["skipped"]),
            "mentions_book_info": bool(ch["mentions_book_info"]),
        }
        for ch in chapters
    ]
    return book


def get_current_chapter(conn: sqlite3.Connection, book_id: int) -> int | None:
    """只取进度，不查章节列表；书不存在返回 None。"""
    row = conn.execute(
        "SELECT current_chapter FROM books WHERE id = ? AND user_id = ?", (book_id, DEFAULT_USER_ID)
    ).fetchone()
    return None if row is None else row["current_chapter"]


class InvalidProgress(ValueError):
    pass


def set_progress(conn: sqlite3.Connection, book_id: int, chapter: int) -> bool:
    """设置"正在读第 N 章"。书不存在返回 False；N 超出范围抛 InvalidProgress。"""
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM chapters c WHERE c.book_id = b.id) AS chapter_count"
        " FROM books b WHERE b.id = ? AND b.user_id = ?",
        (book_id, DEFAULT_USER_ID),
    ).fetchone()
    if row is None:
        return False
    if not 1 <= chapter <= row["chapter_count"]:
        raise InvalidProgress(f"章节序号应在 1 ~ {row['chapter_count']} 之间")
    conn.execute("UPDATE books SET current_chapter = ? WHERE id = ?", (chapter, book_id))
    conn.commit()
    return True


def set_skipped(conn: sqlite3.Connection, book_id: int, number: int, skipped: bool) -> bool:
    cur = conn.execute(
        "UPDATE chapters SET skipped = ? WHERE number = ? AND book_id ="
        " (SELECT id FROM books WHERE id = ? AND user_id = ?)",
        (int(skipped), number, book_id, DEFAULT_USER_ID),
    )
    conn.commit()
    return cur.rowcount > 0


def get_cover_path(conn: sqlite3.Connection, book_id: int) -> Path | None:
    row = conn.execute(
        "SELECT cover_path FROM books WHERE id = ? AND user_id = ?", (book_id, DEFAULT_USER_ID)
    ).fetchone()
    if row is None or row["cover_path"] is None:
        return None
    return get_settings().data_dir / row["cover_path"]


def delete_book(conn: sqlite3.Connection, book_id: int) -> bool:
    row = conn.execute(
        "SELECT file_path, cover_path FROM books WHERE id = ? AND user_id = ?", (book_id, DEFAULT_USER_ID)
    ).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()
    data_dir = get_settings().data_dir
    for rel in (row["file_path"], row["cover_path"]):
        if rel:
            (data_dir / rel).unlink(missing_ok=True)
    return True


def _book_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "current_chapter": row["current_chapter"],
        "chapter_count": row["chapter_count"],
        "has_cover": bool(row["has_cover"]),
    }
