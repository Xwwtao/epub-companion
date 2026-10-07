# EPUB 读书伴侣 — 实施计划（v1）

依据：[spec.md](spec.md)。共四个阶段，每个阶段结束时：

- `pytest` 全部通过；
- `uvicorn` 能启动，浏览器里能实际操作本阶段的功能；
- 按本阶段"验证"一节逐条核对后，再进入下一阶段。

## 0. 约定

### 0.1 目录结构（逐阶段补齐）

```
app/
  main.py          # FastAPI 应用和路由
  config.py        # 从环境变量读配置（§5.2）
  db.py            # SQLite 连接和建表
  epub_parser.py   # EPUB → 元数据 + 展平后的章节列表        [阶段1]
  books.py         # 书库增删查                              [阶段1]
  readable.py      # readable_chapters 边界                  [阶段2]
  llm.py           # LLM 调用封装：系统提示、请求日志         [阶段3]
  prompts.py       # 所有 prompt 模板，含 §2.1 兜底条款       [阶段3]
  summaries.py     # 逐章摘要：上下文组装、切块、缓存          [阶段3]
  recap.py         # 整体回顾、上次读到哪                     [阶段4]
  static/          # index.html（书架）、book.html（书详情）、app.js
tests/
  conftest.py      # 用 ebooklib 现场生成测试 EPUB、临时 DATA_DIR
  ...
.env.example
requirements.txt
```

### 0.2 技术约定

- Python 3.11+，`venv` + `requirements.txt`；依赖：fastapi、uvicorn、ebooklib、beautifulsoup4、lxml、openai（调用 DeepSeek 的 OpenAI 兼容接口）、python-dotenv、python-multipart、pytest、httpx。
- 运行：`uvicorn app.main:app --reload`；测试：`pytest`。
- SQLite 直接用标准库 `sqlite3`，不引入 ORM。建表脚本写在 `db.py`，启动时执行 `CREATE TABLE IF NOT EXISTS`。
- v1 所有数据固定 `user_id = 1`。

### 0.3 测试用 EPUB

自动化测试（`pytest`）用 `conftest.py` 里 ebooklib 现场生成的小书，**每章正文里放一个唯一标记**（如 `MARKER_CH07`），后续所有防剧透测试都靠这些标记判断有没有越界：

| 测试书 | 用途 |
|---|---|
| `nested_zh` | 中文，"卷 > 章"两层目录，含序言和版权页 |
| `anchors` | 同一个 XHTML 文件里用 `#anchor` 分成两章；另有一章跨两个 spine 文件 |
| `no_toc` | 没有目录，测试回退为按 spine 分章 |
| `english` | 英文书，用于验证中文输出和人名保留原文 |

手动验证用真实的书（可以是有版权的书），放在仓库根目录的 `samples/` 下。该目录已加入 `.gitignore`，**书本身不得提交**。优先选模型很熟悉、反转多的名著，最能检验防剧透效果。

---

## 阶段 1：导入和章节解析

**目标**：能上传 EPUB，在书架上看到它，打开后看到正确展平的章节列表。

### 任务

1. 项目骨架：`config.py`、`db.py`、`main.py`、`.env.example`、`requirements.txt`、`.gitignore`（忽略 `.env`、`DATA_DIR`、`.venv`）。
2. 建 `books`、`chapters` 表（字段见 spec §6）。
3. `epub_parser.py`：
   - 读取书名、作者、封面；
   - 解析 EPUB3 nav，没有则用 EPUB2 NCX，展平到叶子节点，记下父级标题；
   - 按"从本叶子节点的 `href#anchor` 到下一个叶子节点的位置"切出章节纯文本，处理跨文件和同一文件里多章的情况；
   - 目录为空时回退为每个 spine 文件一章；
   - DRM 加密或文件损坏时抛出明确的异常。
4. `books.py` 和接口：
   - `POST /api/books`：上传并导入，EPUB 原文件存到 `DATA_DIR/books/`；
   - `GET /api/books`：书架列表；
   - `GET /api/books/{id}`：书的信息和章节列表（不返回章节正文）；
   - `DELETE /api/books/{id}`：删书，连同文件和章节。
5. 前端：`index.html` 有上传和书架列表；`book.html` 显示章节列表（带父级标题）。

### 测试

- `test_epub_parser.py`：四本测试书的章节数、标题、父级标题都正确；每章正文**只含**本章的标记、不含相邻章的标记（验证切分边界）；`no_toc` 正确回退；损坏文件报错。
- `test_books_api.py`：上传、列表、详情、删除的完整流程；删除后文件也被删掉。

### 验证

- [ ] `pytest` 通过。
- [ ] 启动后在浏览器上传 `samples/` 里的两本书（中、英各一），书架显示书名、作者、章节总数。
- [ ] 打开书详情，章节列表和阅读器里的目录对得上。
- [ ] 上传一个随便改了后缀的文件，页面显示明确的错误。

---

## 阶段 2：进度设置和 readable_chapters 边界

**目标**：能设置"正在读第 N 章"、能勾选跳过的章节；建立唯一的"可发给 LLM 的章节"入口，并用测试守住。

### 任务

1. 接口：
   - `PUT /api/books/{id}/progress`：设置当前章节 N（校验 1 ≤ N ≤ 总章数）；
   - `PUT /api/books/{id}/chapters/{index}/skipped`：设置或取消跳过。
2. `readable.py`：
   - `readable_chapters(book_id)` 返回第 1 ~ N-1 章中未跳过的章节（含正文），按顺序排列；
   - 这是全项目**唯一**读取 `chapters.text` 的函数（导入时写入除外）。
3. 前端：章节列表里每章可点"正在读这一章"；当前章及之后的章节用灰色标出"未读"；每章有"跳过"复选框。

### 测试

`test_readable.py`，用测试书的标记验证：

- N=1 时返回空列表。
- N=5 时只返回第 1~4 章，返回结果里搜不到 `MARKER_CH05` 及之后的任何标记。
- N 设为最后一章时，最后一章不在结果里。
- 跳过的章节不在结果里，但不影响其他章节的序号。
- 进度从 8 改回 3 后，只返回第 1~2 章。
- **边界守护测试**：扫描 `app/` 下所有 `.py` 文件，除 `readable.py` 和导入写入的代码外，不得出现读取 `chapters` 表 `text` 字段的 SQL。这是一个简单的文本检查，用来防止以后有人绕过边界。

### 验证

- [ ] `pytest` 通过。
- [ ] 浏览器里设置进度、勾选跳过，刷新后状态保持。
- [ ] 进度显示为"正在读第 N 章 / 共 M 章"。

---

## 阶段 3：逐章摘要

**目标**：提要页能按需生成并展示第 1 ~ N-1 章的逐章摘要，带缓存、可单章重新生成；所有 LLM 请求都满足防剧透要求，并在开发模式下留有日志。

### 任务

1. 建 `chapter_summaries` 表（含 `names` JSON 和 `version`）。
2. `prompts.py`：
   - §2.1 名著兜底条款定义为一个常量，所有系统提示都由它拼出；
   - 逐章摘要、切块摘要、切块合并三种 prompt；
   - 要求模型输出 JSON：`summary`、`ending_state`、`names`；提要一律用中文，英文人名首次出现时附原文。
3. `llm.py`：
   - 封装一个 `complete(kind, messages, meta)`，内部用 openai SDK 调用 DeepSeek，并启用 JSON 输出模式；
   - `APP_ENV=development` 时把每次请求写入 `DATA_DIR/logs/llm_requests.jsonl`（字段见 spec §5.3），不写 key 和请求头；
   - 支持注入假的 LLM 客户端，供测试使用。
4. `summaries.py`：
   - 生成第 k 章时，从 `readable_chapters` 取第 k 章正文、最近 K 章摘要，以及第 1 ~ k-1 章 `names` 合并去重后的人物名单；
   - 请求里不出现书名、作者等元数据；
   - 按字符数估算长度，超过阈值就切块：每块各自摘要（每块都带同样的上下文），再合并成一章；
   - 结果入库；重新生成时 `version` + 1。
5. 接口：
   - `GET /api/books/{id}/summaries`：返回第 1 ~ N-1 章已有的摘要，以及还缺哪些章；
   - `POST /api/books/{id}/summaries/next`：生成**下一个**缺失的章节摘要并返回；
   - `POST /api/books/{id}/chapters/{index}/summary/regenerate`：重新生成单章摘要。
6. 前端：提要页调用 `summaries/next` 一次生成一章，循环到补齐为止，显示"生成中 3 / 10"；每章摘要可展开，并有"重新生成"按钮。

   > 选择"前端循环、每次生成一章"，而不是后台任务队列：实现简单，自然得到进度显示，中途关掉页面也不会丢掉已生成的章节。

### 测试

`test_summaries.py`，用一个会记录所有请求内容的假 LLM：

- 进度为 N 时，所有记录下来的请求里搜不到 `MARKER_CH{N}` 及之后的标记，也搜不到书名和作者。（对应验收标准 2）
- 每个请求的系统提示都包含兜底条款常量。（验收 3）
- 生成第 k 章时，请求里只带第 k-K ~ k-1 章的摘要；人物名单只来自第 1 ~ k-1 章。（验收 4）
- 设置很小的切块阈值，长章节会被切成多块请求，每块都带上下文，最后得到一条合并结果。
- 已有缓存时不再调用 LLM；重新生成后 `version` 递增，后续章节的缓存不变。（验收 6）
- `test_llm_log.py`：`development` 下产生日志，`production` 下不产生；日志里搜不到 key。（验收 5）

### 验证

- [ ] `pytest` 通过。
- [ ] 在 `.env` 配好真实的 DeepSeek key，设 `APP_ENV=development`，对 `samples/` 里的英文书设"正在读第 6 章"，生成摘要。
- [ ] 打开 `llm_requests.jsonl` 人工抽查：没有第 6 章及之后的原文，没有书名和作者，系统提示里有兜底条款。
- [ ] 英文书的摘要是中文，人名带原文；前后章节对同一人物的称谓一致。
- [ ] 刷新提要页，摘要秒出，日志里没有新请求。
- [ ] 点某章"重新生成"，内容更新。

---

## 阶段 4：整体回顾和上次读到哪

**目标**：提要页完整：整体剧情回顾 + 上次读到哪 + 逐章摘要。

### 任务

1. 建 `recaps` 表。
2. `recap.py`：
   - 整体回顾：输入是第 1 ~ N-1 章（跳过的不算）的逐章摘要，**不读原文**；系统提示同样带兜底条款；
   - 缓存键 = 书 + N + 参与各章摘要的 `(chapter_id, version)` 列表的哈希；命中就直接返回；
   - 逐章摘要还没补齐时，不生成整体回顾，界面提示先补齐；
   - 上次读到哪：直接取第 N-1、N-2 章的摘要缓存，突出第 N-1 章的 `ending_state`，不调用 LLM。
3. 接口：`GET /api/books/{id}/recap`（命中缓存直接返回，否则生成）、`GET /api/books/{id}/last-read`。
4. 前端：提要页从上到下依次是：上次读到哪 → 整体回顾 → 逐章摘要；逐章摘要补齐后自动加载整体回顾。

### 测试

`test_recap.py`：

- 整体回顾请求里只有摘要，搜不到任何章节标记（证明没读原文），也搜不到第 N 章及之后的摘要。
- 第二次请求命中缓存，不调用 LLM。
- 重新生成参与的某章摘要后缓存失效，再次请求会重新生成。（验收 7）
- 进度改变后缓存失效；进度回退后，回顾和上次读到哪都不出现超出新进度的内容。（验收 8）
- 上次读到哪不调用 LLM；N=2 时只显示第 1 章；N=1 时提示"还没有可回顾的内容"。

### 验证

- [ ] `pytest` 通过。
- [ ] 用 `samples/` 里的书完整走一遍：导入 → 设进度 → 生成 → 查看三部分内容。
- [ ] 把进度往前调，提要页内容随之收缩，没有超出新进度的内容。
- [ ] 对照 spec §8 的 10 条验收标准全部打勾。
- [ ] `git grep` 查不到 API key。（验收 10）

---

## 风险和应对

| 风险 | 应对 |
|---|---|
| 各家 EPUB 的目录格式不规范（锚点缺失、目录指向的文件不在 spine 里） | 阶段 1 先用 `samples/` 里的书多试几本；解析不了的锚点就退化为整个文件算一章，并记一条警告日志 |
| 模型不严格返回 JSON | 开启 DeepSeek 的 JSON 输出模式；解析失败重试一次，仍失败就报错，不写缓存 |
| 书很长时，全部摘要加起来超出整体回顾的上下文 | v1 先按字符数检查，超出时分组先汇总再合并；阈值写在配置里 |
| 名著兜底条款挡不住所有的剧透 | v1 依靠 spec §2 的措施；开发日志方便人工抽查；自动审查留到以后的版本 |
