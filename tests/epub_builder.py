"""现场生成测试用 EPUB。每章正文里放一个唯一标记 MARKER_CHnn（nn 为展平后的章节序号），
防剧透测试靠搜索这些标记判断有没有越界。"""

import base64
import shutil
import zipfile
from pathlib import Path

from ebooklib import epub

# 1x1 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def marker(n: int) -> str:
    return f"MARKER_CH{n:02d}"


def _page(file_name: str, body: str, lang: str = "zh") -> epub.EpubHtml:
    uid = file_name.replace("/", "_").replace(".", "_")
    item = epub.EpubHtml(uid=uid, file_name=file_name, lang=lang)
    item.content = f"<html><head><title>{file_name}</title></head><body>{body}</body></html>"
    return item


def _new_book(title: str, author: str, lang: str) -> epub.EpubBook:
    book = epub.EpubBook()
    book.set_identifier(f"test-{title}")
    book.set_title(title)
    book.set_language(lang)
    book.add_author(author)
    return book


def _write(book, pages, toc, path: Path, nav: bool = True, ncx: bool = True) -> Path:
    for page in pages:
        book.add_item(page)
    book.toc = toc
    if ncx:
        book.add_item(epub.EpubNcx())
    if nav:
        book.add_item(epub.EpubNav())
    book.spine = pages
    epub.write_epub(str(path), book)
    return path


def nested_zh(path: Path) -> Path:
    """中文，卷 > 章两层目录，含版权页、序言和卷首页，带封面。

    展平后：1 版权页、2 序言、3~5 第一卷的三章、6~7 第二卷的两章。
    卷首页里放 MARKER_VOLn，它们不应出现在任何章节里。
    """
    book = _new_book("测试长篇", "测试作者", "zh")
    book.set_cover("cover.png", PNG, create_page=False)
    copyright_ = _page("copyright.xhtml", f"<p>版权所有 {marker(1)}</p>")
    preface = _page("preface.xhtml", f"<h1>序言</h1><p>写在前面 {marker(2)}</p>")
    vol1 = _page("vol1.xhtml", "<h1>第一卷 起</h1><p>MARKER_VOL1</p>")
    c1 = _page("c1.xhtml", f"<h2>第一章 开端</h2><p>　　故事开始了。{marker(3)}</p>")
    c2 = _page("c2.xhtml", f"<h2>第二章 相遇</h2><p>他们相遇了。{marker(4)}</p>")
    c3 = _page("c3.xhtml", f"<h2>第三章 离别</h2><p>他们分开了。{marker(5)}</p>")
    vol2 = _page("vol2.xhtml", "<h1>第二卷 承</h1><p>MARKER_VOL2</p>")
    c4 = _page("c4.xhtml", f"<h2>第四章 重逢</h2><p>又见面了。{marker(6)}</p>")
    c5 = _page("c5.xhtml", f"<h2>第五章 结局</h2><p>故事结束了。{marker(7)}</p>")
    toc = [
        epub.Link("copyright.xhtml", "版权页", "copyright"),
        epub.Link("preface.xhtml", "序言", "preface"),
        (
            epub.Section("第一卷 起", href="vol1.xhtml"),
            [
                epub.Link("c1.xhtml", "第一章 开端", "c1"),
                epub.Link("c2.xhtml", "第二章 相遇", "c2"),
                epub.Link("c3.xhtml", "第三章 离别", "c3"),
            ],
        ),
        (
            epub.Section("第二卷 承", href="vol2.xhtml"),
            [
                epub.Link("c4.xhtml", "第四章 重逢", "c4"),
                epub.Link("c5.xhtml", "第五章 结局", "c5"),
            ],
        ),
    ]
    return _write(book, [copyright_, preface, vol1, c1, c2, c3, vol2, c4, c5], toc, path)


def anchors(path: Path) -> Path:
    """同一个文件里用锚点分成两章；第三章跨两个文件；第四章的锚点不存在。"""
    book = _new_book("锚点测试", "测试作者", "zh")
    part1 = _page(
        "part1.xhtml",
        f'<h2 id="a1">第一章</h2><p>第一章内容 {marker(1)}</p>'
        f'<h2 id="a2">第二章</h2><p>第二章<span>内容</span> {marker(2)}</p>',
    )
    c3a = _page("c3a.xhtml", f"<h2>第三章</h2><p>上半 {marker(3)}</p>")
    c3b = _page("c3b.xhtml", "<p>下半 C3B_TAIL</p>")
    c4 = _page("c4.xhtml", f"<h2>第四章</h2><p>{marker(4)}</p>")
    toc = [
        epub.Link("part1.xhtml#a1", "第一章", "a1"),
        epub.Link("part1.xhtml#a2", "第二章", "a2"),
        epub.Link("c3a.xhtml", "第三章", "c3"),
        epub.Link("c4.xhtml#missing", "第四章", "c4"),
    ]
    return _write(book, [part1, c3a, c3b, c4], toc, path)


def no_toc(path: Path) -> Path:
    """没有目录，应回退为每个 spine 文件一章。"""
    book = _new_book("无目录", "测试作者", "zh")
    pages = [
        _page("s1.xhtml", f"<h1>开头</h1><p>{marker(1)}</p>"),
        _page("s2.xhtml", f"<p>没有标题的一页 {marker(2)}</p>"),
        _page("s3.xhtml", f"<h1>结尾</h1><p>{marker(3)}</p>"),
    ]
    _write(book, pages, [], path, nav=False, ncx=False)
    # ebooklib 即使没有 NCX 也会写 spine toc="ncx"，读回时会因找不到 NCX 报错；去掉它
    return _rewrite_member(path, "EPUB/content.opf", lambda s: s.replace(' toc="ncx"', ""))


def _rewrite_member(path: Path, member: str, fix) -> Path:
    with zipfile.ZipFile(path) as zf:
        entries = [(info, zf.read(info)) for info in zf.infolist()]
    with zipfile.ZipFile(path, "w") as zf:
        for info, data in entries:
            if info.filename == member:
                data = fix(data.decode()).encode()
            zf.writestr(info, data)
    return path


def english(path: Path) -> Path:
    """英文，只有 NCX 目录（EPUB2 风格），正文放在子目录里。"""
    book = _new_book("Test Novel", "Test Author", "en")
    pages = [
        _page(f"text/ch{n}.xhtml", f"<h1>Chapter {n}</h1><p>Elizabeth walked on. {marker(n)}</p>", lang="en")
        for n in range(1, 4)
    ]
    toc = [epub.Link(f"text/ch{n}.xhtml", f"Chapter {n}", f"ch{n}") for n in range(1, 4)]
    return _write(book, pages, toc, path, nav=False, ncx=True)


def with_encryption(src: Path, dst: Path, encrypted_uri: str) -> Path:
    """复制一本书并加上 META-INF/encryption.xml。"""
    shutil.copy(src, dst)
    xml = (
        '<?xml version="1.0"?>'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
        ' xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
        "<enc:EncryptedData><enc:CipherData>"
        f'<enc:CipherReference URI="{encrypted_uri}"/>'
        "</enc:CipherData></enc:EncryptedData></encryption>"
    )
    with zipfile.ZipFile(dst, "a") as zf:
        zf.writestr("META-INF/encryption.xml", xml)
    return dst
