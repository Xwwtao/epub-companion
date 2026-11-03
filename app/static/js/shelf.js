// 书架：列表、搜索、导入（按钮 / 拖放，可多选）、删除

const grid = document.getElementById("books");
const search = document.getElementById("search");
const fileInput = document.getElementById("file-input");
let books = [];
let introPlayed = false;
const pending = new Set(); // 正在导入的文件名，在列表最前面显示占位卡片

async function load() {
  try {
    books = await api("/api/books");
  } catch (err) {
    grid.replaceChildren(el("div", { class: "empty", style: { gridColumn: "1 / -1" } },
      el("h2", {}, "书架加载失败"), el("p", {}, err.message),
      el("button", { class: "btn", type: "button", onclick: load }, icon("refresh"), "重试")));
    return;
  }
  render();
}

function render() {
  const keyword = search.value.trim().toLowerCase();
  // 书多了才需要搜索框
  search.hidden = books.length < 4;
  const shown = keyword
    ? books.filter((b) => `${b.title} ${b.author || ""}`.toLowerCase().includes(keyword))
    : books;

  document.getElementById("shelf-sub").textContent = books.length
    ? `共 ${books.length} 本书。读到哪，提要就只讲到哪。`
    : "导入 EPUB，读到哪，提要就只讲到哪。";

  grid.classList.toggle("intro", !introPlayed);
  introPlayed = true;
  const cards = [...[...pending].map(pendingCard), ...shown.map(bookCard)];
  if (cards.length === 0) {
    grid.replaceChildren(keyword ? noMatch() : emptyState());
    return;
  }
  grid.replaceChildren(...cards);
}

function bookCard(book, index) {
  const { percent, text } = progressInfo(book);
  return el("article", { class: "book-card", style: { animationDelay: `${Math.min(index, 12) * 30}ms` } },
    el("a", { class: "cover-link", href: `summary.html?id=${book.id}`, title: `打开《${book.title}》的前情提要` },
      coverEl(book)),
    el("div", { class: "book-meta" },
      el("a", { class: "book-title", href: `summary.html?id=${book.id}` }, book.title),
      el("span", { class: "book-author" }, book.author || "佚名"),
      el("div", { class: "book-progress", title: `正在读第 ${book.current_chapter} 章 / 共 ${book.chapter_count} 章` },
        progressBar(percent), el("span", {}, text))),
    el("div", { class: "book-actions" },
      el("a", { class: "btn btn-sm", href: `summary.html?id=${book.id}` }, "提要"),
      el("a", { class: "btn btn-sm", href: `book.html?id=${book.id}` }, "章节"),
      el("button", {
        class: "btn btn-sm btn-ghost btn-icon", type: "button", "aria-label": `删除《${book.title}》`, title: "删除",
        onclick: () => remove(book),
      }, icon("trash"))));
}

function pendingCard(name) {
  return el("article", { class: "book-card pending" },
    el("div", { class: "cover" }, el("span", { class: "spinner" })),
    el("div", { class: "book-meta" },
      el("span", { class: "book-title" }, name.replace(/\.epub$/i, "")),
      el("span", { class: "book-author" }, "正在解析目录和章节…")));
}

function emptyState() {
  const art = icon("book", "empty-art");
  art.setAttribute("stroke-width", "1.2");
  return el("div", { class: "empty", style: { gridColumn: "1 / -1" } },
    art,
    el("h2", {}, "书架还是空的"),
    el("p", {}, "把 EPUB 文件拖到这里，或者点右上角的「导入 EPUB」。设置好读到第几章，就能看到不剧透的前情提要。"),
    el("button", { class: "btn btn-primary", type: "button", onclick: () => fileInput.click() }, icon("upload"), "导入第一本书"));
}

function noMatch() {
  return el("div", { class: "empty", style: { gridColumn: "1 / -1" } },
    el("h2", {}, "没有找到"), el("p", {}, `书架上没有和「${search.value.trim()}」相关的书。`));
}

// 逐个上传：解析一本书要一点时间，并发上传对单进程的服务没有好处
async function importFiles(files) {
  const epubs = [...files].filter((f) => /\.epub$/i.test(f.name));
  if (epubs.length < files.length) toast("只能导入 .epub 文件，其他文件已忽略", { type: "error" });
  for (const file of epubs) {
    pending.add(file.name);
    render();
    const form = new FormData();
    form.append("file", file);
    try {
      const book = await api("/api/books", { method: "POST", body: form });
      books.unshift(book);
      toast(`已导入《${book.title}》，共 ${book.chapter_count} 章`);
    } catch (err) {
      toast(`《${file.name}》导入失败：${err.message}`, { type: "error", duration: 6000 });
    } finally {
      pending.delete(file.name);
      render();
    }
  }
}

async function remove(book) {
  const ok = await confirmDialog({
    title: `删除《${book.title}》？`,
    message: "书的文件、阅读进度和所有已生成的摘要都会一起删除，无法恢复。",
    confirmText: "删除",
    danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/books/${book.id}`, { method: "DELETE" });
    books = books.filter((b) => b.id !== book.id);
    render();
    toast(`已删除《${book.title}》`);
  } catch (err) {
    toast(`删除失败：${err.message}`, { type: "error" });
  }
}

// ---------- 事件 ----------

document.getElementById("import-btn").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  importFiles(fileInput.files);
  fileInput.value = "";
});
search.addEventListener("input", render);

// 整个页面都可以拖放；dragenter/dragleave 会在子元素间反复触发，用计数判断是否真的离开
const overlay = document.getElementById("drop-overlay");
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", (e) => {
  if (!hasFiles(e)) return;
  dragDepth += 1;
  overlay.classList.add("show");
});
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) overlay.classList.remove("show");
});
window.addEventListener("dragover", (e) => {
  if (hasFiles(e)) e.preventDefault();
});
window.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  overlay.classList.remove("show");
  importFiles(e.dataTransfer.files);
});

load();
