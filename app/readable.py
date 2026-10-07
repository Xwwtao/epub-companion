"""防剧透边界：发给 LLM 的章节正文只能从这里取。

进度语义是"正在读第 N 章"，只有第 1 ~ N-1 章中未被标记跳过的章节算已读。
除导入时写库外，全项目只有本模块读取 chapters.text（tests/test_readable.py 有守护测试）。
"""

import json
import sqlite3
from dataclasses import dataclass

from app.db import DEFAULT_USER_ID


@dataclass(frozen=True)
class ReadableChapter:
    id: int
    number: int
    title: str
    parent_titles: list[str]
    text: str


def readable_chapters(conn: sqlite3.Connection, book_id: int) -> list[ReadableChapter]:
    rows = conn.execute(
        """
        SELECT c.id, c.number, c.title, c.parent_titles, c.text
        FROM chapters c JOIN books b ON b.id = c.book_id
        WHERE c.book_id = ? AND b.user_id = ?
          AND c.number < b.current_chapter AND c.skipped = 0
        ORDER BY c.number
        """,
        (book_id, DEFAULT_USER_ID),
    ).fetchall()
    return [
        ReadableChapter(
            id=row["id"],
            number=row["number"],
            title=row["title"],
            parent_titles=json.loads(row["parent_titles"]),
            text=row["text"],
        )
        for row in rows
    ]
