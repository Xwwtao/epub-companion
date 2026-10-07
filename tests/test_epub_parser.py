import re

import pytest

from app.epub_parser import EpubError, parse_epub
from tests import epub_builder
from tests.epub_builder import marker


def markers_in(text: str) -> set[str]:
    return set(re.findall(r"MARKER_\w+", text))


def assert_only_own_marker(chapters):
    for number, ch in enumerate(chapters, start=1):
        assert markers_in(ch.text) == {marker(number)}, f"第 {number} 章：{ch.title}"


def test_nested_toc_is_flattened(epubs):
    book = parse_epub(epubs["nested_zh"])

    assert book.title == "测试长篇"
    assert book.author == "测试作者"
    assert [(ch.parent_titles, ch.title) for ch in book.chapters] == [
        ([], "版权页"),
        ([], "序言"),
        (["第一卷 起"], "第一章 开端"),
        (["第一卷 起"], "第二章 相遇"),
        (["第一卷 起"], "第三章 离别"),
        (["第二卷 承"], "第四章 重逢"),
        (["第二卷 承"], "第五章 结局"),
    ]


def test_volume_pages_do_not_leak_into_chapters(epubs):
    book = parse_epub(epubs["nested_zh"])
    # 卷首页不属于任何章节；尤其第二卷的卷名不能出现在第一卷最后一章的末尾
    assert_only_own_marker(book.chapters)
    assert "第二卷" not in book.chapters[4].text


def test_chapter_text_is_normalized(epubs):
    book = parse_epub(epubs["nested_zh"])
    assert book.chapters[2].text == f"第一章 开端\n故事开始了。{marker(3)}"


def test_cover(epubs):
    book = parse_epub(epubs["nested_zh"])
    assert book.cover is not None
    content, media_type = book.cover
    assert media_type == "image/png"
    assert content == epub_builder.PNG


def test_anchors_split_and_span_files(epubs):
    book = parse_epub(epubs["anchors"])

    assert [ch.title for ch in book.chapters] == ["第一章", "第二章", "第三章", "第四章"]
    assert_only_own_marker(book.chapters)
    # 行内标签不打断段落
    assert "第二章内容" in book.chapters[1].text
    # 第三章跨两个文件
    assert "C3B_TAIL" in book.chapters[2].text
    assert "C3B_TAIL" not in book.chapters[3].text


def test_missing_anchor_falls_back_to_file_start(epubs, caplog):
    book = parse_epub(epubs["anchors"])
    assert book.chapters[3].text.startswith("第四章")
    assert "找不到锚点" in caplog.text


def test_no_toc_falls_back_to_spine(epubs):
    book = parse_epub(epubs["no_toc"])

    assert [ch.title for ch in book.chapters] == ["开头", "第 2 部分", "结尾"]
    assert_only_own_marker(book.chapters)


def test_ncx_only_with_subdirectory(epubs):
    book = parse_epub(epubs["english"])

    assert book.title == "Test Novel"
    assert [ch.title for ch in book.chapters] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert [ch.start_href for ch in book.chapters] == [f"text/ch{n}.xhtml" for n in (1, 2, 3)]
    assert_only_own_marker(book.chapters)
    assert book.chapters[0].text == f"Chapter 1\nElizabeth walked on. {marker(1)}"


def test_not_a_zip(tmp_path):
    path = tmp_path / "fake.epub"
    path.write_text("这不是 EPUB")
    with pytest.raises(EpubError, match="不是有效的 EPUB"):
        parse_epub(path)


def test_drm_is_rejected(epubs, tmp_path):
    path = epub_builder.with_encryption(epubs["nested_zh"], tmp_path / "drm.epub", "EPUB/c1.xhtml")
    with pytest.raises(EpubError, match="DRM"):
        parse_epub(path)


def test_font_obfuscation_is_allowed(epubs, tmp_path):
    path = epub_builder.with_encryption(epubs["nested_zh"], tmp_path / "font.epub", "EPUB/fonts/a.ttf")
    assert len(parse_epub(path).chapters) == 7
