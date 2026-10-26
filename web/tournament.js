// Tournament public views reuse the existing account and replay controls.
let activeTournament = null;
let selectedTournamentId = null;
let pendingSubmission = null;
const tournamentLabels = {
  registration: "Đang đăng ký", allocated: "Chờ xác nhận bảng", groups: "Vòng bảng",
  qualified: "Đã chốt bảng", knockout: "Knockout", complete: "Đã kết thúc", cancelled: "Đã hủy",
  planned: "Chưa mở", submission: "Đang nhận bot", locked: "Đã khóa bot", running: "Đang thi",
  provisional: "Kết quả tạm / chờ khiếu nại", finalized: "Đã chốt", pending: "Đang chờ", paused: "Hoãn do hạ tầng",
  scheduled: "Chưa chạy", enrolled: "Đã tham gia", withdrawn: "Đã rút", disqualified: "Truất quyền",
};
const label = value => tournamentLabels[value] || value || "—";
const vnDate = value => value ? new Date(value).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "—";

function setSubmissionBlock(reason) {
  const button=$("#submitForm button[type=submit]");
  const help=$("#submitButtonHelp");
  button.disabled=Boolean(reason);
  button.title=help.title=reason;
  help.tabIndex=reason?0:-1;
  $("#submitBlockedReason").textContent=reason;
  $("#submitBlockedReason").classList.toggle("hidden",!reason);
}

async function refreshTournament() {
  try {
    const history = await request("/api/tournaments");
    const select = $("#tournamentSelect");
    select.innerHTML = history.map(t => `<option value="${t.id}">${escapeHtml(t.name)} · ${escapeHtml(label(t.status))}</option>`).join("");
    if (!history.length) {
      activeTournament = null;
      setSubmissionBlock("Ban tổ chức chưa mở giải Tournament.");
      $("#tournamentSummary").textContent = "Ban tổ chức chưa mở giải Tournament.";
      $("#eventStatus").textContent = "Chưa mở giải Tournament. Daily và Challenge đang tắt.";
      return;
    }
    if (!history.some(t => t.id === selectedTournamentId)) selectedTournamentId = history[0].id;
    select.value = selectedTournamentId;
    const t = await request(`/api/tournaments/${selectedTournamentId}`);
    // An older polling request must not overwrite a newly selected history.
    if (t.id !== selectedTournamentId) return;
    activeTournament = t;
    const current = t.rounds.at(-1);
    $("#tournamentSummary").innerHTML = `<h3>${escapeHtml(t.name)} · ${escapeHtml(label(t.status))}</h3>
      <p>${t.roster.length} đội · Tối đa ${t.max_group_size} đội mỗi bảng · ${t.qualifiers} suất đi tiếp mỗi bảng. Đăng ký: ${vnDate(t.registration_open)} – ${vnDate(t.registration_close)} (UTC+7).</p>
      <p>${current ? `${tournamentRoundName(t, current.level)} · ${escapeHtml(current.paused?"Đã dừng":label(current.status))}. Cửa sổ bot: ${vnDate(current.opens_at)} – ${vnDate(current.closes_at)} (UTC+7).` : "Chưa xác nhận bảng."}</p>
      <p>Khiếu nại: ${escapeHtml(t.appeal_contact)}. Vòng bảng: 30 phút sau công bố đủ dữ liệu. Loại trực tiếp: thi liên tục, chốt ngay nếu không có hold; mở nộp vòng sau khi đủ suất đi tiếp. ${escapeHtml(t.incident_policy)}</p>
      ${t.auto_run?`<p>Tự động điều hành: ${escapeHtml(t.auto_status||"Worker sẽ xử lý các mốc khi đến hạn.")}</p>`:""}
      <p>${t.champion ? `Vô địch: ${escapeHtml(t.champion)}` : t.result === "no_champion" ? "Giải kết thúc không có vô địch." : ""}</p>
      ${t.notices?.length ? `<details id="tournamentNotices" open><summary>Thông báo BTC, hoãn lịch và quyết định</summary>${t.notices.map(n=>`<p class="notice-text">${vnDate(n.created_at)} (UTC+7) · ${escapeHtml(n.kind)} · ${escapeHtml(n.reason)}${n.appeal_until ? ` · Hạn khiếu nại: ${vnDate(n.appeal_until)}` : ""}</p>`).join("")}</details>` : ""}
      <details><summary>Luật và môi trường đã công bố</summary><p><a href="/api/tournaments/${t.id}/rules" target="_blank" rel="noopener">Luật đã khóa</a> · SHA-256: <code>${t.rules_digest}</code></p><pre>${escapeHtml(JSON.stringify(t.environment, null, 2))}</pre></details>`;
    $("#eventStatus").textContent = `${t.name}: ${label(t.status)}. ${current ? `${tournamentRoundName(t, current.level)} · Bot: ${current.paused?"Đã dừng":label(current.status)}; hạn ${vnDate(current.closes_at)} (UTC+7).` : "Đang đăng ký."}`;
    $("#tournamentGroups").innerHTML = t.groups.map(group => `<article class="panel table-panel"><h3>Bảng ${group.number} · ${group.finalized ? "Đã chốt" : "Tạm thời"}</h3><div class="table-scroll"><table><thead><tr><th>Hạng</th><th>Đội</th><th>Điểm trận</th><th>Điểm ván</th><th>Thắng</th><th>Elo</th><th>Điều kiện</th></tr></thead><tbody>${group.standings.map((row, i) => `<tr><td>${i+1}</td><td>${escapeHtml(row.name)}</td><td>${row.points}</td><td>${row.game_points}</td><td>${row.wins}</td><td>${Math.round(row.elo)}</td><td>${row.qualified ? "Đi tiếp" : row.eligible ? "Hợp lệ" : "Chưa đủ điều kiện"}</td></tr>`).join("")}</tbody></table></div></article>`).join("");
    const names = Object.fromEntries(t.roster.map(p => [p.player_id, p.name]));
    $("#tournamentBracket").innerHTML = t.bracket ? `<h3>Nhánh knockout cố định</h3><p>Hạt giống: ${t.bracket.seeds.map(id => escapeHtml(names[id])).join(", ")}</p><div class="tournament-grid">${Object.entries(t.bracket.levels).map(([level,nodes]) => `<article class="panel"><h4>${tournamentRoundName(t, level)}</h4>${nodes.map(n => `<p>${escapeHtml(names[n.a] || "Ô trống")} — ${escapeHtml(names[n.b] || "Ô trống")} ${n.bye ? "· BYE / ô trống (không điểm, không Elo)" : ""}${n.winner ? `<br>Đi tiếp: ${escapeHtml(names[n.winner])}` : ""}</p>`).join("")}</article>`).join("")}</div>` : "";
    $("#tournamentEncounters").innerHTML = t.encounters.map(e => `<details class="panel"><summary>#${e.id} · ${escapeHtml(e.team_a)} — ${escapeHtml(e.team_b)} · ${e.level ? tournamentRoundName(t, e.level) : `Bảng ${t.groups.find(g=>g.id===e.group_id)?.number}${e.playoff ? " / phân hạng" : ""}`} · Lượt ${e.batch} · ${escapeHtml(label(e.status))}</summary>
      <p>Điểm ván chính: ${e.game_points.join(" – ")}${e.level ? "" : ` · Điểm trận: ${e.encounter_points.join(" – ")}`}. ${e.extra_scores.length ? `Cặp phụ: ${e.extra_scores.map(s=>s.join(" – ")).join("; ")}.` : ""}
      ${e.administrative ? "Kết quả hành chính · không Elo." : e.playing_result === .5 ? "Thực đấu hòa (Elo)." : ""} ${e.excluded ? "Không tính vào bảng." : ""}
      ${e.winner_id ? `Thắng / đi tiếp: ${escapeHtml(names[e.winner_id])}.` : ""} ${escapeHtml(e.reason || "")}</p>
      <p>${e.level ? "Chốt ngay khi đủ ván và không có hold." : `Hạn khiếu nại: ${vnDate(e.appeal_until)} (UTC+7).`}</p>
      ${e.games.map(g => `<p>Ván ${g.number} · Nam ${escapeHtml(names[g.south])} (#${g.submission_south}) · Bắc ${escapeHtml(names[g.north])} (#${g.submission_north}) · ${escapeHtml(label(g.status))} ${g.status === "done" ? `<button class="text-button" data-replay-id="${g.id}" type="button">Replay #${g.id}: ${g.points.join(" – ")}</button>` : ""}</p>`).join("")}</details>`).join("");
    $("#tournamentDraws").innerHTML = `<summary>Bốc thăm đã ghi nhận (${t.draws.length})</summary>${t.draws.map(d=>`<p>${escapeHtml(d.reason)} · ${escapeHtml(JSON.stringify(d.result))}<br><small>${vnDate(d.created_at)} · ${escapeHtml(d.actor)} · seed ${d.seed}</small></p>`).join("")}`;
    $("#joinTournament").classList.toggle("hidden", !token() || t.status !== "registration");
    if (token()) {
      const status = await request(`/api/tournaments/${t.id}/team/status`);
      if (t.id !== selectedTournamentId) return;
      $("#joinTournament").disabled = status.enrolled;
      $("#joinTournament").textContent = status.enrolled ? "Đã tham gia giải" : "Tham gia giải";
      const reasons = {latest_ready:"Bài ready mới nhất",inherited_compatible:"Kế thừa bot Tournament tương thích",missing_compatible_tournament_bot:"Không có bot Tournament hợp lệ",awaiting_bot_lock:"Chờ khóa bot",not_enrolled:"Chưa tham gia giải"};
      $("#tournamentTeam").textContent = `${label(status.enrollment_status)} · Đã nộp ${status.attempts_used}/3 · Chọn #${status.selected_submission || "—"} · Khóa #${status.locked_submission || "—"} · ${reasons[status.eligibility_reason] || status.eligibility_reason} · Elo ${Math.round(status.elo)}`;
      setSubmissionBlock(status.can_submit ? "" : status.submission_block_reason || "Chưa thể nộp bot trong vòng này.");
    } else {
      setSubmissionBlock("Đăng nhập tài khoản đội để nộp bot.");
      $("#tournamentTeam").textContent = "Đăng nhập để tham gia và xem quota của đội.";
    }
  } catch(error) {
    $("#tournamentTeam").textContent = error.message;
    setSubmissionBlock(`Không thể kiểm tra điều kiện nộp bot: ${error.message}`);
  }
}

$("#tournamentSelect").addEventListener("change", event => { selectedTournamentId = Number(event.target.value); setSubmissionBlock("Đang kiểm tra điều kiện nộp bot."); refreshTournament(); });
$("#joinTournament").addEventListener("click", async () => {
  try { await request(`/api/tournaments/${selectedTournamentId}/join`, {method:"POST"}); await refreshTournament(); }
  catch(error) { showToast(error.message,true); }
});
$("#tournamentEncounters").addEventListener("click", event => {
  const button = event.target.closest("[data-replay-id]");
  if (button) openReplay(Number(button.dataset.replayId));
});
refreshTournament();
setInterval(refreshTournament, 15000);
