// 各页面共用的小工具

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const body = await res.json();
      if (body.detail) message = body.detail;
    } catch {}
    throw new Error(message);
  }
  return res.status === 204 ? null : res.json();
}

// 用 textContent 拼 DOM，书名、章节标题来自 EPUB，不能当 HTML 插入
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const child of children) {
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
