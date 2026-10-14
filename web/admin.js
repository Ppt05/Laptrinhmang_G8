const $ = (selector) => document.querySelector(selector);
const sessionKey = "ottv2_admin_session";
let adminToken = sessionStorage.getItem(sessionKey) || "";

function setMessage(selector, text, error = false) {
  const output = $(selector);
  output.textContent = text;
  output.classList.toggle("error", error);
}

function setSignedIn(signedIn) {
  $("#adminLoginPanel").classList.toggle("hidden", signedIn);
  $("#adminControls").classList.toggle("hidden", !signedIn);
}

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (adminToken) headers.Authorization = `Bearer ${adminToken}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Lỗi máy chủ (${response.status})`);
  return data;
}

function showState(event) {
  const labels = {
    open: "Đang mở thi đấu",
    scheduled: "Đã lên lịch",
    closed: "Đã đóng",
    worker_unavailable: "Đến giờ nhưng worker chưa sẵn sàng",
    not_scheduled: "Chưa mở lịch",
  };
  $("#eventState").textContent = labels[event.status] || "Chưa rõ trạng thái";
  const badge = $("#stateBadge");
  badge.textContent = event.status === "open" ? "ĐANG MỞ" : event.status === "scheduled" ? "ĐÃ LÊN LỊCH" : event.status === "worker_unavailable" ? "CHƯA CÓ WORKER" : "ĐÃ ĐÓNG";
  badge.className = `state-badge ${event.status}`;
  $("#workerNote").textContent = event.worker_enabled
    ? "Chế độ chạy trận đã bật. Danh sách máy trực tuyến nằm ở mục Phân công trận bên dưới."
    : "Lịch được lưu, nhưng chế độ chạy trận chưa bật trong cấu hình máy chủ.";
}

function replaceOptions(selector, items, previous, emptyLabel) {
  const select = $(selector);
  select.replaceChildren();
  if (!items.length) {
    const option = new Option(emptyLabel, "");
    select.add(option);
    return;
  }
  select.add(new Option("Chọn…", ""));
  items.forEach(({ value, label }) => select.add(new Option(label, String(value))));
  if (items.some(({ value }) => String(value) === previous)) select.value = previous;
}

async function loadAssignments(silent = false) {
  try {
    const [teams, workers, matches] = await Promise.all([
      api("/api/admin/teams"), api("/api/admin/workers"), api("/api/admin/matches"),
    ]);
    const ready = teams.filter((team) => team.submission_id);
    const teamOptions = ready.map((team) => ({ value: team.id, label: `${team.name} (#${team.id})` }));
    const onlineOptions = workers.filter((worker) => worker.online)
      .map((worker) => ({ value: worker.id, label: worker.id }));
    replaceOptions("#teamA", teamOptions, $("#teamA").value, "Chưa có đội có bot sẵn sàng");
    replaceOptions("#teamB", teamOptions, $("#teamB").value, "Chưa có đội có bot sẵn sàng");
    replaceOptions("#workerId", onlineOptions, $("#workerId").value, "Chưa có máy worker trực tuyến");

    const tbody = $("#assignedMatches");
    tbody.replaceChildren();
    if (!matches.length) {
      const row = tbody.insertRow();
      const cell = row.insertCell();
      cell.colSpan = 5;
      cell.textContent = "Chưa có trận nào.";
    }
    const states = { queued: "Đang chờ", running: "Đang đấu", done: "Hoàn tất", failed: "Lỗi" };
    matches.forEach((match) => {
      const row = tbody.insertRow();
      [
        `#${match.id}`,
        `${match.team_a} — ${match.team_b}`,
        match.worker_id || "Tự động",
        states[match.status] || match.status,
        match.status === "done" ? (match.winner || "Hòa") : "—",
      ].forEach((value) => { row.insertCell().textContent = value; });
    });
    if (!silent) setMessage("#assignMessage", `Có ${ready.length} đội sẵn sàng và ${onlineOptions.length} máy worker trực tuyến.`);
  } catch (error) {
    if (!silent) setMessage("#assignMessage", error.message, true);
    if (error.message.includes("Phiên đăng nhập")) logout();
  }
}

function readSchedule() {
  const startValue = $("#startAt").value;
  const endValue = $("#endAt").value;
  if (!startValue || !endValue) throw new Error("Hãy chọn thời điểm bắt đầu và kết thúc.");
  const start = new Date(startValue);
  const end = new Date(endValue);
  if (end <= start) throw new Error("Thời điểm kết thúc phải sau thời điểm bắt đầu.");
  return { start_at: start.toISOString(), end_at: end.toISOString() };
}

function localInput(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

async function loadSchedule() {
  try {
    const data = await api("/api/admin/event");
    $("#startAt").value = localInput(data.start_at);
    $("#endAt").value = localInput(data.end_at);
    showState(data.event);
    setMessage("#adminMessage", "Đã tải lịch từ máy chủ.");
  } catch (error) {
    setMessage("#adminMessage", error.message, true);
    if (error.message.includes("Phiên đăng nhập")) logout();
  }
}

async function saveSchedule(enabled) {
  try {
    const data = await api("/api/admin/event", {
      method: "PUT",
      body: JSON.stringify({ ...readSchedule(), enabled }),
    });
    showState(data.event);
    setMessage("#adminMessage", enabled
      ? "Đã lưu lịch; hệ thống sẽ mở/đóng theo thời gian này."
      : "Đã đóng lịch thi đấu.");
  } catch (error) {
    setMessage("#adminMessage", error.message, true);
  }
}

function logout() {
  adminToken = "";
  sessionStorage.removeItem(sessionKey);
  setSignedIn(false);
}

$("#adminLoginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("#loginMessage", "Đang đăng nhập…");
  try {
    const result = await api("/api/admin/login", {
      method: "POST",
      body: JSON.stringify({ username: $("#adminUsername").value.trim(), password: $("#adminPassword").value }),
    });
    adminToken = result.access_token;
    sessionStorage.setItem(sessionKey, adminToken);
    $("#adminPassword").value = "";
    setSignedIn(true);
    await loadSchedule();
    await loadAssignments();
  } catch (error) {
    setMessage("#loginMessage", error.message, true);
  }
});

$("#loadSchedule").addEventListener("click", loadSchedule);
$("#refreshAssignments").addEventListener("click", () => loadAssignments());
$("#assignForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const player_a_id = Number($("#teamA").value);
  const player_b_id = Number($("#teamB").value);
  const worker_id = $("#workerId").value;
  if (!player_a_id || !player_b_id || !worker_id) {
    setMessage("#assignMessage", "Hãy chọn đủ hai đội và một máy worker.", true);
    return;
  }
  try {
    const result = await api("/api/admin/matches", {
      method: "POST", body: JSON.stringify({ player_a_id, player_b_id, worker_id }),
    });
    await loadAssignments(true);
    setMessage("#assignMessage", `Đã giao trận #${result.match_id} cho máy ${result.worker_id}.`);
  } catch (error) {
    setMessage("#assignMessage", error.message, true);
  }
});
$("#adminForm").addEventListener("submit", (event) => { event.preventDefault(); saveSchedule(true); });
$("#closeEvent").addEventListener("click", () => saveSchedule(false));
$("#adminLogout").addEventListener("click", logout);

if (adminToken) {
  setSignedIn(true);
  loadSchedule();
  loadAssignments();
}

setInterval(() => { if (adminToken) loadAssignments(true); }, 15000);
