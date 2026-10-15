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

const replayDialog = $("#replayDialog");
let replayItems = [];
let replayHasMore = false;
let replayMatch = null;
let replayFrames = [];
let replayMoves = [];
let replayIndex = 0;
let replayTimer = null;
let replaySelectionId = 0;
const replaySymbols = { rock: "✊", scissors: "✌️", paper: "✋" };
const replayReasons = {
  goal: "Đưa quân tới ô đích", elimination: "Ăn hết một loại quân đối thủ",
  invalid_move_or_timeout: "Bot không trả về nước đi hợp lệ hoặc quá giờ",
  threefold_repetition_draw: "Lặp thế cờ ba lần",
  no_legal_moves_draw: "Không còn nước đi hợp lệ", move_limit_draw: "Đủ giới hạn nước đi",
};

function stopReplay() {
  if (replayTimer) clearInterval(replayTimer);
  replayTimer = null;
  $("#replayPlay").textContent = "▶ Phát";
  $("#replayPlay").setAttribute("aria-label", "Phát trận");
}

function renderReplayList() {
  $("#replayList").innerHTML = replayItems.map((item) => {
    const outcome = item.winner ? `Thắng: ${escapeHtml(item.winner)}` : "Hòa";
    return `<button class="replay-list-item${replayMatch?.id === item.id ? " active" : ""}" type="button" data-replay-id="${item.id}"><span class="replay-list-title">#${item.id} · ${escapeHtml(item.team_a)} vs ${escapeHtml(item.team_b)}</span><span class="replay-list-meta">${escapeHtml(item.event_day || "")} · ${item.move_count} nước · ${outcome}</span></button>`;
  }).join("");
  $("#moreReplays").classList.toggle("hidden", !replayHasMore);
}

async function loadReplays(append = false) {
  if (!append) {
    replayItems = [];
    $("#replayList").innerHTML = '<p class="replay-list-message">Đang tải các trận...</p>';
  }
  try {
    const page = await request(`/api/replays?offset=${replayItems.length}&limit=30`);
    replayItems.push(...page.items);
    replayHasMore = page.has_more;
    renderReplayList();
    if (!replayItems.length) $("#replayList").innerHTML = '<p class="replay-list-message">Chưa có trận hoàn tất. Khi bot đấu xong, trận sẽ xuất hiện ở đây.</p>';
  } catch {
    $("#replayList").innerHTML = '<p class="replay-list-message">Không tải được danh sách trận. Hãy thử lại.</p>';
  }
}

function makeReplayFrames(match, moves) {
  const initial = match.initial_pieces.map((piece) => ({ ...piece }));
  const frames = [initial];
  for (const turn of moves) {
    const pieces = frames.at(-1).map((piece) => ({ ...piece }));
    if (turn.piece_id && turn.to) {
      const moving = pieces.find((piece) => piece.id === turn.piece_id);
      if (moving) {
        const captureIndex = pieces.findIndex((piece) => piece.id === turn.capture);
        if (captureIndex >= 0) pieces.splice(captureIndex, 1);
        moving.x = turn.to.charCodeAt(0) - 97;
        moving.y = Number(turn.to.slice(1)) - 1;
      }
    }
    frames.push(pieces);
  }
  return frames;
}

function paintReplay() {
  const lastTurn = replayIndex ? replayMoves[replayIndex - 1] : null;
  const failedTurn = replayMatch?.detail.turns.find((turn) => turn.error);
  const board = $("#replayBoard");
  const pieces = new Map((replayFrames[replayIndex] || []).map((piece) => [`${piece.x},${piece.y}`, piece]));
  const cells = [];
  for (let y = 8; y >= 0; y--) {
    for (let x = 0; x < 9; x++) {
      const squareName = `${String.fromCharCode(97 + x)}${y + 1}`;
      const piece = pieces.get(`${x},${y}`);
      const classes = ["replay-cell", (x + y) % 2 ? "dark" : "light"];
      if (squareName === "a1") classes.push("north-goal");
      if (squareName === "i9") classes.push("south-goal");
      if (lastTurn?.from === squareName) classes.push("last-from");
      if (lastTurn?.to === squareName) classes.push("last-to");
      cells.push(`<div class="${classes.join(" ")}" aria-label="${squareName}${piece ? ` ${piece.side === "south" ? "Xanh" : "Hồng"} ${piece.type}` : ""}"><small>${squareName}</small>${piece ? `<span class="replay-piece ${piece.side}" title="${piece.id} · ${piece.type}">${replaySymbols[piece.type] || "?"}</span>` : ""}</div>`);
    }
  }
  board.innerHTML = cells.join("");
  $("#replayCounter").textContent = `${replayIndex} / ${Math.max(0, replayFrames.length - 1)}`;
  const team = lastTurn?.side === "south" ? replayMatch.team_a : replayMatch?.team_b;
  const action = lastTurn ? `${lastTurn.piece_id} → ${lastTurn.to}${lastTurn.capture ? `, ăn ${lastTurn.capture}` : ""}` : "Bàn cờ ban đầu";
  const atEnd = replayIndex === replayFrames.length - 1;
  const failedTeam = failedTurn?.side === "south" ? replayMatch?.team_a : replayMatch?.team_b;
  const failure = failedTurn && atEnd ? `${failedTeam} không trả về nước đi hợp lệ hoặc quá giờ.` : "";
  const ending = atEnd && !failedTurn && replayIndex > 0 ? ` · ${replayReasons[replayMatch.detail.win_reason] || replayMatch.detail.win_reason || "Kết thúc"}` : "";
  $("#replayMove").textContent = !replayMoves.length && failure
    ? `Trận kết thúc trước nước đi đầu tiên. ${failure} Không có nước đi để phát lại.`
    : lastTurn ? `Nước ${replayIndex}: ${team} ${action}${ending}${failure ? ` · Sau đó ${failure}` : ""}` : action;
  $("#replayFirst").disabled = $("#replayPrev").disabled = replayIndex === 0;
  $("#replayNext").disabled = $("#replayLast").disabled = replayIndex >= replayFrames.length - 1;
  $("#replayPlay").disabled = replayFrames.length <= 1;
}

async function selectReplay(matchId) {
  const selectionId = ++replaySelectionId;
  stopReplay();
  $("#replayMove").textContent = "Đang tải diễn biến trận...";
  try {
    const match = await request(`/api/matches/${matchId}`);
    if (selectionId !== replaySelectionId) return;
    if (match.status !== "done" || !Array.isArray(match.detail?.turns)) throw new Error("Trận chưa có dữ liệu phát lại.");
    replayMatch = match;
    replayMoves = match.detail.turns.filter((turn) => turn.piece_id && turn.to);
    replayFrames = makeReplayFrames(match, replayMoves);
    replayIndex = 0;
    $("#replaySouth").textContent = match.team_a;
    $("#replayNorth").textContent = match.team_b;
    $("#replayResult").textContent = match.winner_id === null ? "Hòa" : `Thắng: ${match.winner_id === match.player_a ? match.team_a : match.team_b}`;
    renderReplayList();
    paintReplay();
  } catch (error) {
    if (selectionId === replaySelectionId) $("#replayMove").textContent = error.message;
  }
}

async function openReplay(matchId = null) {
  if (!replayDialog.open) replayDialog.showModal();
  stopReplay();
  await loadReplays();
  if (matchId) selectReplay(matchId);
  else if (replayItems.length) selectReplay(replayItems[0].id);
}

$$('[data-open-replay]').forEach((button) => button.addEventListener("click", () => openReplay()));
$("#closeReplay").addEventListener("click", () => replayDialog.close());
replayDialog.addEventListener("close", stopReplay);
replayDialog.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) replayDialog.close();
});
$("#replayList").addEventListener("click", (event) => {
  const item = event.target.closest("[data-replay-id]");
  if (item) selectReplay(Number(item.dataset.replayId));
});
$("#moreReplays").addEventListener("click", () => loadReplays(true));
$("#refreshReplays").addEventListener("click", () => loadReplays());
$("#replayFirst").addEventListener("click", () => { stopReplay(); replayIndex = 0; paintReplay(); });
$("#replayPrev").addEventListener("click", () => { stopReplay(); replayIndex = Math.max(0, replayIndex - 1); paintReplay(); });
$("#replayNext").addEventListener("click", () => { stopReplay(); replayIndex = Math.min(replayFrames.length - 1, replayIndex + 1); paintReplay(); });
$("#replayLast").addEventListener("click", () => { stopReplay(); replayIndex = replayFrames.length - 1; paintReplay(); });
$("#replayPlay").addEventListener("click", () => {
  if (replayTimer) { stopReplay(); return; }
  if (!replayFrames.length) return;
  if (replayIndex >= replayFrames.length - 1) replayIndex = 0;
  $("#replayPlay").textContent = "Ⅱ Tạm dừng";
  $("#replayPlay").setAttribute("aria-label", "Tạm dừng phát trận");
  replayTimer = setInterval(() => {
    if (replayIndex >= replayFrames.length - 1) { stopReplay(); return; }
    replayIndex += 1;
    paintReplay();
    if (replayIndex >= replayFrames.length - 1) stopReplay();
  }, 900);
});

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
    $("#matchesBody").innerHTML = matches.map((m) => `<tr><td><strong>#${m.id}</strong><small class="row-sub">${escapeHtml(m.event_day || "")}</small></td><td>${escapeHtml(m.opponent)}</td><td><span class="pill ${m.result}">${({ win: "Thắng", loss: "Thua", draw: "Hòa", pending: "Đang chờ" })[m.result] || m.status}</span>${m.status === "done" ? `<button class="match-replay-link" type="button" data-replay-id="${m.id}">Xem lại trận ↗</button>` : ""}</td><td>${Number(m.points).toLocaleString("vi-VN")}</td></tr>`).join("");
    $("#matchesEmpty").classList.toggle("hidden", matches.length > 0);
  } catch (error) {
    if (/token|401/i.test(error.message)) { sessionStorage.removeItem(tokenKey); paintSession(); }
  }
}

$("#matchesBody").addEventListener("click", (event) => {
  const button = event.target.closest("[data-replay-id]");
  if (button) openReplay(Number(button.dataset.replayId));
});

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
