import sqlite3

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    author TEXT,
    file_path TEXT NOT NULL,          -- 相对 DATA_DIR
    cover_path TEXT,                  -- 相对 DATA_DIR，仅用于界面展示
    current_chapter INTEGER NOT NULL DEFAULT 1,  -- 正在读第 N 章，1 ~ N-1 为已读
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,          -- 展平后的章节序号，从 1 开始
    title TEXT NOT NULL,
    parent_titles TEXT NOT NULL DEFAULT '[]',  -- JSON 数组，如 ["第一卷"]
    start_href TEXT NOT NULL,
    skipped INTEGER NOT NULL DEFAULT 0,
    mentions_book_info INTEGER NOT NULL DEFAULT 0,  -- 正文同时含书名和作者（如版权页），导入时算好
    text TEXT NOT NULL,               -- 只允许经由 readable_chapters 读取
    UNIQUE (book_id, number)
);

CREATE TABLE IF NOT EXISTS chapter_summaries (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER NOT NULL UNIQUE REFERENCES chapters(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    ending_state TEXT NOT NULL,
    names TEXT NOT NULL DEFAULT '[]',  -- JSON：[{"name", "aliases", "original"}]
    version INTEGER NOT NULL DEFAULT 1,  -- 每次重新生成 +1
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recaps (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    up_to_chapter INTEGER NOT NULL,     -- 生成时的进度 N
    source_versions_hash TEXT NOT NULL, -- 参与汇总的各章 (chapter_id, version) 的哈希
    content TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (book_id, up_to_chapter, source_versions_hash)
);
"""

DEFAULT_USER_ID = 1


def connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # 每个请求一个连接；FastAPI 的依赖和接口可能跑在线程池的不同线程上
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # SQLite 默认不执行外键约束；删书时要靠 ON DELETE CASCADE 连带删掉章节、摘要和回顾
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    settings = get_settings()
    settings.books_dir.mkdir(parents=True, exist_ok=True)
    settings.covers_dir.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    # CREATE TABLE IF NOT EXISTS 不会给已有的表加列，旧库要手动补；新列都带默认值
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(chapters)")}
    if "mentions_book_info" not in columns:
        conn.execute("ALTER TABLE chapters ADD COLUMN mentions_book_info INTEGER NOT NULL DEFAULT 0")
