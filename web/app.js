const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const tokenKey = "ottv2_session";
const teamKey = "ottv2_team";
const token = () => sessionStorage.getItem(tokenKey);
const botTemplates = {
  python: `# API: choose_move(state) returns a move dict.
# state: side, move_number, board_size, pieces, legal_moves.
# Piece: id, side, type, x, y (coordinates 0..8).
# Move: piece_id, from, to, capture (None or enemy ID).
# Return a legal move, or {"piece_id": ..., "to": ...}.
# Judge handles input/output. Add helper functions as needed.
def choose_move(state):
    # Viết thuật toán của bạn tại đây.
    # state["pieces"]: quân cờ; state["legal_moves"]: nước hợp lệ.
    return state["legal_moves"][0]
`,
  cpp: `// API: chooseMove(const BoardState& state) returns Move.
// state: side, move_number, board_size, pieces, legal_moves.
// Piece: id, side, type, x, y (coordinates 0..8).
// Move: piece_id, from, to, capture (optional<string>).
// state.at(x,y): const Piece* or nullptr for an empty square.
// state.board[y][x]: optional<Piece>. Types are provided.
// Return a legal move. No main() or input/output code.
Move chooseMove(const BoardState& state) {
    // Viết thuật toán của bạn tại đây.
    // state.pieces, state.legal_moves, state.at(x, y).
    return state.legal_moves.front();
}
`,
};
const sourceDrafts = new Map();
let sourceLanguage = $("#submitForm [name=language]").value;
function paintCodeTemplate() {
  const form = $("#submitForm");
  form.elements.source.value = sourceDrafts.has(sourceLanguage) ? sourceDrafts.get(sourceLanguage) : botTemplates[sourceLanguage];
  $("#submitCodeHelp").textContent = sourceLanguage === "cpp"
    ? "Chỉ nộp hàm Move chooseMove(const BoardState& state). BoardState và Move được cung cấp sẵn."
    : "Chỉ nộp hàm choose_move(state), trả về nước đi. Hệ thống đọc input và in kết quả.";
}
$("#submitForm [name=language]").addEventListener("change", event => {
  sourceDrafts.set(sourceLanguage, $("#submitForm [name=source]").value);
  sourceLanguage = event.target.value;
  paintCodeTemplate();
});
paintCodeTemplate();
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
  if (typeof refreshTournament === "function") refreshTournament();
}

function paintSession() {
  const signed = Boolean(token());
  if(typeof setSubmissionBlock==="function") setSubmissionBlock(signed?"Đang kiểm tra điều kiện nộp bot.":"Đăng nhập tài khoản đội để nộp bot.");
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
  $("#practiceGuestHint").classList.toggle("hidden", signed);
  $("#practiceCodeForm").classList.toggle("hidden", !signed);
  $("#practiceForm").classList.toggle("hidden", !signed);
  if (!signed) $("#practiceActive").classList.add("hidden");
  if (typeof refreshDaily === "function") refreshDaily();
  if ($("#leaderboardMode").value === "daily") refreshLeaderboard();
}

const practiceCodeDrafts = new Map();
let practiceCodeLanguage = $("#practiceCodeForm [name=language]").value;
function paintPracticeCodeTemplate() {
  const textarea = $("#practiceCodeForm [name=source]");
  textarea.value = practiceCodeDrafts.has(practiceCodeLanguage)
    ? practiceCodeDrafts.get(practiceCodeLanguage) : botTemplates[practiceCodeLanguage];
}
$("#practiceCodeForm [name=language]").addEventListener("change", event => {
  practiceCodeDrafts.set(practiceCodeLanguage, $("#practiceCodeForm [name=source]").value);
  practiceCodeLanguage = event.target.value;
  paintPracticeCodeTemplate();
});
paintPracticeCodeTemplate();
let pendingPracticeSubmission = null;
$("#practiceCodeForm").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("#practiceCodeSubmit");
  const data = Object.fromEntries(new FormData(form));
  const encoded = JSON.stringify(data);
  if (!pendingPracticeSubmission || pendingPracticeSubmission.body !== encoded)
    pendingPracticeSubmission = {body: encoded, key: crypto.randomUUID()};
  button.disabled = true;
  message(form, "Đang gửi bot để kiểm tra…");
  try {
    const result = await request("/api/practice/submissions", {
      method: "POST", headers: {"Idempotency-Key": pendingPracticeSubmission.key}, body: encoded,
    });
    pendingPracticeSubmission = null;
    message(form, `Đã nhận bot Practice #${result.submission_id}; trạng thái: ${result.status}.`);
    await Promise.all([refreshDashboard(), refreshPracticeState()]);
  } catch (error) {
    message(form, error.message, true);
  } finally {
    button.disabled = false;
  }
});

function setMode(mode) {
  mode = ["tournament", "practice", "daily"].includes(mode) ? mode : "tournament";
  sessionStorage.setItem("ottv2_mode", mode);
  for (const name of ["tournament", "practice", "daily"]) {
    const button = document.querySelector(`[data-mode="${name}"]`);
    button?.classList.toggle("active", name === mode);
    button?.setAttribute("aria-pressed", String(name === mode));
    $$(`[data-mode-panel="${name}"]`).forEach(panel => panel.classList.toggle("hidden", name !== mode));
  }
  if (mode === "practice") refreshPracticeState();
  if (typeof refreshDaily === "function" && mode === "daily") refreshDaily();
}
const sidebar = $("#siteSidebar");
const sidebarToggle = $("#sidebarToggle");
const sidebarBackdrop = $("#sidebarBackdrop");
const mobileSidebar = window.matchMedia("(max-width: 700px)");
function closeMobileSidebar() {
  document.body.classList.remove("sidebar-mobile-open");
  const isExpanded = mobileSidebar.matches ? false : document.body.classList.contains("sidebar-expanded");
  sidebarToggle.setAttribute("aria-expanded", String(isExpanded));
  sidebarToggle.setAttribute("aria-label", isExpanded ? "Thu gọn thanh điều hướng" : "Mở thanh điều hướng");
}
sidebarToggle.addEventListener("click", () => {
  if (mobileSidebar.matches) {
    const isOpen = document.body.classList.toggle("sidebar-mobile-open");
    sidebarToggle.setAttribute("aria-expanded", String(isOpen));
    sidebarToggle.setAttribute("aria-label", isOpen ? "Đóng thanh điều hướng" : "Mở thanh điều hướng");
    if (isOpen) sidebar.querySelector(".sidebar-link")?.focus({ preventScroll: true });
    return;
  }
  const isExpanded = document.body.classList.toggle("sidebar-expanded");
  sidebarToggle.setAttribute("aria-expanded", String(isExpanded));
  sidebarToggle.setAttribute("aria-label", isExpanded ? "Thu gọn thanh điều hướng" : "Mở thanh điều hướng");
});
sidebarBackdrop.addEventListener("click", closeMobileSidebar);
sidebar.addEventListener("click", event => {
  const modeButton = event.target.closest("[data-mode]");
  if (modeButton) {
    setMode(modeButton.dataset.mode);
    $("#thi-dau").scrollIntoView({ behavior: "smooth", block: "start" });
    closeMobileSidebar();
    return;
  }
  if (event.target.closest(".sidebar-link")) closeMobileSidebar();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && document.body.classList.contains("sidebar-mobile-open")) {
    closeMobileSidebar();
    sidebarToggle.focus();
  }
});
mobileSidebar.addEventListener("change", event => {
  if (!event.matches) closeMobileSidebar();
});
$$('[data-open-account]').forEach(button => button.addEventListener("click", () => openAccount(button.dataset.openAccount || "login")));

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
let leaderboardTournamentId = null;
let leaderboardRequestId = 0;
const modalDailyLeaderboard = new DailyLeaderboard({body: '#leaderboardBody', day: '#leaderboardDailyDay', prefix: 'modalStanding', active: () => $('#leaderboardMode').value === 'daily'});
function openLeaderboard() {
  if (!leaderboardDialog.open) leaderboardDialog.showModal();
  refreshLeaderboard();
}
$$('[data-open-leaderboard]').forEach((button) => button.addEventListener("click", openLeaderboard));
$("#closeLeaderboard").addEventListener("click", () => leaderboardDialog.close());
leaderboardDialog.addEventListener("click", (event) => {
  if (event.target === event.currentTarget) leaderboardDialog.close();
});
$("#leaderboardTournament").addEventListener("change",event=>{
  leaderboardTournamentId=Number(event.target.value);
  refreshLeaderboard();
});
$("#leaderboardMode").addEventListener("change", () => {
  $("#leaderboardBody").replaceChildren();
  refreshLeaderboard();
});
$("#leaderboardDailyDay").addEventListener("change", () => {
  const day = $("#leaderboardDailyDay").value;
  $("#leaderboardPeriod").textContent = day ? `Daily ngày ${day.split("-").reverse().join("/")}: điểm và số trận trong ngày; Elo chung hiện tại.` : "Toàn bộ trận Daily hợp lệ; xếp theo Elo chung hiện tại.";
});

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

$("#submitForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!token()) { openAccount("login"); showToast("Đăng nhập đội trước khi nộp bài.", true); return; }
  const data = Object.fromEntries(new FormData(form));
  try {
    const encoded = JSON.stringify(data);
    if (!pendingSubmission || pendingSubmission.body !== encoded) pendingSubmission = {body:encoded, key:crypto.randomUUID()};
    const result = await request("/api/submissions", { method: "POST", headers:{"Idempotency-Key":pendingSubmission.key}, body:encoded });
    pendingSubmission = null;
    message(form, `Đã nhận bài #${result.submission_id}; trạng thái hiện tại: ${result.status}.`);
    refreshDashboard();
    refreshTournament();
  } catch (error) { message(form, error.message, true); }
});

const statusText = { queued: "Đang chờ", running: "Đang xử lý", ready: "Sẵn sàng", failed: "Lỗi" };
function dateText(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit" });
}

async function refreshDashboard() {
  if (!token()) return;
  try {
    const [subs, matches] = await Promise.all([request("/api/submissions"), request("/api/my/matches")]);
    $("#submissionsBody").innerHTML = subs.map((s) => {
      const expired = false;
      const displayStatus = expired ? "expired" : s.status;
      const displayText = expired ? "Hết hạn dự thi" : statusText[s.status] || s.status;
      return `<tr><td><strong>#${s.id}</strong><small class="row-sub">${({daily:"Daily",practice:"Practice",tournament:"Tournament"})[s.mode] || "Tournament"}</small></td><td>${s.language === "cpp" ? "C++17" : "Python"}</td><td>${dateText(s.created_at)}</td><td><span class="pill ${displayStatus}">${displayText}</span>${s.compile_error ? `<small class="row-error" title="Lỗi biên dịch">${escapeHtml(s.compile_error)}</small>` : ""}</td></tr>`;
    }).join("");
    $("#submissionEmpty").classList.toggle("hidden", subs.length > 0);
    $("#matchesBody").innerHTML = matches.map((m) => `<tr><td><strong>#${m.id}</strong><small class="row-sub">${({practice:"Practice",daily:"Daily",tournament:"Tournament"})[m.kind] || escapeHtml(m.kind)} · ${escapeHtml(m.event_day || "")}</small></td><td>${escapeHtml(m.opponent)}</td><td><span class="pill ${m.result}">${({ win: "Thắng", loss: "Thua", draw: "Hòa", void: "Hủy do lỗi hệ thống", pending: "Đang chờ", failed: "Lỗi hạ tầng" })[m.result] || m.status}</span>${m.void_reason ? `<small class="row-error">${escapeHtml(m.void_reason)}</small>` : ""}${m.status === "done" ? `<button class="match-replay-link" type="button" data-replay-id="${m.id}">Xem lại trận ↗</button>` : m.kind === "practice" && m.status === "failed" ? `<button class="match-replay-link" type="button" data-practice-retry="${m.id}">Chạy lại Practice ↻</button>` : ""}</td><td>${m.kind === "practice" ? "—" : Number(m.points).toLocaleString("vi-VN")}</td></tr>`).join("");
    $("#matchesEmpty").classList.toggle("hidden", matches.length > 0);
    await refreshPracticeState();
    if (typeof refreshDaily === "function") await refreshDaily();
  } catch (error) {
    if (/token|401/i.test(error.message)) { sessionStorage.removeItem(tokenKey); paintSession(); }
  }
}

$("#matchesBody").addEventListener("click", (event) => {
  const button = event.target.closest("[data-replay-id]");
  if (button) openReplay(Number(button.dataset.replayId));
  const retry = event.target.closest("[data-practice-retry]");
  if (retry) retryPractice(Number(retry.dataset.practiceRetry));
});

let practiceOpponentsLoaded = false;
let pendingPractice = null;
async function loadPracticeOpponents() {
  if (practiceOpponentsLoaded) return;
  const opponents = await request("/api/practice/opponents");
  $("#practiceForm [name=opponent_id]").innerHTML = opponents.map(o => `<option value="${escapeHtml(o.id)}">${escapeHtml(o.name)} — ${escapeHtml(o.description)}</option>`).join("");
  practiceOpponentsLoaded = true;
}
async function refreshPracticeState() {
  const button = $("#practiceSubmit");
  if (!token()) {
    $("#practiceQuota").textContent = "Đăng nhập và chọn bot ready để bắt đầu.";
    button.disabled = true;
    return;
  }
  try {
    await loadPracticeOpponents();
    const state = await request("/api/practice/state");
    const select = $("#practiceForm [name=submission_id]");
    const selected = select.value;
    select.innerHTML = state.submissions.length
      ? state.submissions.map(s => `<option value="${s.id}">#${s.id} · ${s.language === "cpp" ? "C++17" : "Python"} · ${dateText(s.created_at)}</option>`).join("")
      : '<option value="">Chưa có bot ready</option>';
    if (state.submissions.some(s => String(s.id) === selected)) select.value = selected;
    $("#practiceQuota").textContent = `Đã dùng ${state.used}/${state.limit} trận hôm nay · Còn ${state.remaining}.`;
    const active = $("#practiceActive");
    if (state.active_match) {
      active.textContent = `Practice #${state.active_match.id}: ${state.active_match.status === "running" ? "đang chạy" : "đang chờ worker"}.`;
      active.classList.remove("hidden");
    } else active.classList.add("hidden");
    button.disabled = !state.submissions.length || state.remaining <= 0 || Boolean(state.active_match);
  } catch (error) {
    $("#practiceQuota").textContent = error.message;
    button.disabled = true;
  }
}
$("#practiceForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (!token()) { openAccount("login"); return; }
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form));
  const encoded = JSON.stringify(payload);
  if (!pendingPractice || pendingPractice.body !== encoded) pendingPractice = {body: encoded, key: crypto.randomUUID()};
  const button = $("#practiceSubmit");
  button.disabled = true;
  $("#practiceMessage").textContent = "Đang xếp trận Practice…";
  $("#practiceMessage").classList.remove("error");
  try {
    const result = await request("/api/practice", {method:"POST", headers:{"Idempotency-Key":pendingPractice.key}, body:encoded});
    pendingPractice = null;
    $("#practiceMessage").textContent = `Đã nhận trận Practice #${result.match_id}; ${result.status === "queued" ? "đang chờ worker" : result.status}.`;
    refreshDashboard();
  } catch (error) {
    $("#practiceMessage").textContent = error.message;
    $("#practiceMessage").classList.add("error");
    await refreshPracticeState();
  }
});
async function retryPractice(matchId) {
  try {
    const result = await request(`/api/practice/${matchId}/retry`, {method:"POST"});
    showToast(`Đã đưa Practice #${result.match_id} chạy lại.`);
    refreshDashboard();
  } catch (error) { showToast(error.message, true); }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function refreshLeaderboard() {
  const requestId = ++leaderboardRequestId;
  const daily = $("#leaderboardMode").value === "daily";
  $("#leaderboardTournamentFilter").classList.toggle("hidden", daily);
  $("#leaderboardDailyFilter").classList.toggle("hidden", !daily);
  $("#leaderboardDailySelf").classList.toggle("hidden", !daily);
  $("#leaderboardDailyPages").classList.toggle("hidden", !daily);
  $("#leaderboardColumns").innerHTML = (daily ? ["Hạng", "Đội", "Elo", "Điểm Daily", "Số trận"] : ["Hạng", "Đội", "Số trận", "Trận thắng", "Elo"]).map(text => `<th>${text}</th>`).join("");
  $("#leaderboardIntro").textContent = daily
    ? "Daily xếp theo Elo chung với Tournament. Điểm Daily để tham khảo; ngày theo giờ Việt Nam (UTC+7)."
    : "Chọn giải để xem đội và kết quả riêng của giải đó. Elo trong mỗi giải bắt đầu từ 1000, hệ số điều chỉnh 32; BYE và kết quả hành chính không tính Elo.";
  try {
    if (daily) {
      const day = $("#leaderboardDailyDay").value;
      $("#leaderboardPeriod").textContent = day ? `Daily ngày ${day.split("-").reverse().join("/")}: điểm và số trận trong ngày; Elo chung hiện tại.` : "Toàn bộ trận Daily hợp lệ; xếp theo Elo chung hiện tại.";
      $("#leaderboardEmpty").classList.add("hidden");
      await modalDailyLeaderboard.refresh();
      return;
    }
    const history=await request("/api/tournaments");
    if(requestId!==leaderboardRequestId)return;
    const select=$("#leaderboardTournament");
    select.innerHTML=history.map(t=>`<option value="${t.id}">${escapeHtml(t.name)} (#${t.id})</option>`).join("");
    select.disabled=!history.length;
    if(!history.length) {
      leaderboardTournamentId=null;
      select.innerHTML='<option value="">Chưa có giải</option>';
      $("#leaderboardBody").replaceChildren();
      $("#leaderboardPeriod").textContent="Ban tổ chức chưa mở giải Tournament.";
      $("#leaderboardEmpty").textContent="Chưa có dữ liệu xếp hạng.";
      $("#leaderboardEmpty").classList.remove("hidden");
      return;
    }
    if(!history.some(t=>t.id===leaderboardTournamentId))leaderboardTournamentId=history[0].id;
    select.value=leaderboardTournamentId;
    const selected=history.find(t=>t.id===leaderboardTournamentId);
    $("#leaderboardPeriod").textContent=`${selected.name} (#${selected.id}): chỉ tính encounter của giải này; Elo khởi điểm 1000.`;
    const rows = await request(`/api/leaderboard?tournament_id=${leaderboardTournamentId}`);
    if (requestId !== leaderboardRequestId) return;
    $("#leaderboardBody").innerHTML = rows.map((r, i) => `<tr><td><span class="rank-badge${i < 3 ? " top" : ""}">${i + 1}</span></td><td><strong>${escapeHtml(r.name)}</strong></td><td>${r.matches}</td><td>${r.wins}</td><td><strong>${Number(r.points).toLocaleString("vi-VN")}</strong></td></tr>`).join("");
    $("#leaderboardEmpty").textContent = "Giải này chưa có đội tham gia.";
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
  if (typeof refreshTournament === "function") await refreshTournament();
}

async function init() {
  paintSession();
  setMode(sessionStorage.getItem("ottv2_mode") || "tournament");
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
  const replayId = Number(new URLSearchParams(location.search).get("replay"));
  if (Number.isSafeInteger(replayId) && replayId > 0) openReplay(replayId);
}

init();
setInterval(() => { if (token()) refreshDashboard(); refreshLeaderboard(); refreshEventStatus(); }, 30000);
