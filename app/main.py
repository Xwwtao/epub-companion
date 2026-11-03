import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import books, db, recap, summaries
from app.config import get_settings
from app.epub_parser import EpubError
from app.llm import LLMClient, LLMError, deepseek_client

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="EPUB 读书伴侣", lifespan=lifespan)

# 默认前后端同源（静态页面由本服务提供），不需要 CORS。前端单独部署到别的域名时，
# 在 CORS_ORIGINS 里列出它的地址；允许携带 Cookie，为以后的登录预留
if _cors_origins := get_settings().cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def get_conn() -> Iterator[sqlite3.Connection]:
    # 每个请求一个连接，请求结束就关；SQLite 连接很便宜，这样不用操心跨线程共享
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.get("/api/health")
def health() -> dict:
    """存活检查，供部署时的负载均衡 / 监控使用；前端也用它判断能不能生成摘要。"""
    return {"status": "ok", "llm_configured": bool(get_settings().deepseek_api_key)}


@app.post("/api/books", status_code=201)
def upload_book(file: UploadFile, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        book_id = books.import_book(conn, file.file)
    except EpubError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return books.get_book(conn, book_id)


@app.get("/api/books")
def list_books(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return books.list_books(conn)


@app.get("/api/books/{book_id}")
def get_book(book_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    book = books.get_book(conn, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="书不存在")
    return book


@app.get("/api/books/{book_id}/cover")
def get_cover(book_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> FileResponse:
    path = books.get_cover_path(conn, book_id)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="没有封面")
    return FileResponse(path)


class ProgressIn(BaseModel):
    chapter: int


class SkippedIn(BaseModel):
    skipped: bool


@app.put("/api/books/{book_id}/progress")
def set_progress(book_id: int, body: ProgressIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    try:
        found = books.set_progress(conn, book_id, body.chapter)
    except books.InvalidProgress as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not found:
        raise HTTPException(status_code=404, detail="书不存在")
    return books.get_book(conn, book_id)


@app.put("/api/books/{book_id}/chapters/{number}/skipped")
def set_skipped(book_id: int, number: int, body: SkippedIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    if not books.set_skipped(conn, book_id, number, body.skipped):
        raise HTTPException(status_code=404, detail="章节不存在")
    return books.get_book(conn, book_id)


def get_llm() -> LLMClient:
    """作为 FastAPI 依赖注入，测试用 dependency_overrides 换成假模型。
    没配 key 时返回 503，而不是等到真正调用时才失败。"""
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise HTTPException(status_code=503, detail="没有配置 DEEPSEEK_API_KEY")
    return deepseek_client(settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_model)


def require_book(book_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> int:
    # 先确认书存在再进入生成逻辑，书不存在时不会去创建模型客户端
    if books.get_current_chapter(conn, book_id) is None:
        raise HTTPException(status_code=404, detail="书不存在")
    return book_id


@app.get("/api/books/{book_id}/summaries")
def list_summaries(book_id: int = Depends(require_book), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return summaries.list_summaries(conn, book_id)


@app.post("/api/books/{book_id}/summaries/next")
def generate_next_summary(
    book_id: int = Depends(require_book),
    conn: sqlite3.Connection = Depends(get_conn),
    llm: LLMClient = Depends(get_llm),
) -> dict:
    try:
        return summaries.generate_next(conn, llm, book_id)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/api/books/{book_id}/chapters/{number}/summary/regenerate")
def regenerate_summary(
    number: int,
    book_id: int = Depends(require_book),
    conn: sqlite3.Connection = Depends(get_conn),
    llm: LLMClient = Depends(get_llm),
) -> dict:
    try:
        return summaries.regenerate(conn, llm, book_id, number)
    except summaries.NotReadable as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/api/books/{book_id}/recap")
def get_recap(
    book_id: int = Depends(require_book),
    conn: sqlite3.Connection = Depends(get_conn),
    llm: LLMClient = Depends(get_llm),
) -> dict:
    current_chapter = books.get_current_chapter(conn, book_id)
    try:
        return recap.get_recap(conn, llm, book_id, current_chapter)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/api/books/{book_id}/last-read")
def get_last_read(book_id: int = Depends(require_book), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return recap.last_read(conn, book_id)


@app.delete("/api/books/{book_id}", status_code=204)
def delete_book(book_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> None:
    if not books.delete_book(conn, book_id):
        raise HTTPException(status_code=404, detail="书不存在")


# 放在最后，避免拦截 /api 路由
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
