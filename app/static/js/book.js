// 章节与进度：按卷分组的章节列表，设置"正在读的章节"、勾选跳过

const bookId = bookIdFromUrl();
const hero = document.getElementById("hero");
const list = document.getElementById("chapters");
const veilToggle = document.getElementById("veil-toggle");
let book = null;
let firstRender = true;

async function load() {
  if (bookId === null) return showError("链接里缺少书的编号。");
  try {
    book = await api(`/api/books/${bookId}`);
  } catch (err) {
    return showError(err.status === 404 ? "这本书不存在，可能已经被删除了。" : err.message);
  }
  render();
}

function showError(message) {
  hero.replaceChildren();
  list.replaceChildren(el("div", { class: "empty" }, el("h2", {}, "打不开这本书"), el("p", {}, message),
    el("a", { class: "btn", href: "./" }, "回到书架")));
}

function render() {
  document.title = `章节 · ${book.title}`;
  document.getElementById("crumb-title").textContent = book.title;
  renderHero();
  renderChapters();
  if (firstRender) {
    firstRender = false;
    // 打开时直接定位到正在读的章节，长目录不用手动翻
    document.querySelector(".ch-row.current")?.scrollIntoView({ block: "center" });
  }
}

function renderHero() {
  const { percent } = progressInfo(book);
  const current = book.chapters[book.current_chapter - 1];
  hero.replaceChildren(
    el("div", { class: "cover-wrap" }, coverEl(book)),
    el("div", { class: "hero-info" },
      el("div", { class: "eyebrow" }, "章节与进度"),
      el("h1", {}, book.title),
      el("p", { class: "sub" }, book.author || "佚名"),
      progressBar(percent),
      el("div", { class: "hero-progress-text" },
        `正在读第 ${book.current_chapter} 章 / 共 ${book.chapter_count} 章`,
        current ? ` ·「${current.title}」` : ""),
      el("div", { class: "hero-actions" },
        el("a", { class: "btn btn-primary", href: `summary.html?id=${book.id}` }, icon("sparkle"), "查看前情提要"))),
  );
}

// 连续的、父级标题相同的章节归为一组（如同一卷）
function groupChapters(chapters) {
  const groups = [];
  for (const ch of chapters) {
    const key = ch.parent_titles.join(" · ");
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.chapters.push(ch);
    else groups.push({ key, chapters: [ch] });
  }
  return groups;
}

function renderChapters() {
  const groups = groupChapters(book.chapters).map((group) => {
    // 整卷都没读到时，卷名本身也可能剧透
    const allUnread = group.chapters.every((ch) => ch.number > book.current_chapter);
    return el("section", { class: "ch-group" },
      group.key ? el("h2", { class: `ch-group-title ${allUnread ? "veiled" : ""}`, onclick: reveal }, group.key) : null,
      el("ol", { class: "ch-list" }, group.chapters.map(chapterRow)));
  });
  list.replaceChildren(...groups);
}

function chapterRow(ch) {
  const state = ch.number < book.current_chapter ? "read" : ch.number === book.current_chapter ? "current" : "unread";
  const classes = ["ch-row", state, ch.skipped ? "skipped" : ""].join(" ");

  const skip = el("input", {
    type: "checkbox", "aria-label": `跳过「${ch.title}」`,
    onchange: () => update(`/api/books/${book.id}/chapters/${ch.number}/skipped`, { skipped: skip.checked },
      skip.checked ? `已跳过第 ${ch.number} 章，它不会出现在提要里` : `已取消跳过第 ${ch.number} 章`),
  });
  skip.checked = ch.skipped;

  const tags = [];
  if (state === "current") tags.push(el("span", { class: "tag accent" }, "正在读"));
  if (ch.mentions_book_info && !ch.skipped) {
    tags.push(el("span", { class: "tag warn", title: "这一页含书名和作者，不跳过的话会随正文发给模型" }, "含书名/作者，建议跳过"));
  }

  return el("li", { class: classes },
    el("span", { class: "ch-state", "aria-hidden": "true" }, state === "read" ? icon("check") : ch.number),
    el("span", { class: "ch-title" },
      el("span", { class: state === "unread" ? "veiled" : "", onclick: reveal, title: state === "unread" ? "点击显示标题" : null },
        ch.title),
      tags),
    el("span", { class: "ch-actions" },
      state === "current" ? null : el("button", {
        class: "btn btn-sm btn-ghost set-current", type: "button",
        onclick: () => setCurrent(ch),
      }, "从这里读"),
      el("label", { class: "switch", title: "序言、版权页、致谢等非正文可以跳过" }, skip, "跳过")));
}

// 点一下才显示被模糊的未读标题，避免扫一眼目录就被剧透
function reveal(e) {
  if (e.currentTarget.classList.contains("veiled")) e.currentTarget.classList.add("revealed");
}

async function setCurrent(ch) {
  const previous = book.current_chapter;
  const ok = await update(`/api/books/${book.id}/progress`, { chapter: ch.number }, `已设为正在读第 ${ch.number} 章`, {
    label: "撤销",
    run: () => update(`/api/books/${book.id}/progress`, { chapter: previous }, `已恢复到第 ${previous} 章`),
  });
  if (ok) document.querySelector(".ch-row.current")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function update(path, body, message, action = null) {
  try {
    book = await putJson(path, body);
    render();
    if (message) toast(message, { action });
    return true;
  } catch (err) {
    toast(err.message, { type: "error" });
    render(); // 把复选框恢复成服务端的状态
    return false;
  }
}

veilToggle.checked = Prefs.get("veilUnread");
veilToggle.addEventListener("change", () => {
  Prefs.set("veilUnread", veilToggle.checked);
  if (book) renderChapters();
});

load();
