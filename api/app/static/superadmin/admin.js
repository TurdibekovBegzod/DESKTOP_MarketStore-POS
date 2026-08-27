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
  const menu = element("div", "action-menu hidden");

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
byId("refreshButton").addEventListener("click", loadAccounts);
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
