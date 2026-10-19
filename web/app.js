/* 轻连 VPN - local UI controller */
"use strict";

const $ = (id) => document.getElementById(id);
const PAGE_TITLES = {
  home: "网络总览",
  nodes: "节点列表",
  subscriptions: "订阅管理",
  settings: "偏好设置",
};
const PROTOCOL_NAMES = {
  vmess: "VMess",
  vless: "VLESS",
  trojan: "Trojan",
  shadowsocks: "Shadowsocks",
  hysteria2: "Hysteria2",
  tuic: "TUIC",
  socks: "SOCKS",
  http: "HTTP",
  anytls: "AnyTLS",
  wireguard: "WireGuard",
};
const META_TYPES = ["vless", "trojan", "hysteria2", "tuic", "shadowsocks", "vmess"];
const SVG_NS = "http://www.w3.org/2000/svg";

const S = {
  data: null,
  status: null,
  page: "home",
  filter: "all",
  search: "",
  testing: new Set(),
  testingAll: false,
  busy: false,
  lastError: "",
  knownState: "disconnected",
  pollTimer: null,
  pollBusy: false,
  booted: false,
};

function icon(name, className = "icon") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", className);
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", "#i-" + name);
  svg.appendChild(use);
  return svg;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function button(label, className, onClick) {
  const btn = el("button", className || "btn ghost", label);
  btn.type = "button";
  if (onClick) btn.addEventListener("click", onClick);
  return btn;
}

async function api(path, body) {
  const options = { method: body === undefined ? "GET" : "POST", headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new Error("本地服务未响应，请重新启动轻连");
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = { error: "服务返回了无法识别的数据" };
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || ("请求失败（HTTP " + response.status + "）"));
  }
  return payload;
}

function toast(message, type = "info", duration = 3800) {
  const stack = $("toastStack");
  if (!stack) return;
  const item = el("div", "toast " + type);
  item.appendChild(el("span", "toast-dot"));
  item.appendChild(el("span", "", String(message)));
  stack.appendChild(item);
  while (stack.children.length > 4) stack.firstElementChild.remove();
  window.setTimeout(() => {
    item.classList.add("out");
    window.setTimeout(() => item.remove(), 250);
  }, duration);
}

function firstLine(message) {
  return String(message || "").split("\n").filter(Boolean)[0] || "操作失败";
}

function formatBytes(value) {
  const n = Number(value) || 0;
  if (n < 1024) return Math.round(n) + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let current = n / 1024;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return (current >= 100 ? current.toFixed(0) : current.toFixed(1)) + " " + units[index];
}

function formatRate(value) {
  return formatBytes(value) + "/s";
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  if (total < 60) return total + " 秒";
  if (total < 3600) return Math.floor(total / 60) + " 分 " + (total % 60) + " 秒";
  return Math.floor(total / 3600) + " 时 " + Math.floor((total % 3600) / 60) + " 分";
}

function formatDate(timestamp) {
  if (!timestamp) return "尚未更新";
  const date = new Date(Number(timestamp) * 1000);
  if (Number.isNaN(date.getTime())) return "尚未更新";
  const now = Date.now();
  const diff = Math.max(0, now - date.getTime());
  if (diff < 60 * 1000) return "刚刚更新";
  if (diff < 3600 * 1000) return Math.floor(diff / 60000) + " 分钟前更新";
  if (diff < 24 * 3600 * 1000) return Math.floor(diff / 3600000) + " 小时前更新";
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function latencyInfo(node) {
  if (S.testing.has(node.id)) return { label: "测速中…", cls: "testing" };
  const delay = node.delay;
  if (delay === null || delay === undefined) return { label: "未测速", cls: "" };
  if (delay < 0) return { label: "连接超时", cls: "timeout" };
  if (delay < 250) return { label: delay + " ms", cls: "good" };
  if (delay < 600) return { label: delay + " ms", cls: "mid" };
  return { label: delay + " ms", cls: "bad" };
}

function nodeTypeClass(type) {
  return META_TYPES.includes(type) ? type : "other";
}

function protocolName(node) {
  return node && (node.protocol || PROTOCOL_NAMES[node.type] || String(node.type || "未知").toUpperCase());
}

function selectedNode(status = S.status) {
  if (!S.data) return null;
  const id = (status && status.node_id) || S.data.selected_id;
  return S.data.nodes.find((node) => node.id === id) || null;
}

function nodeById(id) {
  return S.data ? S.data.nodes.find((node) => node.id === id) || null : null;
}

function activeSetting(key) {
  if (!S.data) return false;
  if (key === "autostart") return Boolean(S.data.autostart_enabled);
  return Boolean(S.data.settings && S.data.settings[key]);
}

function setButtonBusy(btn, busy, busyText) {
  if (!btn) return;
  if (busy) {
    if (!btn.dataset.originalHtml) btn.dataset.originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = busyText || "处理中…";
  } else {
    btn.disabled = false;
    if (btn.dataset.originalHtml) {
      btn.innerHTML = btn.dataset.originalHtml;
      delete btn.dataset.originalHtml;
    }
  }
}

function showPage(name, updateHash = true) {
  if (!PAGE_TITLES[name]) return;
  S.page = name;
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === "page-" + name));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === name));
  $("pageTitle").textContent = PAGE_TITLES[name];
  if (updateHash) {
    try { history.replaceState(null, "", "#" + name); } catch (error) {}
  }
  if (name === "nodes" && S.data) renderNodes();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- state rendering ---------- */
function applyState(state) {
  if (!state) return;
  S.data = state;
  if (state.status) S.status = state.status;
  renderAll();
}

function renderAll() {
  if (!S.data) return;
  renderHeaderAndCounts();
  renderStatus(S.status || S.data.status);
  renderHomeNodes();
  renderNodes();
  renderSubscriptions();
  renderSettings();
}

function renderHeaderAndCounts() {
  const nodes = S.data.nodes || [];
  const count = nodes.length;
  $("navNodeCount").textContent = String(count);
  $("nodesCount").textContent = String(count);
  $("subCountBadge").textContent = (S.data.subscriptions || []).length + " 个来源";
  $("diagNodeCount").textContent = String(count);
  $("diagAppVersion").textContent = "v" + (S.data.version || "1.0.0");
  const exportBtn = $("clipboardExportBtn");
  if (exportBtn) exportBtn.disabled = count === 0;
}

function setDot(element, state) {
  if (!element) return;
  element.classList.remove("online", "pending", "error");
  if (state === "connected") element.classList.add("online");
  else if (state === "starting") element.classList.add("pending");
  else if (state === "error") element.classList.add("error");
}

function renderStatus(status) {
  if (!status || !S.data) return;
  const connected = Boolean(status.connected);
  const starting = Boolean(status.starting) || S.busy;
  const hasError = Boolean(status.error);
  const node = selectedNode(status);
  const settings = S.data.settings || {};
  const hero = document.querySelector(".hero-card");
  hero.classList.toggle("connected", connected);
  setDot($("heroLiveDot"), connected ? "connected" : starting ? "starting" : hasError ? "error" : "disconnected");
  setDot($("sideDot"), connected ? "connected" : starting ? "starting" : hasError ? "error" : "disconnected");

  const powerBtn = $("connectBtn");
  powerBtn.classList.toggle("connecting", starting && !connected);
  powerBtn.disabled = S.busy || (!connected && !starting && (!S.data.nodes || !S.data.nodes.length));
  $("connectLabel").textContent = connected ? "断开" : starting ? "连接中" : "连接";

  const corePill = $("corePill");
  corePill.classList.remove("online", "error");
  if (connected) {
    corePill.classList.add("online");
    corePill.querySelector("b").textContent = "核心在线";
    $("sideStatus").textContent = "已连接";
    $("sideSub").textContent = node ? node.name : "代理运行中";
  } else if (starting) {
    corePill.querySelector("b").textContent = "正在启动";
    $("sideStatus").textContent = "正在连接";
    $("sideSub").textContent = "正在准备代理核心";
  } else if (hasError) {
    corePill.classList.add("error");
    corePill.querySelector("b").textContent = "核心异常";
    $("sideStatus").textContent = "连接失败";
    $("sideSub").textContent = "请查看错误提示";
  } else {
    corePill.querySelector("b").textContent = "核心待命";
    $("sideStatus").textContent = "未连接";
    $("sideSub").textContent = "本地代理待命";
  }

  if (node) {
    $("currentNodeName").textContent = node.name;
    $("heroProtocol").textContent = protocolName(node);
    const bits = [];
    bits.push(node.server + ":" + node.port);
    if (node.network && node.network !== "tcp") bits.push(String(node.network).toUpperCase());
    if (node.tls) bits.push("TLS");
    $("currentNodeMeta").textContent = bits.join("  ·  ");
  } else {
    $("currentNodeName").textContent = S.data.nodes.length ? "请选择一个节点" : "还没有节点";
    $("heroProtocol").textContent = "未选择";
    $("currentNodeMeta").textContent = S.data.nodes.length ? "从节点列表中选择一条线路" : "导入订阅或 v2rayN 节点后即可一键连接";
  }

  $("proxyAddress").textContent = "127.0.0.1:" + (settings.local_port || 2080);
  if (settings.tun_mode) {
    $("systemProxyText").textContent = "TUN 全程接管";
  } else if (connected && status.system_proxy) {
    $("systemProxyText").textContent = "已接管系统代理";
  } else if (settings.system_proxy) {
    $("systemProxyText").textContent = "连接后自动启用";
  } else {
    $("systemProxyText").textContent = "已关闭";
  }
  $("uptimeText").textContent = connected ? formatDuration(status.uptime) : "--";

  const alertBox = $("heroAlert");
  alertBox.classList.remove("warning", "info");
  if (hasError) {
    alertBox.textContent = status.error;
    alertBox.classList.remove("hidden");
  } else if (status.warning) {
    alertBox.classList.add("warning");
    alertBox.textContent = status.warning;
    alertBox.classList.remove("hidden");
  } else {
    alertBox.classList.add("hidden");
    alertBox.textContent = "";
  }

  document.querySelectorAll("#modeSegmented button").forEach((item) => {
    item.classList.toggle("active", item.dataset.mode === settings.mode);
  });
  const systemToggle = $("homeSystemProxyToggle");
  if (systemToggle) {
    systemToggle.setAttribute("aria-checked", String(Boolean(settings.system_proxy)));
    systemToggle.classList.toggle("disabled", S.busy);
  }
  const tunToggle = $("homeTunToggle");
  if (tunToggle) {
    tunToggle.setAttribute("aria-checked", String(Boolean(settings.tun_mode)));
    tunToggle.classList.toggle("disabled", S.busy);
  }
  $("tunStatusText").textContent = S.data.is_admin ? "管理员已就绪" : "需要管理员权限";
  $("coreVersionText").textContent = (status.core_version || "sing-box").replace(/^sing-box\s*/i, "") || "sing-box";
  $("adminText").textContent = S.data.is_admin ? "管理员权限" : "标准权限";
  $("testCurrentBtn").disabled = !node || S.testing.size > 0 || S.testingAll;
  $("chooseNodeBtn").disabled = !S.data.nodes.length;
  renderTraffic(status.traffic || {});
}

function renderTraffic(traffic) {
  const connected = S.status && S.status.connected;
  $("downSpeed").textContent = formatRate(traffic.down || 0);
  $("upSpeed").textContent = formatRate(traffic.up || 0);
  $("totalDown").textContent = formatBytes(traffic.down_total || 0);
  $("totalUp").textContent = formatBytes(traffic.up_total || 0);
  const stream = $("trafficStreamState");
  stream.textContent = connected && traffic.stream_ok ? "实时" : connected ? "等待数据" : "待命";
  stream.classList.toggle("live", Boolean(connected && traffic.stream_ok));
  const history = Array.isArray(traffic.history) ? traffic.history.slice(-60) : [];
  const chartLine = $("chartLine");
  const chartArea = $("chartArea");
  let upLine = $("chartUpLine");
  if (!upLine) {
    upLine = document.createElementNS(SVG_NS, "path");
    upLine.id = "chartUpLine";
    upLine.setAttribute("fill", "none");
    upLine.setAttribute("stroke", "rgba(102,240,183,.45)");
    upLine.setAttribute("stroke-width", "1.3");
    upLine.setAttribute("stroke-linecap", "round");
    $("trafficChart").insertBefore(upLine, chartLine);
  }
  if (!history.length) {
    chartLine.setAttribute("d", "");
    chartArea.setAttribute("d", "");
    upLine.setAttribute("d", "");
    $("chartEmpty").classList.remove("hidden");
    return;
  }
  $("chartEmpty").classList.add("hidden");
  const valuesDown = history.map((item) => Math.max(0, Number(item.down) || 0));
  const valuesUp = history.map((item) => Math.max(0, Number(item.up) || 0));
  const maxValue = Math.max(1, ...valuesDown, ...valuesUp);
  const width = 620;
  const height = 100;
  const top = 6;
  const bottom = 94;
  const span = Math.max(1, valuesDown.length - 1);
  const pathFor = (values) => values.map((value, index) => {
    const x = (index / span) * width;
    const y = bottom - (value / maxValue) * (bottom - top);
    return { x, y };
  });
  const makeLine = (points) => {
    if (!points.length) return "";
    if (points.length === 1) return "M0 " + points[0].y.toFixed(2) + " L" + width + " " + points[0].y.toFixed(2);
    let d = "M" + points[0].x.toFixed(2) + " " + points[0].y.toFixed(2);
    for (let i = 1; i < points.length; i += 1) {
      const previous = points[i - 1];
      const current = points[i];
      const midX = (previous.x + current.x) / 2;
      d += " C" + midX.toFixed(2) + " " + previous.y.toFixed(2) + ", " + midX.toFixed(2) + " " + current.y.toFixed(2) + ", " + current.x.toFixed(2) + " " + current.y.toFixed(2);
    }
    return d;
  };
  const downPoints = pathFor(valuesDown);
  const upPoints = pathFor(valuesUp);
  const line = makeLine(downPoints);
  chartLine.setAttribute("d", line);
  upLine.setAttribute("d", makeLine(upPoints));
  const area = line + " L" + width + " " + height + " L0 " + height + " Z";
  chartArea.setAttribute("d", line ? area : "");
}

function renderHomeNodes() {
  const container = $("homeNodes");
  if (!container || !S.data) return;
  container.replaceChildren();
  const nodes = [...S.data.nodes];
  nodes.sort((a, b) => {
    if (a.id === S.data.selected_id) return -1;
    if (b.id === S.data.selected_id) return 1;
    const da = a.delay === null || a.delay === undefined || a.delay < 0 ? 999999 : a.delay;
    const db = b.delay === null || b.delay === undefined || b.delay < 0 ? 999999 : b.delay;
    return da - db;
  });
  if (!nodes.length) {
    container.appendChild(el("div", "home-nodes-empty", "还没有节点。导入订阅后，常用线路会显示在这里。"));
    return;
  }
  nodes.slice(0, 4).forEach((node) => {
    const item = el("button", "home-node" + (node.id === S.data.selected_id ? " selected" : ""));
    item.type = "button";
    item.dataset.nodeId = node.id;
    const orb = el("div", "node-orb protocol-" + nodeTypeClass(node.type), shortProtocol(node));
    const copy = el("div", "home-node-copy");
    copy.appendChild(el("strong", "", node.name));
    copy.appendChild(el("small", "", (node.server || "") + " · " + protocolName(node)));
    const info = latencyInfo(node);
    const delay = el("span", "home-node-delay " + info.cls, info.cls === "testing" ? "…" : info.label.replace(" ms", "ms").replace("连接超时", "超时"));
    item.append(orb, copy, delay);
    item.addEventListener("click", () => selectNode(node.id));
    container.appendChild(item);
  });
}

function shortProtocol(node) {
  const type = node.type || "";
  if (type === "hysteria2") return "H2";
  if (type === "shadowsocks") return "SS";
  return (PROTOCOL_NAMES[type] || type).slice(0, 4).toUpperCase();
}

function renderNodes() {
  if (!S.data) return;
  const grid = $("nodeGrid");
  const empty = $("nodesEmpty");
  if (!grid) return;
  grid.replaceChildren();
  const nodes = S.data.nodes || [];
  const query = S.search.trim().toLowerCase();
  const filtered = nodes.filter((node) => {
    if (S.filter !== "all") {
      if (S.filter === "other" ? META_TYPES.includes(node.type) : node.type !== S.filter) return false;
    }
    if (!query) return true;
    return [node.name, node.server, node.protocol, node.type].some((value) => String(value || "").toLowerCase().includes(query));
  });
  grid.classList.toggle("hidden", filtered.length === 0);
  empty.classList.toggle("hidden", filtered.length > 0);
  $("nodesCount").textContent = String(nodes.length);
  if (!filtered.length) {
    const isFiltering = Boolean(query) || S.filter !== "all";
    empty.querySelector("h3").textContent = isFiltering ? "没有找到匹配的节点" : "这里还没有节点";
    empty.querySelector("p").textContent = isFiltering ? "换个关键词或筛选条件试试。" : "粘贴分享链接、订阅地址，或从 v2rayN 一键导入。";
    const importBtn = $("emptyImportBtn");
    if (importBtn) importBtn.classList.toggle("hidden", isFiltering);
    return;
  }
  filtered.forEach((node) => grid.appendChild(createNodeCard(node)));
}

function createNodeCard(node) {
  const card = el("article", "node-card" + (node.id === S.data.selected_id ? " selected" : ""));
  card.dataset.nodeId = node.id;
  card.title = "点击切换到该节点";
  const top = el("div", "node-top");
  const badge = el("span", "protocol-badge " + nodeTypeClass(node.type), protocolName(node));
  top.appendChild(badge);
  if (node.id === S.data.selected_id) {
    const mark = el("span", "selected-mark");
    mark.appendChild(icon("check"));
    mark.appendChild(el("span", "", "当前"));
    top.appendChild(mark);
  }
  const spacer = el("span", "node-top-spacer");
  top.appendChild(spacer);
  const tools = el("div", "node-tools");
  const testBtn = el("button", "mini-icon-button", "");
  testBtn.type = "button";
  testBtn.title = "测试延迟";
  testBtn.appendChild(icon("bolt"));
  testBtn.dataset.action = "test";
  testBtn.dataset.id = node.id;
  const renameBtn = el("button", "mini-icon-button", "");
  renameBtn.type = "button";
  renameBtn.title = "重命名";
  renameBtn.appendChild(icon("edit"));
  renameBtn.dataset.action = "rename";
  renameBtn.dataset.id = node.id;
  const deleteBtn = el("button", "mini-icon-button danger", "");
  deleteBtn.type = "button";
  deleteBtn.title = "删除节点";
  deleteBtn.appendChild(icon("trash"));
  deleteBtn.dataset.action = "delete";
  deleteBtn.dataset.id = node.id;
  tools.append(testBtn, renameBtn, deleteBtn);
  top.appendChild(tools);
  card.appendChild(top);

  card.appendChild(el("h3", "", node.name));
  card.appendChild(el("p", "node-server", (node.server || "") + ":" + node.port));

  const meta = el("div", "node-meta");
  const tags = el("div", "node-tags");
  const network = String(node.network || "tcp").toUpperCase();
  if (network && network !== "TCP") tags.appendChild(el("span", "meta-tag", network));
  if (node.tls) tags.appendChild(el("span", "meta-tag", "TLS"));
  if (node.source === "v2rayn") tags.appendChild(el("span", "meta-tag", "v2rayN"));
  if (node.source && node.source.startsWith("subscription:")) tags.appendChild(el("span", "meta-tag", "订阅"));
  const info = latencyInfo(node);
  const latency = el("span", "latency " + info.cls, info.label);
  meta.append(tags, latency);
  card.appendChild(meta);

  card.addEventListener("click", (event) => {
    if (event.target.closest("[data-action]")) return;
    selectNode(node.id);
  });
  card.addEventListener("dblclick", () => selectNode(node.id));
  return card;
}

/* ---------- subscriptions ---------- */
function renderSubscriptions() {
  if (!S.data) return;
  const list = $("subscriptionList");
  const empty = $("subsEmpty");
  if (!list) return;
  list.replaceChildren();
  const subscriptions = S.data.subscriptions || [];
  $("subCountBadge").textContent = subscriptions.length + " 个来源";
  empty.classList.toggle("hidden", subscriptions.length > 0);
  $("subUrl").placeholder = subscriptions.length ? "https://provider.example/sub" : "https://provider.example/sub";
  const pathText = $("v2raynPathText");
  if (pathText) pathText.textContent = (S.data.settings && S.data.settings.v2rayn_path) || "未配置";
  subscriptions.forEach((sub) => {
    const item = el("div", "subscription-item");
    const subIcon = el("div", "sub-icon");
    subIcon.appendChild(icon("cloud"));
    const copy = el("div", "sub-copy");
    copy.appendChild(el("strong", "", sub.name || "未命名订阅"));
    let host = "订阅地址";
    try {
      host = new URL(sub.url).host;
    } catch (error) {
      host = "本地订阅";
    }
    copy.appendChild(el("small", "", host + "  ·  " + (sub.node_count || 0) + " 个节点  ·  " + formatDate(sub.updated_at)));
    const actions = el("div", "sub-actions");
    const updateBtn = el("button", "mini-icon-button", "");
    updateBtn.type = "button";
    updateBtn.title = "更新订阅";
    updateBtn.appendChild(icon("refresh"));
    updateBtn.dataset.subAction = "update";
    updateBtn.dataset.id = sub.id;
    const deleteBtn = el("button", "mini-icon-button danger", "");
    deleteBtn.type = "button";
    deleteBtn.title = "删除订阅";
    deleteBtn.appendChild(icon("trash"));
    deleteBtn.dataset.subAction = "delete";
    deleteBtn.dataset.id = sub.id;
    actions.append(updateBtn, deleteBtn);
    item.append(subIcon, copy, actions);
    list.appendChild(item);
  });
}

/* ---------- settings ---------- */
function renderSettings() {
  if (!S.data) return;
  const settings = S.data.settings || {};
  $("settingsPort").value = String(settings.local_port || 2080);
  document.querySelectorAll("[data-setting-toggle]").forEach((toggle) => {
    const key = toggle.dataset.settingToggle;
    toggle.setAttribute("aria-checked", String(activeSetting(key)));
  });
  const tunWarning = $("tunAdminWarning");
  if (settings.tun_mode && !S.data.is_admin) tunWarning.classList.remove("hidden");
  else tunWarning.classList.add("hidden");
  const admin = Boolean(S.data.is_admin);
  $("diagAdmin").textContent = admin ? "管理员" : "标准用户";
  $("adminSettingHint").textContent = admin ? "当前已具备管理员权限" : "TUN 模式需要管理员权限";
  const relBtn = $("relaunchAdminBtn");
  relBtn.textContent = admin ? "已是管理员" : "以管理员身份重启";
  relBtn.disabled = admin;
  $("diagCoreVersion").textContent = (S.status && S.status.core_version) || "sing-box";
  $("settingsV2raynHint").textContent = S.data.v2rayn_available ? "已找到 v2rayN 数据，可直接迁移" : "未找到 v2rayN 数据";
  const dataPath = $("localDataPath");
  if (dataPath) {
    const dir = S.data.data_dir || "未知";
    dataPath.textContent = dir + "\\store.json";
    dataPath.title = dir;
  }
  const testUrlBtn = $("editTestUrlBtn");
  if (testUrlBtn) testUrlBtn.title = settings.test_url || "";
}

/* ---------- status polling ---------- */
async function pollStatus() {
  if (S.pollBusy) return;
  S.pollBusy = true;
  try {
    const payload = await api("/api/status");
    const previous = S.status;
    S.status = payload.status;
    renderStatus(S.status);
    const previousState = previous ? previous.state : S.knownState;
    if (S.status.state !== previousState) {
      S.knownState = S.status.state;
      if (S.status.state === "connected" && !S.busy) {
        toast("已连接", "success");
        refreshStateQuietly();
      } else if (S.status.state === "disconnected" && previous && previous.state === "connected") {
        toast("连接已断开", "warning");
        refreshStateQuietly();
      }
    }
    if (S.status.error && S.status.error !== S.lastError) {
      S.lastError = S.status.error;
      toast(firstLine(S.status.error), "error", 5000);
    }
    if (!S.status.error && S.lastError) S.lastError = "";
  } catch (error) {
    if (!S.status || S.status.state !== "error") {
      toast(firstLine(error.message), "error", 5000);
    }
    if (S.status) {
      S.status.state = "error";
      S.status.error = error.message;
      renderStatus(S.status);
    }
  } finally {
    S.pollBusy = false;
    window.clearTimeout(S.pollTimer);
    S.pollTimer = window.setTimeout(pollStatus, 1000);
  }
}

async function refreshStateQuietly() {
  try {
    const payload = await api("/api/state");
    applyState(payload);
  } catch (error) {
    /* polling will surface the issue */
  }
}

async function loadState(showErrors = true) {
  try {
    const payload = await api("/api/state");
    applyState(payload);
    return payload;
  } catch (error) {
    if (showErrors) toast(firstLine(error.message), "error", 6000);
    throw error;
  }
}

/* ---------- connect / node actions ---------- */
async function toggleConnection() {
  if (S.busy) return;
  if (!S.data || !S.data.nodes.length) {
    openImportModal();
    return;
  }
  const connected = S.status && S.status.connected;
  S.busy = true;
  renderStatus(S.status);
  try {
    const payload = await api(connected ? "/api/disconnect" : "/api/connect", {});
    S.status = payload.status;
    S.busy = false;
    renderStatus(S.status);
    toast(connected ? "已断开连接" : "连接成功", connected ? "info" : "success");
    await refreshStateQuietly();
  } catch (error) {
    S.busy = false;
    toast(firstLine(error.message), "error", 6000);
    await refreshStateQuietly();
    try {
      const payload = await api("/api/status");
      S.status = payload.status;
      renderStatus(S.status);
    } catch (ignored) {}
  }
}

async function selectNode(id) {
  if (!S.data || !id) return;
  if (S.busy) return;
  const node = nodeById(id);
  if (!node) return;
  if (S.data.selected_id === id) {
    showPage("home");
    toast("当前已选择 " + node.name, "info");
    return;
  }
  S.busy = true;
  renderStatus(S.status);
  try {
    const payload = await api("/api/node/select", { id });
    if (payload.state) applyState(payload.state);
    S.busy = false;
    toast("已切换到 " + node.name, "success");
    showPage("home");
  } catch (error) {
    S.busy = false;
    toast(firstLine(error.message), "error", 5000);
    await refreshStateQuietly();
  }
}

function openRenameModal(node) {
  const input = el("input", "text-field");
  input.type = "text";
  input.value = node.name;
  input.maxLength = 80;
  const body = el("div");
  body.appendChild(el("p", "", "给这条线路起一个更容易识别的名字。"));
  body.appendChild(input);
  const modal = openModal({
    kicker: "NODE",
    title: "重命名节点",
    body,
    actions: [
      { label: "取消", className: "btn ghost", onClick: () => modal.close() },
      {
        label: "保存",
        className: "btn primary",
        onClick: async (btn) => {
          const name = input.value.trim();
          if (!name) {
            toast("名称不能为空", "warning");
            return;
          }
          setButtonBusy(btn, true, "保存中…");
          try {
            const payload = await api("/api/node/rename", { id: node.id, name });
            if (payload.state) applyState(payload.state);
            modal.close();
            toast("名称已更新", "success");
          } catch (error) {
            setButtonBusy(btn, false);
            toast(firstLine(error.message), "error");
          }
        },
      },
    ],
  });
  window.setTimeout(() => { input.focus(); input.select(); }, 50);
}

function openDeleteNodeModal(node) {
  const body = el("div");
  body.appendChild(el("p", "", "确定要从本地删除「" + node.name + "」吗？"));
  body.appendChild(el("div", "danger-note", "删除只会移除轻连中的节点记录，不会影响 v2rayN 或订阅服务端的数据。"));
  const modal = openModal({
    kicker: "NODE",
    title: "删除节点",
    body,
    actions: [
      { label: "取消", className: "btn ghost", onClick: () => modal.close() },
      {
        label: "删除",
        className: "btn danger",
        onClick: async (btn) => {
          setButtonBusy(btn, true, "删除中…");
          try {
            const payload = await api("/api/node/delete", { id: node.id });
            if (payload.state) applyState(payload.state);
            modal.close();
            toast("节点已删除", "success");
          } catch (error) {
            setButtonBusy(btn, false);
            toast(firstLine(error.message), "error");
          }
        },
      },
    ],
  });
}

async function testNodes(ids) {
  if (!ids || !ids.length || S.testingAll || S.testing.size) return;
  S.testingAll = ids.length > 1;
  ids.forEach((id) => S.testing.add(id));
  renderNodes();
  renderHomeNodes();
  renderStatus(S.status);
  try {
    const payload = await api("/api/nodes/test", { ids });
    if (payload.state) applyState(payload.state);
    const results = payload.results || [];
    const ok = results.filter((item) => item.delay !== null && item.delay >= 0);
    if (ok.length) {
      const avg = Math.round(ok.reduce((sum, item) => sum + item.delay, 0) / ok.length);
      toast("测速完成：" + ok.length + "/" + results.length + " 个节点可用，平均 " + avg + " ms", "success", 5000);
    } else {
      toast("测速完成，但节点暂时都不可用", "warning", 5000);
    }
  } catch (error) {
    toast(firstLine(error.message), "error", 6000);
  } finally {
    ids.forEach((id) => S.testing.delete(id));
    S.testingAll = false;
    renderNodes();
    renderHomeNodes();
    renderStatus(S.status);
  }
}

/* ---------- import ---------- */
function openImportModal() {
  const textarea = el("textarea");
  textarea.placeholder = "每行一个分享链接，例如：\n\nvless://...\ntrojan://...\nss://...\n\n也可以直接粘贴 base64 编码的节点列表。";
  textarea.spellcheck = false;
  const body = el("div");
  body.appendChild(el("p", "", "支持 VLESS、VMess、Trojan、Hysteria2、TUIC、Shadowsocks、SOCKS 和 HTTP 节点。"));
  body.appendChild(textarea);
  const modal = openModal({
    kicker: "IMPORT",
    title: "粘贴节点链接",
    body,
    wide: true,
    actions: [
      { label: "取消", className: "btn ghost", onClick: () => modal.close() },
      {
        label: "导入",
        className: "btn primary",
        onClick: async (btn) => {
          const text = textarea.value.trim();
          if (!text) {
            toast("请先粘贴节点链接", "warning");
            return;
          }
          setButtonBusy(btn, true, "正在导入…");
          try {
            const payload = await api("/api/nodes/import", { text });
            if (payload.state) applyState(payload.state);
            modal.close();
            toast("已导入 " + payload.count + " 个节点", "success");
            if (S.page !== "nodes") showPage("nodes");
          } catch (error) {
            setButtonBusy(btn, false);
            toast(firstLine(error.message), "error", 6000);
          }
        },
      },
    ],
  });
  window.setTimeout(() => textarea.focus(), 50);
}

async function importFromClipboard() {
  const btn = $("clipboardImportBtn");
  setButtonBusy(btn, true, "读取中…");
  try {
    const payload = await api("/api/clipboard/import", {});
    if (payload.state) applyState(payload.state);
    let message;
    if (payload.mode === "subscription") {
      message = (payload.updated ? "已更新剪贴板中的订阅" : "已从剪贴板添加订阅") + "，共 " + payload.count + " 个节点";
    } else if (payload.count > 0) {
      message = "已从剪贴板导入 " + payload.count + " 个节点";
      if (payload.skipped) message += "，跳过 " + payload.skipped + " 个重复节点";
    } else {
      message = "剪贴板中的 " + payload.skipped + " 个节点都已经存在";
    }
    toast(message, payload.count > 0 || payload.mode === "subscription" ? "success" : "info", 5500);
    if (payload.count > 0 && S.page !== "nodes") showPage("nodes");
  } catch (error) {
    toast(firstLine(error.message), "error", 6000);
  } finally {
    setButtonBusy(btn, false);
  }
}

async function exportToClipboard() {
  if (!S.data || !S.data.nodes.length) {
    toast("还没有可导出的节点", "warning");
    return;
  }
  const btn = $("clipboardExportBtn");
  setButtonBusy(btn, true, "复制中…");
  try {
    const payload = await api("/api/clipboard/export", {});
    let message = "已复制 " + payload.count + " 个节点到剪贴板";
    if (payload.skipped) message += "，跳过 " + payload.skipped + " 个节点";
    toast(message, "success", 5500);
  } catch (error) {
    toast(firstLine(error.message), "error", 6000);
  } finally {
    setButtonBusy(btn, false);
  }
}

async function addSubscription(event) {
  event.preventDefault();
  const name = $("subName").value.trim();
  const url = $("subUrl").value.trim();
  if (!url) {
    toast("请输入订阅地址", "warning");
    return;
  }
  const btn = $("addSubBtn");
  setButtonBusy(btn, true, "正在拉取订阅…");
  try {
    const payload = await api("/api/subscriptions/add", { name, url });
    if (payload.state) applyState(payload.state);
    $("subName").value = "";
    $("subUrl").value = "";
    toast("订阅已添加，共 " + payload.count + " 个节点", "success", 5000);
  } catch (error) {
    toast(firstLine(error.message), "error", 6000);
  } finally {
    setButtonBusy(btn, false);
  }
}

async function updateSubscription(id) {
  const btn = document.querySelector('[data-sub-action="update"][data-id="' + id + '"]');
  if (btn) btn.classList.add("rotating");
  try {
    const payload = await api("/api/subscriptions/update", { id });
    if (payload.state) applyState(payload.state);
    toast("订阅已更新，共 " + payload.count + " 个节点", "success", 4500);
  } catch (error) {
    toast(firstLine(error.message), "error", 6000);
  } finally {
    if (btn) btn.classList.remove("rotating");
  }
}

function deleteSubscription(id) {
  const sub = (S.data.subscriptions || []).find((item) => item.id === id);
  if (!sub) return;
  const body = el("div");
  body.appendChild(el("p", "", "确定删除订阅「" + (sub.name || "未命名") + "」吗？"));
  body.appendChild(el("div", "danger-note", "这个订阅同步过来的节点也会从轻连中移除。"));
  const modal = openModal({
    kicker: "SUBSCRIPTION",
    title: "删除订阅",
    body,
    actions: [
      { label: "取消", className: "btn ghost", onClick: () => modal.close() },
      {
        label: "删除",
        className: "btn danger",
        onClick: async (btn) => {
          setButtonBusy(btn, true, "删除中…");
          try {
            const payload = await api("/api/subscriptions/delete", { id });
            if (payload.state) applyState(payload.state);
            modal.close();
            toast("订阅已删除", "success");
          } catch (error) {
            setButtonBusy(btn, false);
            toast(firstLine(error.message), "error");
          }
        },
      },
    ],
  });
}

async function importV2rayN() {
  const btn = $("importV2raynBtn");
  setButtonBusy(btn, true, "正在读取 v2rayN…");
  try {
    const payload = await api("/api/import/v2rayn", { path: S.data.settings.v2rayn_path || "" });
    if (payload.state) applyState(payload.state);
    toast("已从 v2rayN 同步 " + payload.count + " 个节点", "success", 4500);
  } catch (error) {
    toast(firstLine(error.message), "error", 6000);
  } finally {
    setButtonBusy(btn, false);
  }
}

/* ---------- settings actions ---------- */
async function saveSettings(values, successMessage) {
  try {
    const payload = await api("/api/settings", values);
    if (payload.state) applyState(payload.state);
    if (successMessage) toast(successMessage, "success");
    return payload;
  } catch (error) {
    toast(firstLine(error.message), "error", 5000);
    await refreshStateQuietly();
    return null;
  }
}

async function changeMode(mode) {
  if (!S.data || S.data.settings.mode === mode) return;
  const old = S.data.settings.mode;
  S.data.settings.mode = mode;
  renderStatus(S.status);
  const payload = await saveSettings({ mode }, mode === "rule" ? "已启用规则分流" : mode === "global" ? "已切换到全局代理" : "已切换到直连");
  if (!payload && S.data) S.data.settings.mode = old;
}

async function toggleSetting(key) {
  if (!S.data) return;
  const next = !activeSetting(key);
  if (key === "tun_mode" && next && !S.data.is_admin) {
    openTunAdminModal();
    return;
  }
  const labelMap = {
    system_proxy: next ? "系统代理已启用" : "系统代理已关闭",
    auto_connect: next ? "下次启动会自动连接" : "已关闭自动连接",
    close_to_tray: next ? "关闭窗口后将留在托盘" : "关闭窗口将退出轻连",
    autostart: next ? "已设置开机启动" : "已关闭开机启动",
    tun_mode: next ? "TUN 模式已启用" : "TUN 模式已关闭",
  };
  await saveSettings({ [key]: next }, labelMap[key]);
}

function openTunAdminModal() {
  const body = el("div");
  body.appendChild(el("p", "", "TUN 模式会创建虚拟网卡并接管全部系统流量，需要管理员权限。"));
  body.appendChild(el("div", "danger-note", "点击「保存并重启」后，Windows 会弹出权限确认。轻连会短暂断开并以管理员身份重新打开。"));
  const modal = openModal({
    kicker: "TUN MODE",
    title: "需要管理员权限",
    body,
    actions: [
      { label: "取消", className: "btn ghost", onClick: () => modal.close() },
      {
        label: "保存并重启",
        className: "btn primary",
        onClick: async (btn) => {
          setButtonBusy(btn, true, "正在准备…");
          const payload = await saveSettings({ tun_mode: true });
          if (!payload) {
            setButtonBusy(btn, false);
            return;
          }
          modal.close();
          await requestElevation(true);
        },
      },
    ],
  });
}

async function requestElevation(connectAfterStart = false) {
  try {
    showRestartingScreen();
    await api("/api/relaunch-admin", { connect_after_start: Boolean(connectAfterStart) });
  } catch (error) {
    hideRestartingScreen();
    toast(firstLine(error.message), "error", 6000);
  }
}

function showRestartingScreen() {
  const boot = $("bootScreen");
  boot.classList.remove("done");
  boot.querySelector(".boot-screen p").textContent = "正在以管理员权限重新打开轻连…";
  const spinner = boot.querySelector(".boot-spinner");
  if (spinner) spinner.style.display = "block";
}

function hideRestartingScreen() {
  const boot = $("bootScreen");
  boot.classList.add("done");
}

function openTestUrlModal() {
  const input = el("input", "text-field");
  input.type = "url";
  input.value = (S.data.settings && S.data.settings.test_url) || "https://www.gstatic.com/generate_204";
  const body = el("div");
  body.appendChild(el("p", "", "测速时会请求这个地址，使用返回 204 的地址可以获得更准确的结果。"));
  body.appendChild(input);
  const modal = openModal({
    kicker: "DIAGNOSTICS",
    title: "修改测试地址",
    body,
    actions: [
      { label: "取消", className: "btn ghost", onClick: () => modal.close() },
      {
        label: "保存",
        className: "btn primary",
        onClick: async (btn) => {
          const value = input.value.trim();
          if (!/^https?:\/\//i.test(value)) {
            toast("请输入 http(s) 地址", "warning");
            return;
          }
          setButtonBusy(btn, true, "保存中…");
          const payload = await saveSettings({ test_url: value });
          if (payload) {
            modal.close();
            toast("测试地址已更新", "success");
          } else setButtonBusy(btn, false);
        },
      },
    ],
  });
  window.setTimeout(() => input.focus(), 50);
}

function openV2rayNPathModal() {
  const input = el("input", "text-field");
  input.type = "text";
  input.value = (S.data.settings && S.data.settings.v2rayn_path) || "";
  input.placeholder = "例如：C:\\Tools\\v2rayN-windows-64";
  const body = el("div");
  body.appendChild(el("p", "", "填写包含 guiConfigs 和 bin 文件夹的 v2rayN 目录。"));
  body.appendChild(input);
  const modal = openModal({
    kicker: "MIGRATION",
    title: "修改 v2rayN 目录",
    body,
    actions: [
      { label: "取消", className: "btn ghost", onClick: () => modal.close() },
      {
        label: "保存",
        className: "btn primary",
        onClick: async (btn) => {
          const value = input.value.trim();
          if (!value) {
            toast("路径不能为空", "warning");
            return;
          }
          setButtonBusy(btn, true, "保存中…");
          const payload = await saveSettings({ v2rayn_path: value }, "v2rayN 目录已更新");
          if (payload) modal.close();
          else setButtonBusy(btn, false);
        },
      },
    ],
  });
  window.setTimeout(() => input.focus(), 50);
}

function openLogsModal() {
  const pre = el("pre", "log-box", "正在读取日志…");
  const body = el("div");
  body.appendChild(pre);
  const modal = openModal({
    kicker: "DIAGNOSTICS",
    title: "运行日志",
    body,
    wide: true,
    actions: [
      { label: "关闭", className: "btn ghost", onClick: () => modal.close() },
      {
        label: "刷新",
        className: "btn secondary",
        onClick: async (btn) => {
          setButtonBusy(btn, true, "读取中…");
          try {
            const payload = await api("/api/logs?lines=400");
            pre.textContent = payload.logs || "日志为空";
          } catch (error) {
            pre.textContent = "读取日志失败：" + error.message;
          } finally {
            setButtonBusy(btn, false);
          }
        },
      },
    ],
  });
  api("/api/logs?lines=400").then((payload) => {
    pre.textContent = payload.logs || "日志为空";
    pre.scrollTop = pre.scrollHeight;
  }).catch((error) => {
    pre.textContent = "读取日志失败：" + error.message;
  });
}

function openHelpModal() {
  const body = el("div");
  const p1 = el("p", "", "轻连是一个轻量的本地代理客户端：一个按钮连接，自动设置系统代理；规则模式让国内网站直连、国外网站走代理。");
  const p2 = el("p", "", "节点密码只保存在本机 data/store.json 中，不会上传到任何服务器。轻连使用的内核是 sing-box。");
  const p3 = el("p", "", "如果 TUN 模式无法启动，请以管理员身份重新打开轻连。遇到连接问题，可以在「偏好设置 → 诊断 → 查看运行日志」中查看原因。");
  body.append(p1, p2, p3);
  const modal = openModal({
    kicker: "ABOUT QINGLIAN",
    title: "关于轻连",
    body,
    actions: [
      { label: "知道了", className: "btn primary", onClick: () => modal.close() },
    ],
  });
}

function openDataInfoModal() {
  const body = el("div");
  const dir = (S.data && S.data.data_dir) || "未知";
  body.appendChild(el("p", "", "轻连会把节点、订阅地址和偏好设置保存在当前 Windows 用户的数据目录："));
  const pathBox = el("div", "path-box");
  pathBox.appendChild(el("span", "", dir));
  body.appendChild(pathBox);
  body.appendChild(el("div", "danger-note", "这个目录包含节点认证信息。请不要上传或分享 data 目录，也不要把应用目录放进公开的网盘。"));
  openModal({
    kicker: "PRIVACY",
    title: "本地数据说明",
    body,
    actions: [
      { label: "关闭", className: "btn ghost", onClick: () => closeModal() },
      {
        label: "打开数据目录",
        className: "btn secondary",
        onClick: async (btn) => {
          setButtonBusy(btn, true, "打开中…");
          try {
            await api("/api/data/open", {});
          } catch (error) {
            toast(firstLine(error.message), "error");
          } finally {
            setButtonBusy(btn, false);
          }
        },
      },
    ],
  });
}

/* ---------- modal ---------- */
let previousFocus = null;

function openModal({ kicker = "QINGLIAN", title, body, actions = [], wide = false }) {
  previousFocus = document.activeElement;
  $("modalKicker").textContent = kicker;
  $("modalTitle").textContent = title || "";
  const modalBody = $("modalBody");
  modalBody.replaceChildren();
  if (body instanceof Node) modalBody.appendChild(body);
  else if (typeof body === "string") modalBody.textContent = body;
  const modalFoot = $("modalFoot");
  modalFoot.replaceChildren();
  const modal = {
    close: closeModal,
  };
  actions.forEach((action) => {
    const btn = button(action.label, action.className || "btn ghost", async () => {
      if (action.onClick) await action.onClick(btn);
    });
    modalFoot.appendChild(btn);
  });
  $("modalCard").classList.toggle("wide", Boolean(wide));
  $("modalLayer").classList.remove("hidden");
  window.setTimeout(() => {
    const firstInput = modalBody.querySelector("input, textarea");
    if (firstInput) firstInput.focus();
    else modalFoot.querySelector("button")?.focus();
  }, 50);
  return modal;
}

function closeModal() {
  $("modalLayer").classList.add("hidden");
  $("modalBody").replaceChildren();
  $("modalFoot").replaceChildren();
  if (previousFocus && typeof previousFocus.focus === "function") {
    try { previousFocus.focus(); } catch (error) {}
  }
}

/* ---------- event wiring ---------- */
function setupEvents() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => showPage(item.dataset.page));
  });
  document.querySelectorAll("[data-page-jump]").forEach((item) => {
    item.addEventListener("click", () => showPage(item.dataset.pageJump));
  });
  $("topSettingsBtn").addEventListener("click", () => showPage("settings"));
  $("topImportBtn").addEventListener("click", openImportModal);
  $("importNodeBtn").addEventListener("click", openImportModal);
  $("emptyImportBtn").addEventListener("click", openImportModal);
  $("manualImportBtn").addEventListener("click", openImportModal);
  $("clipboardImportBtn").addEventListener("click", importFromClipboard);
  $("clipboardExportBtn").addEventListener("click", exportToClipboard);
  $("chooseNodeBtn").addEventListener("click", () => showPage("nodes"));
  $("connectBtn").addEventListener("click", toggleConnection);
  $("testCurrentBtn").addEventListener("click", () => {
    const node = selectedNode();
    if (node) testNodes([node.id]);
  });
  $("testAllBtn").addEventListener("click", () => {
    if (!S.data || !S.data.nodes.length) {
      toast("还没有可测试的节点", "warning");
      return;
    }
    testNodes(S.data.nodes.map((node) => node.id));
  });
  $("homeNodes").addEventListener("click", (event) => {
    const item = event.target.closest(".home-node");
    if (item && item.dataset.nodeId) selectNode(item.dataset.nodeId);
  });
  $("nodeGrid").addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]");
    if (!action) return;
    const node = nodeById(action.dataset.id);
    if (!node) return;
    if (action.dataset.action === "test") testNodes([node.id]);
    else if (action.dataset.action === "rename") openRenameModal(node);
    else if (action.dataset.action === "delete") openDeleteNodeModal(node);
  });
  $("nodeSearch").addEventListener("input", (event) => {
    S.search = event.target.value;
    renderNodes();
  });
  $("filterStrip").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-filter]");
    if (!chip) return;
    S.filter = chip.dataset.filter;
    document.querySelectorAll(".filter-chip").forEach((item) => item.classList.toggle("active", item === chip));
    renderNodes();
  });
  $("modeSegmented").addEventListener("click", (event) => {
    const item = event.target.closest("[data-mode]");
    if (item) changeMode(item.dataset.mode);
  });
  $("homeSystemProxyToggle").addEventListener("click", () => toggleSetting("system_proxy"));
  $("homeTunToggle").addEventListener("click", () => toggleSetting("tun_mode"));
  document.querySelectorAll("[data-setting-toggle]").forEach((toggle) => {
    toggle.addEventListener("click", () => toggleSetting(toggle.dataset.settingToggle));
  });
  $("addSubForm").addEventListener("submit", addSubscription);
  $("subscriptionList").addEventListener("click", (event) => {
    const action = event.target.closest("[data-sub-action]");
    if (!action) return;
    if (action.dataset.subAction === "update") updateSubscription(action.dataset.id);
    else if (action.dataset.subAction === "delete") deleteSubscription(action.dataset.id);
  });
  $("importV2raynBtn").addEventListener("click", importV2rayN);
  $("changeV2raynPath").addEventListener("click", openV2rayNPathModal);
  $("settingsChangeV2rayn").addEventListener("click", openV2rayNPathModal);
  $("savePortBtn").addEventListener("click", async () => {
    const value = Number($("settingsPort").value);
    if (!Number.isInteger(value) || value < 1024 || value > 65535) {
      toast("端口需要在 1024 到 65535 之间", "warning");
      return;
    }
    const btn = $("savePortBtn");
    setButtonBusy(btn, true, "保存中…");
    const payload = await saveSettings({ local_port: value }, "本地端口已更新");
    if (payload && S.status && S.status.connected) toast("端口变更后代理已自动重连", "info", 4500);
    setButtonBusy(btn, false);
  });
  $("editTestUrlBtn").addEventListener("click", openTestUrlModal);
  $("settingsReloadBtn").addEventListener("click", async () => {
    const btn = $("settingsReloadBtn");
    setButtonBusy(btn, true, "读取中…");
    try {
      await loadState(true);
      toast("配置已重新读取", "success");
    } finally {
      setButtonBusy(btn, false);
    }
  });
  $("relaunchAdminBtn").addEventListener("click", async () => {
    if (!S.data || S.data.is_admin) return;
    const body = el("div");
    body.appendChild(el("p", "", "轻连会关闭当前窗口并以管理员身份重新打开。"));
    body.appendChild(el("div", "danger-note", "当前连接会短暂断开。Windows 会弹出权限确认窗口。"));
    const modal = openModal({
      kicker: "ELEVATION",
      title: "以管理员身份重启",
      body,
      actions: [
        { label: "取消", className: "btn ghost", onClick: () => modal.close() },
        { label: "继续", className: "btn primary", onClick: async () => {
          const keepConnected = Boolean(S.status && S.status.connected);
          modal.close();
          await requestElevation(keepConnected);
        } },
      ],
    });
  });
  $("viewLogsBtn").addEventListener("click", openLogsModal);
  $("clearLogsBtn").addEventListener("click", async () => {
    try {
      await api("/api/logs/clear", {});
      toast("运行日志已清空", "success");
    } catch (error) {
      toast(firstLine(error.message), "error");
    }
  });
  $("openDataInfoBtn").addEventListener("click", openDataInfoModal);
  $("helpBtn").addEventListener("click", openHelpModal);
  $("quitAppBtn").addEventListener("click", () => {
    const body = el("div");
    body.appendChild(el("p", "", "确定要退出轻连吗？当前代理连接会被断开。"));
    const modal = openModal({
      kicker: "APPLICATION",
      title: "退出轻连",
      body,
      actions: [
        { label: "取消", className: "btn ghost", onClick: () => modal.close() },
        { label: "退出", className: "btn danger", onClick: async (btn) => {
          setButtonBusy(btn, true, "正在退出…");
          try {
            await api("/api/quit", {});
            modal.close();
            window.setTimeout(() => window.close(), 350);
          } catch (error) {
            setButtonBusy(btn, false);
            toast(firstLine(error.message), "error");
          }
        } },
      ],
    });
  });
  $("modalCloseBtn").addEventListener("click", closeModal);
  $("modalLayer").addEventListener("click", (event) => {
    if (event.target.closest("[data-modal-close]")) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("modalLayer").classList.contains("hidden")) closeModal();
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !$("modalLayer").classList.contains("hidden")) {
      const primary = $("modalFoot").querySelector(".btn.primary");
      if (primary) primary.click();
    }
  });
}

/* ---------- boot ---------- */
async function boot() {
  setupEvents();
  try {
    const payload = await api("/api/state");
    applyState(payload);
    S.knownState = payload.status ? payload.status.state : "disconnected";
    renderAll();
    const initialPage = String(location.hash || "").replace(/^#/, "");
    if (PAGE_TITLES[initialPage]) showPage(initialPage, false);
    S.booted = true;
    window.setTimeout(() => $("bootScreen").classList.add("done"), 350);
    pollStatus();
  } catch (error) {
    const boot = $("bootScreen");
    const p = boot.querySelector(".boot-screen p");
    if (p) p.textContent = "无法连接本地服务：" + firstLine(error.message);
    const spinner = boot.querySelector(".boot-spinner");
    if (spinner) spinner.style.display = "none";
    const retry = button("重试", "btn primary", () => window.location.reload());
    retry.style.marginTop = "22px";
    boot.appendChild(retry);
  }
}

document.addEventListener("DOMContentLoaded", boot);
