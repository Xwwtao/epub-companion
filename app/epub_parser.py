"""EPUB → 元数据 + 展平后的章节列表。

章节以目录（EPUB3 nav / EPUB2 NCX）为准，嵌套目录展平到叶子节点。
章节正文 = 从本叶子节点指向的位置，到目录中下一个位置（叶子或带链接的父节点）为止。
父节点自己指向的内容（如卷首页）不归入任何章节，避免卷名泄露到上一章末尾。
"""

import logging
import posixpath
import re
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import ebooklib
from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning
from bs4.element import CData, Comment, Declaration, Doctype, ProcessingInstruction
from ebooklib import epub
from lxml import etree

logger = logging.getLogger(__name__)

# 这些标签前后断行，其余（span、em 等行内标签）不打断段落
# fmt: off
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "ol", "p", "pre", "section", "table", "tr", "ul",
}
# fmt: on
SKIP_TAGS = {"script", "style", "head", "title"}
NON_TEXT_STRINGS = (Comment, CData, ProcessingInstruction, Declaration, Doctype)
FONT_EXTENSIONS = (".ttf", ".otf", ".woff", ".woff2")
XMLENC_NS = "http://www.w3.org/2001/04/xmlenc#"


class EpubError(Exception):
    """EPUB 无法导入；消息直接展示给用户。"""


@dataclass
class ParsedChapter:
    title: str
    parent_titles: list[str]
    start_href: str
    text: str


@dataclass
class ParsedBook:
    title: str
    author: str | None
    cover: tuple[bytes, str] | None  # (内容, media type)
    chapters: list[ParsedChapter] = field(default_factory=list)


@dataclass
class _TocEntry:
    title: str
    href: str
    parent_titles: list[str]
    is_leaf: bool


class _TextStream:
    """把整本书按 spine 顺序拼成一串文本片段，并记下每个锚点所在的片段下标。"""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.positions: dict[tuple[str, str | None], int] = {}

    def add_file(self, file_name: str, html: bytes) -> None:
        self.parts.append("\n")
        self.positions[(file_name, None)] = len(self.parts)
        soup = _soup(html)
        root = soup.body or soup
        for node in root.descendants:
            if isinstance(node, Tag):
                if node.name in BLOCK_TAGS:
                    self.parts.append("\n")
                for attr in ("id", "name"):
                    anchor = node.get(attr)
                    if anchor:
                        self.positions.setdefault((file_name, anchor), len(self.parts))
            elif isinstance(node, NavigableString) and not isinstance(node, NON_TEXT_STRINGS):
                if node.parent is not None and node.parent.name in SKIP_TAGS:
                    continue
                self.parts.append(re.sub(r"\s+", " ", str(node)))

    def text(self, start: int, end: int) -> str:
        return _normalize("".join(self.parts[start:end]))


def parse_epub(path: Path) -> ParsedBook:
    _check_drm(path)
    try:
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
    except Exception as e:
        raise EpubError(f"EPUB 解析失败：{e}") from e

    stream = _TextStream()
    for item in _spine_html_items(book):
        stream.add_file(item.file_name, item.content)

    toc_entries = list(_flatten_toc(book.toc, []))
    chapters = _chapters_from_toc(book, toc_entries, stream) if toc_entries else []
    if not chapters:
        if toc_entries:
            logger.warning("目录无法对应到正文，回退为按 spine 分章：%s", path.name)
        chapters = _chapters_from_spine(book, stream)
    if not chapters:
        raise EpubError("没有在 EPUB 里找到任何章节内容")

    return ParsedBook(
        title=_first_metadata(book, "title") or path.stem,
        author=_first_metadata(book, "creator"),
        cover=_find_cover(book),
        chapters=chapters,
    )


def _check_drm(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            if "META-INF/encryption.xml" not in zf.namelist():
                return
            root = etree.fromstring(zf.read("META-INF/encryption.xml"))
    except zipfile.BadZipFile as e:
        raise EpubError("不是有效的 EPUB 文件") from e
    except etree.XMLSyntaxError:
        return
    # 只加密字体属于常见的字体混淆，不影响正文
    uris = [el.get("URI", "") for el in root.iter(f"{{{XMLENC_NS}}}CipherReference")]
    if any(not uri.lower().endswith(FONT_EXTENSIONS) for uri in uris):
        raise EpubError("这本 EPUB 有 DRM 加密，无法导入")


def _spine_html_items(book: epub.EpubBook):
    for idref, _linear in book.spine:
        item = book.get_item_with_id(idref)
        # 目录页本身不算正文
        if item is None or isinstance(item, epub.EpubNav):
            continue
        if item.media_type == "application/xhtml+xml" and item.content:
            yield item


def _flatten_toc(nodes, parents: list[str]):
    for node in nodes:
        if isinstance(node, tuple):
            section, children = node
            title = _clean_title(section.title)
            if children:
                if section.href:
                    yield _TocEntry(title, section.href, parents, is_leaf=False)
                yield from _flatten_toc(children, parents + [title])
            elif section.href:
                yield _TocEntry(title, section.href, parents, is_leaf=True)
        elif isinstance(node, (epub.Link, epub.Section)) and node.href:
            yield _TocEntry(_clean_title(node.title), node.href, parents, is_leaf=True)
        elif isinstance(node, list):
            yield from _flatten_toc(node, parents)


def _chapters_from_toc(book, entries: list[_TocEntry], stream: _TextStream) -> list[ParsedChapter]:
    located = _locate_entries(book, entries, stream)
    # 所有目录项（含带链接的父节点）都是边界；章节到下一个边界为止
    boundaries = sorted({position for _, position in located})
    chapters = []
    for entry, start in located:
        if not entry.is_leaf:
            continue
        end = next((b for b in boundaries if b > start), len(stream.parts))
        chapters.append(
            ParsedChapter(
                title=entry.title or f"第 {len(chapters) + 1} 章",
                parent_titles=entry.parent_titles,
                start_href=entry.href,
                text=stream.text(start, end),
            )
        )
    return chapters


def _locate_entries(book, entries: list[_TocEntry], stream: _TextStream) -> list[tuple[_TocEntry, int]]:
    """把目录项的 href#anchor 换算成文本流里的下标；找不到文件的目录项丢弃。"""
    file_names = {file for file, anchor in stream.positions if anchor is None}
    ncx_dir = _ncx_dir(book)
    located: list[tuple[_TocEntry, int]] = []
    for entry in entries:
        file_part, _, fragment = entry.href.partition("#")
        file_name = _resolve_file(unquote(file_part), file_names, ncx_dir)
        if file_name is None:
            logger.warning("目录项指向的文件不在正文里，已忽略：%s (%s)", entry.title, entry.href)
            continue
        position = stream.positions.get((file_name, unquote(fragment))) if fragment else None
        if position is None:
            if fragment:
                logger.warning("找不到锚点，退化为整个文件起始处：%s (%s)", entry.title, entry.href)
            position = stream.positions[(file_name, None)]
        located.append((entry, position))
    return located


def _chapters_from_spine(book, stream: _TextStream) -> list[ParsedChapter]:
    starts = [(file, pos) for (file, anchor), pos in stream.positions.items() if anchor is None]
    starts.sort(key=lambda fp: fp[1])
    chapters = []
    for i, (file_name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(stream.parts)
        text = stream.text(start, end)
        if not text:
            continue
        title = _html_heading(book.get_item_with_href(file_name)) or f"第 {len(chapters) + 1} 部分"
        chapters.append(ParsedChapter(title=title, parent_titles=[], start_href=file_name, text=text))
    return chapters


def _resolve_file(path: str, file_names: set[str], ncx_dir: str) -> str | None:
    if path in file_names:
        return path
    # NCX 里的路径相对 NCX 文件，ebooklib 没有换算
    joined = posixpath.normpath(posixpath.join(ncx_dir, path))
    if joined in file_names:
        return joined
    same_basename = [f for f in file_names if posixpath.basename(f) == posixpath.basename(path)]
    return same_basename[0] if len(same_basename) == 1 else None


def _ncx_dir(book) -> str:
    for item in book.get_items_of_type(ebooklib.ITEM_NAVIGATION):
        if item.file_name.endswith(".ncx"):
            return posixpath.dirname(item.file_name)
    return ""


def _html_heading(item) -> str | None:
    if item is None:
        return None
    soup = _soup(item.content)
    for tag in soup.find_all(["h1", "h2", "h3", "title"]):
        text = _clean_title(tag.get_text())
        if text:
            return text
    return None


def _find_cover(book) -> tuple[bytes, str] | None:
    candidates = list(book.get_items_of_type(ebooklib.ITEM_COVER))
    for _value, attrs in book.get_metadata("OPF", "cover"):
        item = book.get_item_with_id(attrs.get("content", ""))
        if item is not None:
            candidates.append(item)
    for item in candidates:
        if item.media_type and item.media_type.startswith("image/") and item.content:
            return item.content, item.media_type
    return None


def _first_metadata(book, name: str) -> str | None:
    values = book.get_metadata("DC", name)
    if values and values[0][0]:
        return values[0][0].strip() or None
    return None


def _soup(html: bytes) -> BeautifulSoup:
    # XHTML 用 HTML 解析器处理是有意为之（容错更好），不需要 bs4 的提示
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        return BeautifulSoup(html, "lxml")


def _clean_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", title or "").strip()


def _normalize(text: str) -> str:
    lines = (re.sub(r"[ \t 　]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)
