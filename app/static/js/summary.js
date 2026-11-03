// 前情提要：上次读到哪 → 整体回顾 → 逐章摘要。
// 缺失的逐章摘要由这里循环调用 summaries/next 一次生成一章（后一章要用前面的摘要作上下文），
// 补齐后再请求整体回顾。

const bookId = bookIdFromUrl();
const $ = (id) => document.getElementById(id);
const hero = $("hero");

const state = {
  book: null,
  summaries: null, // { chapters, missing }
  llmReady: true,
  generating: false, // 正在循环补齐缺失的摘要
  loop: null, // 循环的 Promise，暂停时等它真正停下
  stopRequested: false,
  regenerating: null, // 正在重新生成的章节序号
  open: new Set(), // 展开的章节；重新渲染时保持
  epoch: 0, // 每次改进度 +1；旧进度下发出的请求回来时直接丢弃，避免显示过期内容
};

const busy = () => state.generating || state.regenerating !== null;

// ---------- 加载 ----------

async function init() {
  if (bookId === null) return showFatal("链接里缺少书的编号。");
  try {
    const [book, health] = await Promise.all([
      api(`/api/books/${bookId}`),
      api("/api/health").catch(() => ({ llm_configured: true })), // 健康检查失败不影响看已有的摘要
    ]);
    state.book = book;
    state.llmReady = health.llm_configured;
  } catch (err) {
    return showFatal(err.status === 404 ? "这本书不存在，可能已经被删除了。" : err.message);
  }
  document.title = `前情提要 · ${state.book.title}`;
  $("crumb-title").textContent = state.book.title;
  renderHero();
  renderNotice();
  await refreshAndGenerate();
}

function showFatal(message) {
  document.querySelector("main").replaceChildren(el("div", { class: "empty" },
    el("h2", {}, "打不开前情提要"), el("p", {}, message), el("a", { class: "btn", href: "./" }, "回到书架")));
}

async function refresh() {
  const epoch = state.epoch;
  const [summaries, last] = await Promise.all([
    api(`/api/books/${bookId}/summaries`),
    api(`/api/books/${bookId}/last-read`),
  ]);
  if (epoch !== state.epoch) return;
  state.summaries = summaries;
  renderSummaries();
  renderLastRead(last);
}

// 进度变化或首次打开时：刷新 → 补齐缺失的摘要 → 整体回顾
async function refreshAndGenerate() {
  try {
    await refresh();
  } catch (err) {
    renderGenError(`加载摘要失败：${err.message}`, refreshAndGenerate);
    return;
  }
  if (state.summaries.missing > 0 && state.llmReady) await startGeneration();
  loadRecap();
}

// ---------- 顶部 ----------

function renderHero() {
  const { book } = state;
  const { percent } = progressInfo(book);
  const current = book.chapters[book.current_chapter - 1];
  const isLast = book.current_chapter >= book.chapter_count;

  hero.replaceChildren(
    el("div", { class: "cover-wrap" }, coverEl(book)),
    el("div", { class: "hero-info" },
      el("div", { class: "eyebrow" }, "前情提要"),
      el("h1", {}, book.title),
      el("p", { class: "sub" }, book.author || "佚名"),
      progressBar(percent),
      el("div", { class: "hero-progress-text" },
        `正在读第 ${book.current_chapter} 章 / 共 ${book.chapter_count} 章`,
        current ? ` ·「${current.title}」` : ""),
      el("div", { class: "hero-actions" },
        isLast
          ? el("span", { class: "muted", style: { fontSize: "13px", alignSelf: "center" } }, "已经读到最后一章了")
          : el("button", {
            class: "btn btn-primary", type: "button", disabled: busy(),
            title: busy() ? "等摘要生成完再标记" : "进度前进一章，并为刚读完的这一章生成摘要",
            onclick: finishCurrentChapter,
          }, icon("check"), "读完这一章"),
        el("a", { class: "btn", href: `book.html?id=${book.id}` }, icon("list"), "章节与进度"))),
  );
}

function renderNotice() {
  $("notice").replaceChildren(state.llmReady ? "" : el("div", { class: "notice" }, icon("alert"),
    el("span", {}, "还没有配置 DEEPSEEK_API_KEY，暂时不能生成新的摘要。已经生成过的内容仍然可以查看。")));
}

// ---------- 上次读到哪 ----------

function renderLastRead(data) {
  const box = $("last-read");
  if (data.chapters.length === 0) {
    const message = state.book.current_chapter === 1
      ? "你正在读第 1 章，还没有可回顾的内容。读完后点「读完这一章」，下次打开时这里会帮你接上断点。"
      : "已读的章节都被标记为跳过了，没有可回顾的内容。";
    box.replaceChildren(el("div", { class: "card muted" }, message));
    return;
  }
  const last = data.chapters[data.chapters.length - 1];
  box.replaceChildren(el("div", { class: "resume" },
    el("div", { class: "label" }, icon("bookmark"), "你停在这里"),
    last.ending_state
      ? el("blockquote", {}, last.ending_state)
      : el("p", { class: "muted", style: { margin: "12px 0 0" } }, "摘要生成后，这里会告诉你上次读到了哪里。"),
    el("div", { class: "where" }, `${chapterLabel(last)} · 章末`),
    el("div", { class: "recent-list" }, data.chapters.map((ch) => el("div", {},
      el("h3", {}, chapterLabel(ch)),
      el("p", { class: ch.summary ? "" : "muted" }, ch.summary || "等待生成…")))),
  ));
}

// ---------- 整体回顾 ----------

async function loadRecap() {
  const epoch = state.epoch;
  const box = $("recap");
  const read = state.book.current_chapter - 1;
  $("recap-hint").textContent = read > 0 ? "只根据逐章摘要写成，不重读原文" : "";
  box.replaceChildren(el("div", { class: "card" },
    el("div", { class: "skeleton", "aria-label": "正在生成整体回顾" }, ...Array.from({ length: 5 }, () => el("span")))));

  let data;
  try {
    data = await api(`/api/books/${bookId}/recap`);
  } catch (err) {
    if (epoch !== state.epoch) return;
    const message = err.status === 503 ? "配置 DEEPSEEK_API_KEY 后才能生成整体回顾。" : `整体回顾生成失败：${err.message}`;
    box.replaceChildren(el("div", { class: "card" }, el("p", { class: "muted", style: { margin: 0 } }, message),
      err.status === 503 ? null : el("button", { class: "btn btn-sm", type: "button", style: { marginTop: "12px" }, onclick: loadRecap },
        icon("refresh"), "重试")));
    return;
  }

  if (epoch !== state.epoch) return;
  if (data.status === "empty") {
    box.replaceChildren(el("div", { class: "card muted" }, "还没有可回顾的内容。"));
  } else if (data.status === "incomplete") {
    box.replaceChildren(el("div", { class: "card muted" }, `还有 ${data.missing} 章摘要没有生成，补齐后这里会出现整体回顾。`));
  } else {
    const paragraphs = data.content.split(/\n+/).filter(Boolean).map((p) => el("p", {}, p));
    box.replaceChildren(el("div", { class: "card" },
      el("div", { class: "prose" }, paragraphs),
      el("div", { class: "recap-foot" },
        el("span", {}, `回顾第 1 ~ ${read} 章`, data.created_at ? ` · 生成于 ${formatTime(data.created_at)}` : ""),
        el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: () => copyText(data.content) },
          icon("copy"), "复制"))));
  }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // 非 HTTPS 的远程地址没有 clipboard API，退回到老办法
    const area = el("textarea", { style: { position: "fixed", opacity: "0" } });
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("已复制到剪贴板");
}

// ---------- 逐章摘要 ----------

function renderSummaries() {
  const box = $("summaries");
  const { chapters } = state.summaries;
  const toggleAll = $("toggle-all");
  const done = chapters.filter((ch) => ch.summary);
  toggleAll.hidden = done.length === 0;
  toggleAll.textContent = done.length && done.every((ch) => state.open.has(ch.number)) ? "全部收起" : "全部展开";

  if (chapters.length === 0) {
    box.replaceChildren(el("div", { class: "card muted" }, "还没有可回顾的内容。"));
    return;
  }
  box.replaceChildren(el("ol", { class: "timeline" }, chapters.map(summaryItem)));
}

function summaryItem(ch) {
  const s = ch.summary;
  const head = el("summary", {},
    el("span", { class: "tl-num" }, `${ch.number}`),
    el("span", { class: "tl-title" },
      ch.parent_titles.length ? el("span", { class: "tl-parent" }, ch.parent_titles.join(" · ")) : null,
      ch.title,
      s ? null : el("span", { class: "tag" }, "等待生成")),
    icon("chevron", "chev"));

  const details = el("details", { open: state.open.has(ch.number) }, head);
  details.addEventListener("toggle", () => {
    if (details.open) state.open.add(ch.number);
    else state.open.delete(ch.number);
    renderToggleAllLabel();
  });

  if (!s) {
    details.append(el("div", { class: "tl-body muted" }, state.llmReady ? "这一章的摘要还没有生成。" : "配置 DEEPSEEK_API_KEY 后才能生成。"));
    return el("li", { class: "tl-item pending" }, details);
  }

  const regenerating = state.regenerating === ch.number;
  details.append(el("div", { class: "tl-body" },
    el("div", { class: "prose" }, el("p", {}, s.summary)),
    s.ending_state ? el("div", { class: "tl-ending" }, el("b", {}, "章末"), s.ending_state) : null,
    el("div", { class: "tl-foot" },
      el("span", {}, `${formatTime(s.created_at)}${s.version > 1 ? ` · 第 ${s.version} 版` : ""}`),
      el("button", {
        class: "btn btn-sm btn-ghost", type: "button", disabled: busy() || !state.llmReady,
        onclick: () => regenerate(ch),
      }, regenerating ? el("span", { class: "spinner" }) : icon("refresh"), regenerating ? "生成中…" : "重新生成"))));
  return el("li", { class: "tl-item" }, details);
}

function renderToggleAllLabel() {
  const done = state.summaries.chapters.filter((ch) => ch.summary);
  $("toggle-all").textContent = done.length && done.every((ch) => state.open.has(ch.number)) ? "全部收起" : "全部展开";
}

$("toggle-all").addEventListener("click", () => {
  const done = state.summaries.chapters.filter((ch) => ch.summary);
  const expand = !done.every((ch) => state.open.has(ch.number));
  for (const ch of done) expand ? state.open.add(ch.number) : state.open.delete(ch.number);
  renderSummaries();
});

// ---------- 生成 ----------

function startGeneration() {
  if (state.generating) return state.loop;
  state.loop = generateMissing();
  return state.loop;
}

async function generateMissing() {
  state.generating = true;
  state.stopRequested = false;
  setBusy();
  let done = 0;
  let remaining = state.summaries.missing;
  try {
    while (remaining > 0 && !state.stopRequested) {
      const next = state.summaries.chapters.find((ch) => !ch.summary);
      renderGenProgress(done, remaining, next ? chapterLabel(next) : "");
      const res = await api(`/api/books/${bookId}/summaries/next`, { method: "POST" });
      done += 1;
      remaining = res.missing;
      await refresh();
    }
    if (remaining > 0) renderGenPaused(remaining);
    else $("gen-status").replaceChildren();
  } catch (err) {
    renderGenError(`生成失败：${err.message}`, async () => {
      await startGeneration();
      loadRecap();
    });
  } finally {
    state.generating = false;
    setBusy();
  }
}

async function stopGeneration() {
  if (!state.generating) return;
  state.stopRequested = true;
  await state.loop; // 当前这一章生成完才会停，不浪费已经发出的请求
}

function renderGenProgress(done, remaining, label) {
  const total = done + remaining;
  $("gen-status").replaceChildren(el("div", { class: "gen-status" },
    el("span", { class: "spinner" }),
    el("div", { class: "gen-text" }, `正在生成逐章摘要 ${done + 1} / ${total}`, label ? el("small", {}, label) : null),
    el("button", {
      class: "btn btn-sm btn-ghost", type: "button",
      onclick: (e) => {
        state.stopRequested = true;
        e.currentTarget.disabled = true;
        e.currentTarget.lastChild.textContent = "这一章完成后暂停";
      },
    }, icon("pause"), el("span", {}, "暂停")),
    progressBar(Math.round((done / total) * 100))));
}

function renderGenPaused(remaining) {
  $("gen-status").replaceChildren(el("div", { class: "gen-status" },
    el("div", { class: "gen-text" }, `已暂停，还有 ${remaining} 章摘要没有生成`),
    el("button", {
      class: "btn btn-sm btn-primary", type: "button",
      onclick: async () => {
        await startGeneration();
        loadRecap();
      },
    }, icon("play"), "继续生成")));
}

function renderGenError(message, retry) {
  $("gen-status").replaceChildren(el("div", { class: "gen-status error" },
    el("div", { class: "gen-text" }, message, el("small", {}, "已经生成的章节都已保存，重试会从断点继续。")),
    el("button", { class: "btn btn-sm", type: "button", onclick: retry }, icon("refresh"), "重试")));
}

function setBusy() {
  renderHero();
  if (state.summaries) renderSummaries();
}

async function regenerate(ch) {
  if (busy()) return;
  state.regenerating = ch.number;
  setBusy();
  try {
    await api(`/api/books/${bookId}/chapters/${ch.number}/summary/regenerate`, { method: "POST" });
    state.regenerating = null;
    await refresh();
    toast(`已重新生成「${ch.title}」的摘要`);
    loadRecap(); // 参与汇总的摘要变了，回顾会重新生成
  } catch (err) {
    toast(err.message, { type: "error" });
  } finally {
    state.regenerating = null;
    setBusy();
  }
}

// ---------- 进度 ----------

async function setProgress(chapter) {
  await stopGeneration();
  await setProgressQuietly(chapter);
  await refreshAndGenerate();
}

async function finishCurrentChapter() {
  const previous = state.book.current_chapter;
  const finished = state.book.chapters[previous - 1];
  try {
    await setProgressQuietly(previous + 1);
  } catch (err) {
    toast(err.message, { type: "error" });
    return;
  }
  toast(`读完了「${finished.title}」，接下来读第 ${previous + 1} 章`, {
    action: { label: "撤销", run: () => setProgress(previous).catch((err) => toast(err.message, { type: "error" })) },
  });
  await refreshAndGenerate();
}

async function setProgressQuietly(chapter) {
  state.book = await putJson(`/api/books/${bookId}/progress`, { chapter });
  state.epoch += 1;
  renderHero();
}

init();
