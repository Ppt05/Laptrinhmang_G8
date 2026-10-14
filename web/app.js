const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const tokenKey = "ottv2_session";
const teamKey = "ottv2_team";
const token = () => sessionStorage.getItem(tokenKey);
const moveSample = `import json, sys

for line in sys.stdin:
    state = json.loads(line)
    moves = state["legal_moves"]
    if not moves:
        break
    move = max(moves, key=lambda m: m["capture"] is not None)
    print(json.dumps({"piece_id": move["piece_id"], "to": move["to"]}), flush=True)
`;

async function request(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const response = await fetch(url, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Lỗi máy chủ (${response.status})`);
  return body;
}

function message(form, text, error = false) {
  const box = $(".form-message", form);
  if (!box) return;
  box.textContent = text;
  box.classList.toggle("error", error);
}

function setSession(data) {
  sessionStorage.setItem(tokenKey, data.token);
  sessionStorage.setItem(teamKey, data.name);
  paintSession();
  refreshDashboard();
}

function paintSession() {
  const signed = Boolean(token());
  $("#authForms").classList.toggle("hidden", signed);
  $("#signedIn").classList.toggle("hidden", !signed);
  $("#submitForm").classList.toggle("hidden", !signed);
  $("#submitGuestHint").classList.toggle("hidden", signed);
  $("#dashboard").classList.toggle("hidden", !signed);
  $("#dashboardHint").classList.toggle("hidden", signed);
  $("#teamName").textContent = sessionStorage.getItem(teamKey) || "Đội dự thi";
  $("#navLogin").classList.toggle("hidden", signed);
  $("#navRegister").classList.toggle("hidden", signed);
  $("#navAccount").classList.toggle("hidden", !signed);
  $("#dialogTitle").textContent = signed ? "Tài khoản đội" : "Đăng nhập";
}

function showToast(text, error = false) {
  const toast = document.createElement("div");
  toast.className = `toast${error ? " error" : ""}`;
  toast.textContent = text;
  document.body.append(toast);
  setTimeout(() => toast.remove(), 4200);
}

function activateTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("#loginForm").classList.toggle("hidden", name !== "login");
  $("#registerForm").classList.toggle("hidden", name !== "register");
}

function openAccount(mode = "login") {
  activateTab(mode);
  const dialog = $("#accountDialog");
  if (!dialog.open) dialog.showModal();
}

$("#navLogin").addEventListener("click", () => openAccount("login"));
$("#navRegister").addEventListener("click", () => openAccount("register"));
$("#navAccount").addEventListener("click", () => openAccount("login"));
$("#submitLoginLink").addEventListener("click", () => openAccount("login"));
$("#closeDialog").addEventListener("click", () => $("#accountDialog").close());
$("#accountDialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

const rulesDialog = $("#rulesDialog");
$$("[data-open-rules]").forEach((button) => button.addEventListener("click", () => rulesDialog.showModal()));
$("#closeRules").addEventListener("click", () => rulesDialog.close());
rulesDialog.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) rulesDialog.close();
});

const leaderboardDialog = $("#leaderboardDialog");
let leaderboardMode = "season";
let leaderboardRequestId = 0;
function vietnamToday() {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}
function openLeaderboard() {
  if (!leaderboardDialog.open) leaderboardDialog.showModal();
  refreshLeaderboard();
}
$$('[data-open-leaderboard]').forEach((button) => button.addEventListener("click", openLeaderboard));
$("#closeLeaderboard").addEventListener("click", () => leaderboardDialog.close());
leaderboardDialog.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) leaderboardDialog.close();
});
function setLeaderboardMode(mode) {
  leaderboardMode = mode;
  const daily = mode === "daily";
  $("#leaderboardSeason").classList.toggle("active", !daily);
  $("#leaderboardDaily").classList.toggle("active", daily);
  $("#leaderboardSeason").setAttribute("aria-pressed", String(!daily));
  $("#leaderboardDaily").setAttribute("aria-pressed", String(daily));
  $("#leaderboardDayWrap").classList.toggle("hidden", !daily);
  if (daily && !$("#leaderboardDay").value) $("#leaderboardDay").value = vietnamToday();
  refreshLeaderboard();
}
$("#leaderboardSeason").addEventListener("click", () => setLeaderboardMode("season"));
$("#leaderboardDaily").addEventListener("click", () => setLeaderboardMode("daily"));
$("#leaderboardDay").addEventListener("change", refreshLeaderboard);

$$(".tab").forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));

$("#registerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form));
  try {
    const result = await request("/api/register", { method: "POST", body: JSON.stringify(data) });
    setSession(result);
    $("#accountDialog").close();
    form.reset();
    showToast(`Đã tạo tài khoản đội ${result.name}.`);
  } catch (error) { message(form, error.message, true); }
});

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const result = await request("/api/login", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(form))) });
    setSession(result);
    $("#accountDialog").close();
    form.reset();
    showToast(`Xin chào ${result.name}!`);
  } catch (error) { message(form, error.message, true); }
});

$("#logoutBtn").addEventListener("click", () => {
  sessionStorage.removeItem(tokenKey);
  sessionStorage.removeItem(teamKey);
  paintSession();
  $("#accountDialog").close();
  showToast("Bạn đã đăng xuất.");
});

$("#sampleBtn").addEventListener("click", () => {
  $("#submitForm textarea[name=source]").value = moveSample;
  $("#submitForm select[name=language]").value = "python";
});

$("#submitForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!token()) { openAccount("login"); showToast("Đăng nhập đội trước khi nộp bài.", true); return; }
  const data = Object.fromEntries(new FormData(form));
  try {
    const result = await request("/api/submissions", { method: "POST", body: JSON.stringify(data) });
    message(form, `Đã nhận bài #${result.submission_id}; trạng thái hiện tại: ${result.status}.`);
    form.elements.source.value = "";
    refreshDashboard();
  } catch (error) { message(form, error.message, true); }
});

const statusText = { queued: "Đang chờ", running: "Đang xử lý", ready: "Sẵn sàng", failed: "Lỗi" };
function dateText(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

async function refreshDashboard() {
  if (!token()) return;
  try {
    const [subs, matches] = await Promise.all([request("/api/submissions"), request("/api/my/matches")]);
    $("#submissionsBody").innerHTML = subs.map((s) => `<tr><td><strong>#${s.id}</strong></td><td>${s.language === "cpp" ? "C++17" : "Python"}</td><td>${dateText(s.created_at)}</td><td><span class="pill ${s.status}">${statusText[s.status] || s.status}</span>${s.compile_error ? `<small class="row-error" title="Lỗi biên dịch">${escapeHtml(s.compile_error)}</small>` : ""}</td></tr>`).join("");
    $("#submissionEmpty").classList.toggle("hidden", subs.length > 0);
    $("#matchesBody").innerHTML = matches.map((m) => `<tr><td><strong>#${m.id}</strong><small class="row-sub">${escapeHtml(m.event_day || "")}</small></td><td>${escapeHtml(m.opponent)}</td><td><span class="pill ${m.result}">${({ win: "Thắng", loss: "Thua", draw: "Hòa", pending: "Đang chờ" })[m.result] || m.status}</span></td><td>${Number(m.points).toLocaleString("vi-VN")}</td></tr>`).join("");
    $("#matchesEmpty").classList.toggle("hidden", matches.length > 0);
  } catch (error) {
    if (/token|401/i.test(error.message)) { sessionStorage.removeItem(tokenKey); paintSession(); }
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function refreshLeaderboard() {
  const requestId = ++leaderboardRequestId;
  if (leaderboardMode === "daily" && !$("#leaderboardDay").value) $("#leaderboardDay").value = vietnamToday();
  const day = leaderboardMode === "daily" ? $("#leaderboardDay").value : "";
  $("#leaderboardPeriod").textContent = day ? `Kết quả các trận ghép ngày ${day.split("-").reverse().join("/")}.` : "Tổng điểm từ đầu cuộc thi.";
  try {
    const rows = await request(`/api/leaderboard${day ? `?day=${encodeURIComponent(day)}` : ""}`);
    if (requestId !== leaderboardRequestId) return;
    $("#leaderboardBody").innerHTML = rows.map((r, i) => `<tr><td><span class="rank-badge${i < 3 ? " top" : ""}">${i + 1}</span></td><td><strong>${escapeHtml(r.name)}</strong></td><td>${r.matches}</td><td>${r.wins}</td><td><strong>${Number(r.points).toLocaleString("vi-VN")}</strong></td></tr>`).join("");
    $("#leaderboardEmpty").textContent = day ? "Ngày này chưa có trận hoàn tất." : "Bảng xếp hạng chưa có dữ liệu.";
    $("#leaderboardEmpty").classList.toggle("hidden", rows.length > 0);
  } catch {
    if (requestId !== leaderboardRequestId) return;
    $("#leaderboardBody").replaceChildren();
    $("#leaderboardEmpty").textContent = "Chưa tải được bảng xếp hạng.";
    $("#leaderboardEmpty").classList.remove("hidden");
  }
}

$("#refreshBtn").addEventListener("click", () => { refreshDashboard(); refreshLeaderboard(); });

async function refreshEventStatus() {
  try {
    const meta = await request("/api/meta");
    const start = dateText(meta.start_at);
    const end = dateText(meta.end_at);
    const statusMessages = {
      open: `Thi đấu đang mở từ ${start} đến ${end}. Nộp bot và theo dõi kết quả trên bảng xếp hạng.`,
      scheduled: `Cuộc thi đã lên lịch mở từ ${start} đến ${end}.`,
      closed: "Khung giờ thi đấu đã đóng. Ban tổ chức sẽ cập nhật kết quả tại đây.",
      worker_unavailable: `Đã đến khung giờ thi ${start} – ${end}, nhưng máy chủ chạy bot chưa sẵn sàng.`,
      not_scheduled: "Chưa có lịch thi đấu. Ban tổ chức sẽ cập nhật thời gian tại đây.",
    };
    $("#eventStatus").textContent = statusMessages[meta.status] || "Chưa tải được lịch thi đấu.";
  } catch { $("#eventStatus").textContent = "Không lấy được trạng thái máy chủ. Hãy tải lại trang sau ít phút."; }
}

async function init() {
  paintSession();
  refreshLeaderboard();
  if (location.hash === "#bang-xep-hang") openLeaderboard();
  if (token()) {
    try {
      const player = await request("/api/me");
      sessionStorage.setItem(teamKey, player.name);
      paintSession();
      refreshDashboard();
    } catch { sessionStorage.removeItem(tokenKey); sessionStorage.removeItem(teamKey); paintSession(); }
  }
  refreshEventStatus();
}

init();
setInterval(() => { if (token()) refreshDashboard(); refreshLeaderboard(); refreshEventStatus(); }, 30000);
