import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import books, db
from app.epub_parser import EpubError

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


@app.delete("/api/books/{book_id}", status_code=204)
def delete_book(book_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> None:
    if not books.delete_book(conn, book_id):
        raise HTTPException(status_code=404, detail="书不存在")


# 放在最后，避免拦截 /api 路由
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
