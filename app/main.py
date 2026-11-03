import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import books, db, recap, summaries
from app.config import get_settings
from app.epub_parser import EpubError
from app.llm import DeepSeekClient, LLMClient, LLMError

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="EPUB 读书伴侣", lifespan=lifespan)


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


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
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise HTTPException(status_code=503, detail="没有配置 DEEPSEEK_API_KEY")
    return DeepSeekClient(settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_model)


def require_book(book_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> int:
    if books.get_book(conn, book_id) is None:
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
    current_chapter = books.get_book(conn, book_id)["current_chapter"]
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
