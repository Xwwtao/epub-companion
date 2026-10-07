"""整体回顾和上次读到哪（spec §4.3b、§4.3c）。

整体回顾只基于已读章节的逐章摘要，不读原文。上次读到哪直接取摘要缓存，不调用模型。
"""

import hashlib
import json
import sqlite3

from app import prompts
from app.llm import LLMClient, complete_json
from app.readable import readable_chapters
from app.summaries import load_summaries

# 摘要总字数超过这个值时，先分组汇总再合并，避免超出模型上下文
RECAP_MAX_INPUT_CHARS = 60000


def get_recap(conn: sqlite3.Connection, client: LLMClient, book_id: int, current_chapter: int) -> dict:
    readable = readable_chapters(conn, book_id)
    if not readable:
        return {"status": "empty"}
    stored = load_summaries(conn, readable)
    missing = sum(1 for ch in readable if ch.id not in stored)
    if missing:
        return {"status": "incomplete", "missing": missing}

    # 进度、参与章节（含跳过的变化）、任一章摘要内容变化，都会让哈希变化。
    # 用摘要内容而不是版本号：摘要被删掉重建时版本号会从 1 重新开始，可能撞上旧缓存
    source = [(ch.id, stored[ch.id]["summary"]) for ch in readable]
    source_hash = hashlib.sha256(json.dumps(source).encode()).hexdigest()
    row = conn.execute(
        "SELECT content, created_at FROM recaps"
        " WHERE book_id = ? AND up_to_chapter = ? AND source_versions_hash = ?",
        (book_id, current_chapter, source_hash),
    ).fetchone()
    if row is not None:
        return {"status": "ok", "content": row["content"], "created_at": row["created_at"]}

    items = [(prompts.chapter_label(ch.parent_titles, ch.title), stored[ch.id]["summary"]) for ch in readable]
    meta = {"book_id": book_id, "chapter": current_chapter}
    groups = _group(items, RECAP_MAX_INPUT_CHARS)
    if len(groups) > 1:
        items = [
            (f"第 {i} 部分", complete_json(client, "recap_group", prompts.recap_messages(g, group=True),
                                         {**meta, "group": i}, _validate)["recap"])
            for i, g in enumerate(groups, start=1)
        ]
    content = complete_json(client, "recap", prompts.recap_messages(items), meta, _validate)["recap"].strip()

    conn.execute(
        "INSERT OR REPLACE INTO recaps (book_id, up_to_chapter, source_versions_hash, content, model)"
        " VALUES (?, ?, ?, ?, ?)",
        (book_id, current_chapter, source_hash, content, client.model),
    )
    conn.commit()
    created_at = conn.execute(
        "SELECT created_at FROM recaps WHERE book_id = ? AND up_to_chapter = ? AND source_versions_hash = ?",
        (book_id, current_chapter, source_hash),
    ).fetchone()["created_at"]
    return {"status": "ok", "content": content, "created_at": created_at}


def last_read(conn: sqlite3.Connection, book_id: int) -> dict:
    """最近 1~2 个已读章节的摘要；最后一章的 ending_state 用来接上断点。"""
    recent = readable_chapters(conn, book_id)[-2:]
    stored = load_summaries(conn, recent)
    return {
        "chapters": [
            {
                "number": ch.number,
                "title": ch.title,
                "parent_titles": ch.parent_titles,
                "summary": stored[ch.id]["summary"] if ch.id in stored else None,
                "ending_state": stored[ch.id]["ending_state"] if ch.id in stored else None,
            }
            for ch in recent
        ]
    }


def _group(items: list[tuple[str, str]], limit: int) -> list[list[tuple[str, str]]]:
    groups: list[list[tuple[str, str]]] = [[]]
    size = 0
    for item in items:
        length = len(item[0]) + len(item[1])
        if groups[-1] and size + length > limit:
            groups.append([])
            size = 0
        groups[-1].append(item)
        size += length
    return groups


def _validate(data) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("recap"), str) or not data["recap"].strip():
        raise ValueError("缺少 recap")
