// 各页面共用：API 调用、DOM 构造、阅读设置、提示消息、确认框。
// 不引入框架和构建工具（spec §5.1），每个页面直接用 <script> 引入。

// ---------- API ----------

// 默认和页面同源。前端单独部署时，在页面的 <meta name="api-base"> 里填后端地址，
// 后端相应地配置 CORS_ORIGINS。
const API_BASE = (document.querySelector('meta[name="api-base"]')?.content || "").replace(/\/$/, "");

function apiUrl(path) {
  return API_BASE + path;
}

async function api(path, options = {}) {
  let res;
  try {
    // 跨域部署时带上 Cookie，为以后的登录预留
    res = await fetch(apiUrl(path), { credentials: API_BASE ? "include" : "same-origin", ...options });
  } catch {
    throw new Error("连接不上服务器，请确认服务正在运行");
  }
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {}
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

function putJson(path, body) {
  return api(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ---------- DOM ----------

// 文本一律用 textContent 插入：书名、章节标题、摘要都来自 EPUB 或模型，不能当 HTML。
// attrs 里 on* 绑定事件，值为 false / null 的属性不设置。
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value === null || value === undefined) continue;
    if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    // setProperty 才能设置 --hue 这类 CSS 变量，直接赋值给 style 对象会被静默忽略
    else if (key === "style" && typeof value === "object") {
      for (const [prop, v] of Object.entries(value)) {
        if (prop.startsWith("--")) node.style.setProperty(prop, v);
        else node.style[prop] = v;
      }
    }
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false || child === "") continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// 图标是写死在这里的常量，不含任何外部数据，可以安全地当 SVG 解析
const ICONS = {
  book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/>',
  settings: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  upload: '<path d="M12 15V4M7 9l5-5 5 5"/><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
  bookmark: '<path d="M6 3h12v18l-6-4-6 4z"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  pause: '<path d="M9 5v14M15 5v14"/>',
  play: '<path d="m7 4 13 8-13 8z"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
};

function icon(name, extraClass = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", `icon ${extraClass}`.trim());
  svg.innerHTML = ICONS[name] || "";
  return svg;
}

function chapterLabel(ch) {
  return [...ch.parent_titles, ch.title].join(" · ");
}

function bookIdFromUrl() {
  const id = Number(new URLSearchParams(location.search).get("id"));
  return Number.isInteger(id) && id > 0 ? id : null;
}

// 进度语义是"正在读第 N 章"，已读的是第 1 ~ N-1 章
function progressInfo(book) {
  const read = book.current_chapter - 1;
  const percent = book.chapter_count ? Math.round((read / book.chapter_count) * 100) : 0;
  const text = read === 0 ? "尚未开始" : `已读 ${read} / ${book.chapter_count} 章`;
  return { read, percent, text };
}

function progressBar(percent) {
  return el("div", { class: "progress", role: "progressbar", "aria-valuenow": percent, "aria-valuemin": 0, "aria-valuemax": 100 },
    el("span", { style: { width: `${percent}%` } }));
}

// 没有封面时按书名生成一张固定配色的占位封面，同一本书每次颜色一样
function coverEl(book) {
  const placeholder = () => {
    let hash = 0;
    for (const ch of book.title) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
    return el("div", { class: "cover cover-ph", style: { "--hue": String(hash % 360) }, "aria-hidden": "true" },
      el("div", {}, el("div", { class: "ph-title" }, book.title), el("div", { class: "ph-rule" })),
      el("div", { class: "ph-author" }, book.author || ""));
  };
  if (!book.has_cover) return placeholder();
  const img = el("img", { class: "cover", src: apiUrl(`/api/books/${book.id}/cover`), alt: "", loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(placeholder()), { once: true });
  return img;
}

function formatTime(sqliteUtc) {
  // SQLite 的 CURRENT_TIMESTAMP 是 UTC 的 "YYYY-MM-DD HH:MM:SS"
  if (!sqliteUtc) return "";
  const date = new Date(sqliteUtc.replace(" ", "T") + "Z");
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// ---------- 阅读设置（只存在浏览器里） ----------

const Prefs = {
  defaults: { theme: "auto", fontSize: 18, veilUnread: true },
  get(key) {
    try {
      const raw = localStorage.getItem(`ec.${key}`);
      return raw === null ? this.defaults[key] : JSON.parse(raw);
    } catch {
      return this.defaults[key];
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`ec.${key}`, JSON.stringify(value));
    } catch {}
    applyPrefs();
  },
};

function applyPrefs() {
  const root = document.documentElement;
  const theme = Prefs.get("theme");
  if (theme === "auto") delete root.dataset.theme;
  else root.dataset.theme = theme;
  root.style.setProperty("--read-size", `${Prefs.get("fontSize")}px`);
  root.classList.toggle("veil-unread", Prefs.get("veilUnread"));
  // 让手机浏览器的地址栏颜色跟着主题走
  const meta = document.querySelector('meta[name="theme-color"]');
  // 读 CSS 变量而不是 body 的背景色：body 有过渡动画，切换瞬间读到的还是旧颜色
  if (meta) meta.content = getComputedStyle(root).getPropertyValue("--bg").trim();
}

const THEMES = [
  { id: "auto", name: "跟随系统", bg: "linear-gradient(135deg, #f6f2ea 50%, #1f1d1a 50%)", fg: "#9a5b2f" },
  { id: "paper", name: "纸张", bg: "#fdfbf7", fg: "#2b2621" },
  { id: "sepia", name: "护眼", bg: "#f2e8d2", fg: "#3d3326" },
  { id: "night", name: "夜间", bg: "#1f1d1a", fg: "#e7e0d4" },
];

function mountSettings() {
  const btn = document.getElementById("settings-btn");
  if (!btn) return;
  let panel = null;

  const close = () => {
    panel?.remove();
    panel = null;
    btn.setAttribute("aria-expanded", "false");
  };

  const build = () => {
    const swatches = THEMES.map((t) => el("button", {
      class: "swatch", type: "button", "aria-pressed": String(Prefs.get("theme") === t.id),
      onclick: () => {
        Prefs.set("theme", t.id);
        for (const s of swatches) s.setAttribute("aria-pressed", String(s === swatches[THEMES.indexOf(t)]));
      },
    }, el("i", { style: { background: t.bg, color: t.fg } }, "文"), t.name));

    const value = el("span", { class: "value" }, `${Prefs.get("fontSize")}px`);
    const range = el("input", {
      type: "range", min: 15, max: 24, step: 1, value: Prefs.get("fontSize"), "aria-label": "正文字号",
      oninput: (e) => { Prefs.set("fontSize", Number(e.target.value)); value.textContent = `${e.target.value}px`; },
    });

    const veil = el("input", { type: "checkbox", onchange: (e) => Prefs.set("veilUnread", e.target.checked) });
    veil.checked = Prefs.get("veilUnread");

    return el("div", { class: "popover", role: "dialog", "aria-label": "阅读设置" },
      el("h3", {}, "主题"),
      el("div", { class: "theme-swatches row" }, swatches),
      el("h3", {}, "正文字号"),
      el("div", { class: "font-size-row row" }, el("span", { class: "muted", style: { fontSize: "13px" } }, "A"), range,
        el("span", { class: "muted", style: { fontSize: "18px" } }, "A"), value),
      el("h3", {}, "防剧透"),
      el("label", { class: "switch row" }, veil, "模糊未读章节的标题"),
    );
  };

  const open = () => {
    panel = build();
    document.body.append(panel);
    btn.setAttribute("aria-expanded", "true");
  };

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    panel ? close() : open();
  });
  document.addEventListener("click", (e) => {
    if (panel && !panel.contains(e.target)) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });
}

// ---------- 提示消息 ----------

function toast(message, { type = "info", action = null, duration = 3600 } = {}) {
  let host = document.querySelector(".toasts");
  if (!host) {
    host = el("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(host);
  }
  const node = el("div", { class: `toast ${type}` }, el("span", {}, message));
  const dismiss = () => {
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 200);
  };
  if (action) {
    node.append(el("button", { type: "button", onclick: () => { dismiss(); action.run(); } }, action.label));
  }
  host.append(node);
  setTimeout(dismiss, action ? duration * 1.8 : duration);
}

// 用原生 <dialog> 做确认框，代替样式无法控制的 confirm()
function confirmDialog({ title, message, confirmText = "确定", danger = false }) {
  return new Promise((resolve) => {
    const dialog = el("dialog", { class: "confirm" },
      el("h2", {}, title),
      el("p", {}, message),
      el("form", { method: "dialog", class: "actions" },
        el("button", { class: "btn btn-ghost", value: "cancel" }, "取消"),
        el("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, value: "ok" }, confirmText)));
    dialog.addEventListener("close", () => {
      resolve(dialog.returnValue === "ok");
      dialog.remove();
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}

// ---------- 页面初始化 ----------

function watchScroll() {
  const bar = document.querySelector(".topbar");
  if (!bar) return;
  const update = () => bar.classList.toggle("scrolled", window.scrollY > 4);
  window.addEventListener("scroll", update, { passive: true });
  update();
}

applyPrefs();
document.addEventListener("DOMContentLoaded", () => {
  applyPrefs();
  mountSettings();
  watchScroll();
});
