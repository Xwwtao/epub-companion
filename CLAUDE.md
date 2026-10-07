# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

EPUB 读书伴侣：导入 EPUB、设置阅读进度，基于已读章节生成前情提要，**绝不剧透未读内容**。需求见 `docs/spec.md`，分阶段实施计划见 `docs/plan.md`（每个阶段做完都要能运行和验证，按阶段推进）。文档和界面文字用中文。

## 常用命令

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                 # 配置全部走环境变量，见 app/config.py

.venv/bin/python -m app              # 启动，读取 HOST/PORT；APP_ENV=development 时自动 reload
.venv/bin/pytest                     # 全部测试
.venv/bin/pytest tests/test_epub_parser.py::test_anchors_split_and_span_files   # 单个测试
```

没有构建步骤：前端是 `app/static/` 下的静态 HTML + 原生 JS，由 FastAPI 在 `/` 挂载提供。

## 防剧透约束（改代码前必读）

- 进度语义是"**正在读第 N 章**"：只有第 1 ~ N-1 章（且未被标记跳过）算已读。
- 发给 LLM 的章节正文只能经由 `readable_chapters(conn, book_id)`（`app/readable.py`）取得，`tests/test_readable.py` 里有守护测试扫描代码。除了导入时写库，其他代码不得直接 `SELECT` `chapters.text`；`app/books.py` 里查询章节时要显式列出字段，不带 `text`。
- 不向 LLM 传书名、作者等元数据（版权页这类正文含书名和作者的页面，导入时由 `books.mentions_book_info` 标记，短页面默认跳过）；所有生成请求的系统提示共用 spec §2.1 的名著兜底条款（集中定义在一处）。
- 开发模式下 LLM 请求日志不得包含 API key 或请求头。

## 架构

- `app/epub_parser.py` 是最复杂的部分。它先把整本书按 spine 顺序拼成一串文本片段（`_TextStream`），同时记下每个文件起点和每个 `id`/`name` 锚点在这串片段里的下标；再把目录展平，章节正文 = 从本叶子节点的位置到目录中下一个位置为止。带链接的父节点（如卷首页）也算边界但不成章，这样卷名不会混进上一章末尾。目录对不上正文时回退为每个 spine 文件一章。
- ebooklib 的坑：EPUB3 nav 里的 href 已被换算成相对 OPF 的路径，EPUB2 NCX 里的没有（见 `_resolve_file`）；读书时传 `ignore_ncx=True`，有 nav 就优先用 nav。
- 数据：SQLite（标准库 `sqlite3`，不用 ORM），建表语句在 `app/db.py`，启动时 `CREATE TABLE IF NOT EXISTS`；EPUB 原文件和封面存在 `DATA_DIR` 下，库里存相对路径。每个请求一个连接（`main.get_conn`）。
- LLM：所有请求都经由 `llm.complete_json`，它负责 JSON 解析、内容不合格时重试一次，以及开发模式日志（`DATA_DIR/logs/llm_requests.jsonl`）。prompt 全部在 `app/prompts.py`，系统提示一律用 `system_prompt()` 拼，自动带上 `SPOILER_GUARD`。模型客户端通过 FastAPI 依赖 `main.get_llm` 注入。
- 逐章摘要（`app/summaries.py`）由前端循环调用 `POST .../summaries/next` 一次生成一章，因为后一章要用前面章节的摘要和人物名单（`merge_names`）作上下文。人物名单只在内部使用，接口不返回 `names`。
- 为以后的登录预留了 `user_id`，v1 固定为 `DEFAULT_USER_ID = 1`，查询时都带上这个条件。

## 测试

- 自动化测试用的 EPUB 由 `tests/epub_builder.py` 现场生成，不依赖外部文件。每章正文里有唯一标记 `MARKER_CHnn`（nn = 展平后的章节序号），防剧透测试靠搜索这些标记判断有没有越界；新增测试书时沿用这个约定。
- 涉及 LLM 的测试用 `conftest.py` 的 `fake_llm` fixture（覆盖 `get_llm`，记录所有请求）。它按原文里的 `MARKER_CHnn` 返回 `SUM_CHnn` / `NAME_CHnn`，方便断言上下文里带了哪些章节。`data_dir` fixture 会清空 `DEEPSEEK_API_KEY`，测试绝不会调用真实模型。
- `conftest.py` 的 `data_dir` fixture 把 `DATA_DIR` 指到临时目录；`get_settings()` 每次都重新读环境变量，所以 monkeypatch 即可生效。
- 手动验证用的真实书放在 `samples/`（已 gitignore，书本身不得提交）。
