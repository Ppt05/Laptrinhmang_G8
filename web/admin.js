const $ = (selector) => document.querySelector(selector);
let adminKey = "";

function localInput(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function setMessage(text, error = false) {
  const output = $("#adminMessage");
  output.textContent = text;
  output.classList.toggle("error", error);
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { ...(options.headers || {}), "x-admin-key": adminKey, ...(options.body ? { "Content-Type": "application/json" } : {}) },
  });
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
    not_scheduled: "Chưa mở lịch thi",
  };
  $("#eventState").textContent = labels[event.status] || "Chưa rõ trạng thái";
  const badge = $("#stateBadge");
  badge.textContent = event.status === "open" ? "ĐANG MỞ" : event.status === "scheduled" ? "ĐÃ LÊN LỊCH" : event.status === "worker_unavailable" ? "CHƯA CÓ WORKER" : "ĐÃ ĐÓNG";
  badge.className = `state-badge ${event.status}`;
  $("#workerNote").textContent = event.worker_enabled
    ? "Worker chạy bot đã được bật trên máy chủ."
    : "Lịch điều khiển đã sẵn sàng; máy chủ Render hiện chưa có worker sandbox để thực thi bot và ghi kết quả trận.";
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

async function loadSchedule() {
  adminKey = $("#adminKey").value.trim();
  if (!adminKey) return setMessage("Nhập khóa quản trị trước.", true);
  try {
    const data = await api("/api/admin/event");
    $("#startAt").value = localInput(data.start_at);
    $("#endAt").value = localInput(data.end_at);
    showState(data.event);
    setMessage("Đã tải lịch từ máy chủ.");
  } catch (error) { setMessage(error.message, true); }
}

async function saveSchedule(enabled) {
  adminKey = $("#adminKey").value.trim();
  if (!adminKey) return setMessage("Nhập khóa quản trị trước.", true);
  try {
    const schedule = readSchedule();
    const data = await api("/api/admin/event", { method: "PUT", body: JSON.stringify({ ...schedule, enabled }) });
    showState(data.event);
    setMessage(enabled ? "Đã lưu lịch; hệ thống sẽ mở/đóng theo hai mốc giờ này." : "Đã đóng lịch thi đấu.");
  } catch (error) { setMessage(error.message, true); }
}

$("#loadSchedule").addEventListener("click", loadSchedule);
$("#adminForm").addEventListener("submit", (event) => { event.preventDefault(); saveSchedule(true); });
$("#closeEvent").addEventListener("click", () => saveSchedule(false));
$("#adminKey").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); loadSchedule(); } });
