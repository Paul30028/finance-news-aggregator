// 纯原生 JS，无第三方依赖：负责新闻列表的筛选、分页、自动轮询刷新，
// 以及数据源管理页的增/删/启停操作。

function currentQuery(extra) {
  const params = new URLSearchParams();
  const category = document.getElementById("f-category")?.value || "";
  const source = document.getElementById("f-source")?.value || "";
  const tier = document.getElementById("f-tier")?.value || "";
  const q = document.getElementById("f-q")?.value || "";
  if (category) params.set("category", category);
  if (source) params.set("source", source);
  if (tier) params.set("tier", tier);
  if (q) params.set("q", q);
  Object.entries(extra || {}).forEach(([k, v]) => params.set(k, v));
  return params.toString();
}

async function refreshList(page) {
  const p = page || window.APP_STATE.page || 1;
  const qs = currentQuery({ page: p });
  const resp = await fetch(`/partials/news-list?${qs}`);
  if (!resp.ok) return;
  const html = await resp.text();
  document.getElementById("news-list").innerHTML = html;
  window.APP_STATE.page = p;
}

function applyFilters(evt) {
  evt.preventDefault();
  refreshList(1);
}

function loadPage(p) {
  refreshList(p);
}

async function crawlNow() {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = "抓取中...";
  try {
    const resp = await fetch("/api/crawl-now", { method: "POST" });
    const data = await resp.json();
    await refreshList();
    btn.textContent = `完成，新增 ${data.new_articles} 条`;
  } catch (e) {
    btn.textContent = "抓取失败";
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = "立即抓取"; }, 2000);
  }
}

function initNewsList() {
  if (!window.APP_STATE) return;
  // SSE（initLiveUpdates）负责"即时"刷新；这里只是一个兜底轮询，
  // 防止 SSE 连接异常断开时列表长时间不更新，固定 30s，不依赖抓取间隔配置。
  setInterval(() => refreshList(window.APP_STATE.page), 30 * 1000);
}

// ------------------------- 实时推送（SSE） -------------------------

function initLiveUpdates() {
  const dot = document.getElementById("live-dot");
  const status = document.getElementById("live-status");
  if (!window.EventSource) {
    if (status) status.textContent = "浏览器不支持实时推送，已启用轮询刷新";
    return;
  }

  const es = new EventSource("/events/stream");

  es.onopen = () => {
    if (dot) dot.classList.remove("offline");
    if (status) status.textContent = "实时推送中";
  };

  es.onmessage = (evt) => {
    let items = [];
    try {
      items = JSON.parse(evt.data);
    } catch (e) {
      return;
    }
    if (!items.length) return;
    if (status) status.textContent = `刚刚推送 ${items.length} 条新新闻`;
    // 新文章已经立即入库，这里直接刷新当前视图即可看到（含 NEW 徽标）
    refreshList(window.APP_STATE.page);
  };

  es.onerror = () => {
    if (dot) dot.classList.add("offline");
    if (status) status.textContent = "实时推送已断开，正在重连...";
    // 浏览器 EventSource 会自动重连，无需手动处理
  };
}

// ------------------------- 数据源管理页 -------------------------

async function toggleSource(name) {
  await fetch(`/api/sources/${encodeURIComponent(name)}/toggle`, { method: "POST" });
  location.reload();
}

async function deleteSource(name) {
  if (!confirm(`确定删除数据源 "${name}" 吗？`)) return;
  await fetch(`/api/sources/${encodeURIComponent(name)}`, { method: "DELETE" });
  location.reload();
}

async function addSource(evt) {
  evt.preventDefault();
  const form = evt.target;
  const fd = new FormData(form);
  const intervalRaw = fd.get("interval_seconds");
  const payload = {
    name: fd.get("name"),
    type: fd.get("type"),
    url: fd.get("url"),
    enabled: fd.get("enabled") === "on",
    category_hint: fd.get("category_hint") || null,
    list_selector: fd.get("list_selector") || null,
    interval_seconds: intervalRaw ? parseInt(intervalRaw, 10) : null,
    tier: fd.get("tier") || null,
  };
  const resp = await fetch("/api/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await resp.json();
  const msgEl = document.getElementById("add-source-msg");
  if (resp.ok) {
    msgEl.textContent = data.message;
    setTimeout(() => location.reload(), 800);
  } else {
    msgEl.textContent = "添加失败：" + (data.detail ? JSON.stringify(data.detail) : "未知错误");
  }
}
