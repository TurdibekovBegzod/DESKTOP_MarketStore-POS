"use strict";

const API_ROOT = "/api/v1/superadmin";
const state = { accounts: [], token: sessionStorage.getItem("marketstore_superadmin_token") || "", action: null };

const byId = (id) => document.getElementById(id);
const loginView = byId("loginView");
const appView = byId("appView");
const accountsBody = byId("accountsBody");
const loadingState = byId("loadingState");
const emptyState = byId("emptyState");
const confirmModal = byId("confirmModal");

function detailMessage(payload, fallback) {
  if (!payload) return fallback;
  if (typeof payload.detail === "string") return payload.detail;
  if (Array.isArray(payload.detail)) return payload.detail.map((item) => item.msg).join("; ");
  return fallback;
}

async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Content-Type", "application/json");
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  const response = await fetch(`${API_ROOT}${path}`, { ...options, headers, cache: "no-store" });
  const payload = await response.json().catch(() => null);
  if (response.status === 401 && path !== "/login") {
    logout("Sessiya tugadi. Qayta kiring.");
    throw new Error("Sessiya tugadi");
  }
  if (!response.ok) throw new Error(detailMessage(payload, `HTTP ${response.status}`));
  return payload;
}

function setView(authenticated) {
  loginView.classList.toggle("hidden", authenticated);
  appView.classList.toggle("hidden", !authenticated);
  if (!authenticated) setTimeout(() => byId("username").focus(), 0);
}

function logout(message = "") {
  state.token = "";
  state.accounts = [];
  // The log stream carries the token in its URL, so it must not outlive it.
  if (typeof stopLogStream === "function") stopLogStream();
  sessionStorage.removeItem("marketstore_superadmin_token");
  sessionStorage.removeItem("marketstore_superadmin_expires");
  setView(false);
  if (message) byId("loginError").textContent = message;
}

function formatNumber(value) {
  return new Intl.NumberFormat("uz-UZ").format(Number(value || 0));
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : new Intl.DateTimeFormat("uz-UZ", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  }).format(date);
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function accountCell(account) {
  const cell = document.createElement("td");
  cell.append(element("div", "account-name", account.display_name || "Nomsiz account"));
  cell.append(element("div", "account-email", account.email));
  cell.append(element("div", "account-uid", account.user_uid));
  return cell;
}

function statusCell(account) {
  const cell = document.createElement("td");
  cell.append(element("span", `badge ${account.is_active ? "badge-active" : "badge-inactive"}`, account.is_active ? "Faol" : "Bloklangan"));
  if (!account.is_verified) cell.append(element("div", "badge badge-pending", "Tasdiqlanmagan"));
  return cell;
}

function metricCell(main, sub = "") {
  const cell = document.createElement("td");
  cell.append(element("div", "metric-main", formatNumber(main)));
  if (sub) cell.append(element("div", "metric-sub", sub));
  return cell;
}

function actionButton(label, className, onClick) {
  const button = element("button", className, label);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

function menuCell(account) {
  const cell = element("td", "menu-cell");
  const trigger = element("button", "menu-trigger");
  trigger.type = "button";
  trigger.setAttribute("aria-label", `${account.email} amallari`);
  trigger.innerHTML = "&#8942;";
  menu.append(actionButton("Loglarni ko'rish", "", () => {
    closeMenus();
    switchView("logs");
    byId("logSearch").value = account.email;
    logs.query = account.email;
    logs.before = null;
    loadLogPage({ reset: true });
  }));
  menu.append(actionButton(account.is_active ? "Accountni bloklash" : "Accountni faollashtirish", "", () => {
    closeMenus();
    openConfirm(account, "status");
  }));
  menu.append(actionButton("Hamma qurilmadan tozalash", "", () => {
    closeMenus();
    openConfirm(account, "clear");
  }));
  menu.append(actionButton("Accountni o'chirish", "menu-danger", () => {
    closeMenus();
    openConfirm(account, "delete");
  }));

  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    const wasHidden = menu.classList.contains("hidden");
    closeMenus();
    if (wasHidden) menu.classList.remove("hidden");
  });
  menu.addEventListener("click", (event) => event.stopPropagation());
  cell.append(trigger, menu);
  return cell;
}

function renderAccounts() {
  const query = byId("searchInput").value.trim().toLowerCase();
  const accounts = state.accounts.filter((account) => {
    const haystack = `${account.email} ${account.display_name || ""} ${account.user_uid}`.toLowerCase();
    return haystack.includes(query);
  });
  accountsBody.replaceChildren();
  for (const account of accounts) {
    const row = document.createElement("tr");
    const recordSub = account.deleted_records_count ? `${formatNumber(account.deleted_records_count)} ta o'chirilgan` : "";
    row.append(
      accountCell(account),
      statusCell(account),
      metricCell(account.records_count, recordSub),
      metricCell(account.devices_count, `${formatNumber(account.sync_batches_count)} ta sync`),
      element("td", "date-cell", formatDate(account.last_activity_at)),
      element("td", "date-cell", formatDate(account.created_at)),
      menuCell(account)
    );
    accountsBody.append(row);
  }
  emptyState.classList.toggle("hidden", accounts.length !== 0);
}

async function loadAccounts() {
  loadingState.classList.remove("hidden");
  emptyState.classList.add("hidden");
  try {
    const data = await apiRequest("/accounts");
    state.accounts = data.accounts;
    byId("totalAccounts").textContent = formatNumber(data.total_accounts);
    byId("activeAccounts").textContent = formatNumber(data.active_accounts);
    byId("totalRecords").textContent = formatNumber(data.total_records);
    byId("totalDevices").textContent = formatNumber(data.total_devices);
    byId("updatedAt").textContent = `Yangilandi: ${formatDate(new Date().toISOString())}`;
    renderAccounts();
  } catch (error) {
    if (state.token) showToast(error.message, true);
  } finally {
    loadingState.classList.add("hidden");
  }
}

function closeMenus() {
  document.querySelectorAll(".action-menu").forEach((menu) => menu.classList.add("hidden"));
}

function openConfirm(account, kind) {
  state.action = { account, kind };
  const title = byId("modalTitle");
  const message = byId("modalMessage");
  const confirm = byId("modalConfirm");
  if (kind === "status") {
    title.textContent = account.is_active ? "Accountni bloklash" : "Accountni faollashtirish";
    message.textContent = account.is_active
      ? "Bloklangandan keyin account mavjud sessiyalar bilan ham API amallarini bajara olmaydi."
      : "Account qayta login va sinxronizatsiya qila oladi.";
    confirm.textContent = account.is_active ? "Bloklash" : "Faollashtirish";
  } else if (kind === "clear") {
    title.textContent = "Account ma'lumotini tozalash";
    message.textContent = "Account saqlanadi. Serverdagi barcha ma'lumot o'chadi; online qurilmalar darhol, offline qurilmalar esa keyingi ulanishida lokal mahsulotlar va boshqa ma'lumotlarni ham o'chiradi. Eski qurilma ularni qayta yuklay olmaydi.";
    confirm.textContent = "Hamma qurilmalardan tozalash";
  } else {
    title.textContent = "Accountni butunlay o'chirish";
    message.textContent = "Account, login, qurilmalar va serverdagi barcha ma'lumotlar qaytarib bo'lmaydigan tarzda o'chiriladi.";
    confirm.textContent = "Accountni o'chirish";
  }
  byId("modalEmail").textContent = account.email;
  byId("confirmEmail").value = "";
  byId("modalError").textContent = "";
  confirm.disabled = true;
  confirmModal.classList.remove("hidden");
  setTimeout(() => byId("confirmEmail").focus(), 0);
}

function closeConfirm() {
  state.action = null;
  confirmModal.classList.add("hidden");
}

async function runConfirmedAction() {
  if (!state.action) return;
  const { account, kind } = state.action;
  const confirmButton = byId("modalConfirm");
  const confirmEmail = byId("confirmEmail").value.trim();
  confirmButton.disabled = true;
  byId("modalError").textContent = "";
  try {
    let path;
    let body = { confirm_email: confirmEmail };
    if (kind === "status") {
      path = `/accounts/${encodeURIComponent(account.user_uid)}/status`;
      body.is_active = !account.is_active;
    } else if (kind === "clear") {
      path = `/accounts/${encodeURIComponent(account.user_uid)}/clear-data`;
    } else {
      path = `/accounts/${encodeURIComponent(account.user_uid)}/delete`;
    }
    const result = await apiRequest(path, { method: "POST", body: JSON.stringify(body) });
    closeConfirm();
    showToast(result.message || "Amal bajarildi");
    await loadAccounts();
  } catch (error) {
    byId("modalError").textContent = error.message;
    confirmButton.disabled = false;
  }
}

let toastTimer;
function showToast(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("toast-error", isError);
  toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), 4200);
}

async function checkAvailability() {
  // Say plainly that the server was never configured, rather than letting the
  // person conclude they typed the wrong password.
  try {
    const response = await fetch(`${API_ROOT}/availability`, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    if (payload && payload.enabled === false) {
      byId("loginError").textContent = payload.message || "Superadmin panel yoqilmagan.";
      byId("loginButton").disabled = true;
    }
  } catch (error) {
    /* Offline or blocked: the login attempt will report it. */
  }
}

checkAvailability();

byId("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("loginButton");
  const error = byId("loginError");
  error.textContent = "";
  button.disabled = true;
  button.textContent = "Kirilmoqda...";
  try {
    const result = await apiRequest("/login", {
      method: "POST",
      body: JSON.stringify({ username: byId("username").value.trim(), password: byId("password").value })
    });
    state.token = result.access_token;
    sessionStorage.setItem("marketstore_superadmin_token", state.token);
    sessionStorage.setItem("marketstore_superadmin_expires", String(Date.now() + result.expires_in_seconds * 1000));
    byId("password").value = "";
    setView(true);
    await loadAccounts();
  } catch (loginError) {
    error.textContent = loginError.message;
  } finally {
    button.disabled = false;
    button.textContent = "Kirish";
  }
});

byId("showPassword").addEventListener("click", () => {
  const input = byId("password");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  byId("showPassword").textContent = show ? "Yashirish" : "Ko'rsatish";
});
byId("logoutButton").addEventListener("click", () => logout());
byId("refreshButton").addEventListener("click", () => {
  if (byId("tabLogs").classList.contains("is-active")) {
    openLogs();
  } else {
    loadAccounts();
  }
});
byId("searchInput").addEventListener("input", renderAccounts);
byId("modalClose").addEventListener("click", closeConfirm);
byId("modalCancel").addEventListener("click", closeConfirm);
byId("modalConfirm").addEventListener("click", runConfirmedAction);
byId("confirmEmail").addEventListener("input", () => {
  const expected = state.action ? state.action.account.email.toLowerCase() : "";
  byId("modalConfirm").disabled = byId("confirmEmail").value.trim().toLowerCase() !== expected;
});
confirmModal.addEventListener("click", (event) => { if (event.target === confirmModal) closeConfirm(); });
document.addEventListener("click", closeMenus);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeMenus();
    if (!confirmModal.classList.contains("hidden")) closeConfirm();
  }
});

const expiresAt = Number(sessionStorage.getItem("marketstore_superadmin_expires") || 0);
if (state.token && expiresAt > Date.now()) {
  setView(true);
  loadAccounts();
} else {
  logout();
}

/* --- Server loglari -------------------------------------------------------
 * Docker faqat oxirgi bir necha megabaytni saqlaydi. Yig'uvchi xizmat har bir
 * qatorni oylik arxivga yozadi: pastda jonli oqim, yuqoriga aylantirilsa esa
 * o'sha oyning boshigacha, undan keyin arxivlangan oylar.
 */

const logs = {
  month: "",
  current: "",
  container: "",
  query: "",
  before: null,
  offset: 0,
  live: true,
  source: null,
  loading: false,
  searchTimer: null,
};

function logStatus(text) {
  byId("logsStatus").textContent = text;
}

function atBottom(view) {
  return view.scrollHeight - view.scrollTop - view.clientHeight < 40;
}

function logRow(entry) {
  const row = document.createElement("div");
  row.className = entry.s === "stderr" ? "log-row is-stderr" : "log-row";

  const time = document.createElement("span");
  time.className = "log-time";
  const moment = new Date(entry.t);
  time.textContent = Number.isNaN(moment.getTime())
    ? "--:--:--"
    : moment.toLocaleTimeString("uz-UZ", { hour12: false });
  time.title = entry.t || "";

  const name = document.createElement("span");
  name.className = "log-container";
  name.textContent = (entry.c || "").replace(/^marketstore-/, "");
  name.title = entry.c || "";

  const message = document.createElement("span");
  message.className = "log-message";
  message.textContent = entry.m || "";

  row.append(time, name, message);
  return row;
}

function appendLines(entries, { prepend = false } = {}) {
  if (!entries || !entries.length) return;
  const view = byId("logView");
  const emptyMsg = view.querySelector(".log-empty-msg");
  if (emptyMsg) emptyMsg.remove();
  const stick = !prepend && atBottom(view);
  const fragment = document.createDocumentFragment();
  entries.forEach((entry) => fragment.append(logRow(entry)));
  if (prepend) {
    // Keep the reader where they were: adding above must not move the text
    // they are currently looking at.
    const anchorHeight = view.scrollHeight;
    view.prepend(fragment);
    view.scrollTop += view.scrollHeight - anchorHeight;
  } else {
    view.append(fragment);
    if (stick) view.scrollTop = view.scrollHeight;
  }
}

async function loadLogMonths() {
  try {
    const result = await apiRequest("/logs/months");
    logs.current = result.current || new Date().toISOString().slice(0, 7);
    if (!logs.month) logs.month = logs.current;
    const select = byId("logMonth");
    select.textContent = "";
    const months = (result.months && result.months.length) ? result.months : [{ month: logs.current, bytes: 0, archived: false }];
    months.forEach((item) => {
      const option = document.createElement("option");
      const size = item.bytes > 1024 * 1024
        ? `${(item.bytes / (1024 * 1024)).toFixed(1)} MB`
        : `${Math.round(item.bytes / 1024)} KB`;
      option.value = item.month;
      option.textContent = `${item.month} - ${size}${item.archived ? " (arxiv)" : ""}`;
      select.append(option);
    });
    select.value = logs.month;

    const containers = byId("logContainer");
    const chosen = logs.container;
    containers.textContent = "";
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "Barcha konteynerlar";
    containers.append(all);
    (result.containers || []).forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name.replace(/^marketstore-/, "");
      containers.append(option);
    });
    containers.value = chosen;
  } catch (error) {
    if (!logs.month) logs.month = new Date().toISOString().slice(0, 7);
  }
}

async function loadLogPage({ reset = false } = {}) {
  if (logs.loading) return;
  logs.loading = true;
  const view = byId("logView");
  try {
    const targetMonth = logs.month || logs.current || new Date().toISOString().slice(0, 7);
    const params = new URLSearchParams({ month: targetMonth, limit: "300" });
    if (!reset && logs.before !== null) params.set("before", String(logs.before));
    if (logs.container) params.set("container", logs.container);
    if (logs.query) params.set("q", logs.query);
    const page = await apiRequest(`/logs?${params.toString()}`);
    if (reset) view.textContent = "";
    const lines = page.lines || [];
    appendLines(lines, { prepend: !reset });
    logs.before = page.next_before;
    if (reset) {
      logs.offset = page.offset || 0;
      view.scrollTop = view.scrollHeight;
      if (!lines.length) {
        logStatus("Bu oy uchun yozuv yo'q. Yig'uvchi xizmat ishga tushganini tekshiring.");
        if (!view.children.length) {
          const empty = document.createElement("div");
          empty.className = "log-empty-msg";
          empty.textContent = "Hozircha server loglari mavjud emas.";
          view.append(empty);
        }
      }
    }
    byId("logOlder").disabled = !page.has_more;
    if (lines.length || !reset) {
      logStatus(page.has_more
        ? `${targetMonth} - yuqoriga aylantirib eskisini ko'ring`
        : `${targetMonth} - oy boshidan beri hammasi ko'rsatildi`);
    }
  } catch (error) {
    logStatus(error.message);
  } finally {
    logs.loading = false;
  }
}

function stopLogStream() {
  if (logs.source) {
    logs.source.close();
    logs.source = null;
  }
}

function startLogStream() {
  stopLogStream();
  if (!logs.live || logs.month !== logs.current || !state.token) return;
  const params = new URLSearchParams({ token: state.token, offset: String(logs.offset || 0) });
  if (logs.container) params.set("container", logs.container);
  const source = new EventSource(`${API_ROOT}/logs/stream?${params.toString()}`);
  logs.source = source;
  source.addEventListener("hello", (event) => {
    const payload = JSON.parse(event.data || "{}");
    logs.offset = payload.offset || logs.offset;
    logStatus(`${logs.month} - jonli`);
  });
  source.addEventListener("lines", (event) => {
    const payload = JSON.parse(event.data || "{}");
    logs.offset = payload.offset || logs.offset;
    const entries = (payload.lines || []).filter(
      (entry) => !logs.query || (entry.m || "").toLowerCase().includes(logs.query.toLowerCase())
    );
    appendLines(entries);
  });
  source.onerror = () => {
    if (logs.live && logs.source) {
      logStatus(`${logs.month} - aloqa uzildi, qayta ulanmoqda...`);
    }
  };
}

async function openLogs() {
  try {
    await loadLogMonths();
    await loadLogPage({ reset: true });
    startLogStream();
  } catch (error) {
    logStatus(error.message);
  }
}

function switchView(view) {
  const showLogs = view === "logs";
  byId("accountsView").classList.toggle("hidden", showLogs);
  byId("logsView").classList.toggle("hidden", !showLogs);
  byId("tabAccounts").classList.toggle("is-active", !showLogs);
  byId("tabLogs").classList.toggle("is-active", showLogs);
  if (showLogs) openLogs();
  else stopLogStream();
}

byId("tabAccounts").addEventListener("click", () => switchView("accounts"));
byId("tabLogs").addEventListener("click", () => switchView("logs"));
byId("logMonth").addEventListener("change", (event) => {
  logs.month = event.target.value;
  logs.before = null;
  stopLogStream();
  loadLogPage({ reset: true }).then(startLogStream);
});
byId("logContainer").addEventListener("change", (event) => {
  logs.container = event.target.value;
  logs.before = null;
  stopLogStream();
  loadLogPage({ reset: true }).then(startLogStream);
});
byId("logSearch").addEventListener("input", (event) => {
  logs.query = event.target.value.trim();
  clearTimeout(logs.searchTimer);
  logs.searchTimer = setTimeout(() => {
    logs.before = null;
    loadLogPage({ reset: true });
  }, 300);
});
byId("logOlder").addEventListener("click", () => loadLogPage());
byId("logLive").addEventListener("click", () => {
  logs.live = !logs.live;
  const button = byId("logLive");
  button.textContent = logs.live ? "Jonli: yoniq" : "Jonli: o'chiq";
  button.setAttribute("aria-pressed", String(logs.live));
  button.classList.toggle("is-live", logs.live);
  if (logs.live) startLogStream();
  else stopLogStream();
});
byId("logView").addEventListener("scroll", () => {
  const view = byId("logView");
  if (view.scrollTop <= 24 && logs.before !== null && !logs.loading) loadLogPage();
});
window.addEventListener("beforeunload", stopLogStream);
