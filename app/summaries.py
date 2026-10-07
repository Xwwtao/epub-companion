"""逐章摘要：上下文组装、切块、缓存（spec §4.3a）。

章节正文和已读范围只从 readable_chapters 取。生成第 k 章时只带第 k 章之前的
最近 K 章摘要，以及由第 k 章之前各章 names 合并出的人物名单。
"""

import json
import sqlite3

from app import prompts
from app.config import get_settings
from app.llm import LLMClient, complete_json
from app.readable import ReadableChapter, readable_chapters

# 超过这个字数的章节先切块分别摘要再合并
MAX_CHAPTER_CHARS = 30000


class NotReadable(Exception):
    """目标章节不在已读范围内（未读或已跳过）。"""


def list_summaries(conn: sqlite3.Connection, book_id: int) -> dict:
    readable = readable_chapters(conn, book_id)
    stored = _load(conn, readable)
    chapters = [
        {
            "number": ch.number,
            "title": ch.title,
            "parent_titles": ch.parent_titles,
            "summary": _public(stored[ch.id]) if ch.id in stored else None,
        }
        for ch in readable
    ]
    return {"chapters": chapters, "missing": sum(1 for ch in readable if ch.id not in stored)}


def generate_next(conn: sqlite3.Connection, client: LLMClient, book_id: int) -> dict:
    """生成下一个缺失的章节摘要；前端循环调用直到 missing 为 0。"""
    readable = readable_chapters(conn, book_id)
    stored = _load(conn, readable)
    missing = [ch for ch in readable if ch.id not in stored]
    if not missing:
        return {"generated": None, "missing": 0}
    generated = _generate(conn, client, book_id, readable, stored, missing[0])
    return {"generated": generated, "missing": len(missing) - 1}


def regenerate(conn: sqlite3.Connection, client: LLMClient, book_id: int, number: int) -> dict:
    readable = readable_chapters(conn, book_id)
    target = next((ch for ch in readable if ch.number == number), None)
    if target is None:
        raise NotReadable(f"第 {number} 章不在已读范围内")
    return _generate(conn, client, book_id, readable, _load(conn, readable), target)


def _generate(conn, client, book_id, readable: list[ReadableChapter], stored: dict, target) -> dict:
    prior = [(ch, stored[ch.id]) for ch in readable if ch.number < target.number and ch.id in stored]
    k = get_settings().summary_context_chapters
    recent = [(prompts.chapter_label(ch.parent_titles, ch.title), s["summary"]) for ch, s in prior[-k:]] if k > 0 else []
    roster = merge_names(s["names"] for _, s in prior)

    label = prompts.chapter_label(target.parent_titles, target.title)
    meta = {"book_id": book_id, "chapter": target.number}
    chunks = split_text(target.text, MAX_CHAPTER_CHARS)
    if len(chunks) == 1:
        result = complete_json(
            client, "chapter", prompts.chapter_messages(label, target.text, recent, roster), meta, validate_summary
        )
    else:
        parts = [
            complete_json(
                client,
                "chunk",
                prompts.chunk_messages(label, chunk, i, len(chunks), recent, roster),
                {**meta, "chunk": i},
                validate_summary,
            )
            for i, chunk in enumerate(chunks, start=1)
        ]
        result = complete_json(client, "merge", prompts.merge_messages(label, parts, roster), meta, validate_summary)

    names = _normalize_names(result["names"])
    # 外文原名必须真的出现在本章原文里，否则是模型凭记忆补的，不能进入人物名单
    for p in names:
        if p["original"] and p["original"] not in target.text:
            p["original"] = ""
    conn.execute(
        """
        INSERT INTO chapter_summaries (chapter_id, summary, ending_state, names, model)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (chapter_id) DO UPDATE SET
            summary = excluded.summary, ending_state = excluded.ending_state, names = excluded.names,
            model = excluded.model, version = chapter_summaries.version + 1, created_at = CURRENT_TIMESTAMP
        """,
        (target.id, result["summary"].strip(), result["ending_state"].strip(),
         json.dumps(names, ensure_ascii=False), client.model),
    )
    conn.commit()
    saved = _load(conn, [target])[target.id]
    return {"number": target.number, "title": target.title, "parent_titles": target.parent_titles, **_public(saved)}


def validate_summary(data) -> None:
    if not isinstance(data, dict):
        raise ValueError("不是 JSON 对象")
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        raise ValueError("缺少 summary")
    if not isinstance(data.get("ending_state"), str):
        raise ValueError("缺少 ending_state")
    names = data.get("names")
    if not isinstance(names, list) or not all(
        isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"].strip() for p in names
    ):
        raise ValueError("names 格式不对")


def merge_names(names_lists) -> list[dict]:
    """按章节顺序合并人物名单；后出现的称谓为准，旧称谓并入别名。

    只有一方的正式称谓出现在另一方的名字或别名里才算同一人。两边别名有交集不算：
    "女孩""老板"这类泛称会把不同的人并到一起，制造原文没有的身份关联。
    """
    roster: list[dict] = []
    for names in names_lists:
        for person in names:
            existing = next(
                (
                    r for r in roster
                    if person["name"] == r["name"] or person["name"] in r["aliases"] or r["name"] in person["aliases"]
                ),
                None,
            )
            if existing is None:
                roster.append({"name": person["name"], "aliases": list(person["aliases"]), "original": person["original"]})
                continue
            aliases = [*existing["aliases"], existing["name"], *person["aliases"]]
            existing["name"] = person["name"]
            existing["aliases"] = list(dict.fromkeys(a for a in aliases if a != person["name"]))
            existing["original"] = person["original"] or existing["original"]
    return roster


def split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # 单行超长时硬切
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if current and len(current) + 1 + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def _normalize_names(names: list[dict]) -> list[dict]:
    result = []
    for p in names:
        aliases = p.get("aliases") if isinstance(p.get("aliases"), list) else []
        original = p.get("original") if isinstance(p.get("original"), str) else ""
        result.append({
            "name": p["name"].strip(),
            "aliases": [a.strip() for a in aliases if isinstance(a, str) and a.strip()],
            "original": original.strip(),
        })
    return result


def _load(conn: sqlite3.Connection, chapters: list[ReadableChapter]) -> dict[int, dict]:
    if not chapters:
        return {}
    ids = [ch.id for ch in chapters]
    rows = conn.execute(
        f"SELECT chapter_id, summary, ending_state, names, version, model, created_at"
        f" FROM chapter_summaries WHERE chapter_id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    return {row["chapter_id"]: {**dict(row), "names": json.loads(row["names"])} for row in rows}


def _public(stored: dict) -> dict:
    # 人物名单只在内部用来统一称谓，不返回给界面
    return {k: stored[k] for k in ("summary", "ending_state", "version", "model", "created_at")}
