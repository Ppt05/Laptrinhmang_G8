const $ = selector => document.querySelector(selector);
const sessionKey = "ottv2_admin_session";
let adminToken = sessionStorage.getItem(sessionKey) || "";
let sessionExpiryTimer = null;
let tournamentId = null;
let tournament = null;
let startingBatch = false;
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const vnDate = value => value ? new Date(value).toLocaleString("vi-VN",{timeZone:"Asia/Ho_Chi_Minh"}) : "—";

function setMessage(text,error=false) { $("#adminMessage").textContent=text; $("#adminMessage").classList.toggle("error",error); }
function setSignedIn(value) { $("#adminLoginPanel").classList.toggle("hidden",value); $("#adminControls").classList.toggle("hidden",!value); }
function expireSession() {
  if(!adminToken) return;
  alert("Phiên đăng nhập đã hết hạn hoặc không còn hợp lệ. Vui lòng đăng nhập lại.");
  logout();
  $("#loginMessage").textContent="Phiên đăng nhập đã hết hạn hoặc không còn hợp lệ. Vui lòng đăng nhập lại.";
}
function scheduleSessionExpiry() {
  clearTimeout(sessionExpiryTimer);
  sessionExpiryTimer=null;
  if(!adminToken) return;
  let expiresAt;
  try {
    const payload=adminToken.split(".")[0].replace(/-/g,"+").replace(/_/g,"/");
    expiresAt=Number(JSON.parse(atob(payload.padEnd(Math.ceil(payload.length/4)*4,"="))).exp)*1000;
  } catch { return; }
  if(!Number.isFinite(expiresAt)) return;
  const remaining=expiresAt-Date.now();
  if(remaining<=0) expireSession();
  else sessionExpiryTimer=setTimeout(scheduleSessionExpiry,Math.min(remaining,2147483647));
}
async function api(url,options={}) {
  const requestToken=adminToken;
  const headers={...options.headers};
  if(requestToken) headers.Authorization=`Bearer ${requestToken}`;
  if(options.body) headers["Content-Type"]="application/json";
  const response=await fetch(url,{...options,headers});
  if(response.status===401 && requestToken && requestToken===adminToken && url!=="/api/admin/login") expireSession();
  const data=await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function round() { return tournament?.rounds.find(r=>r.id===Number($("#adminRound").value)); }
function route() { if(!tournamentId) throw new Error("Chọn giải trước."); return `/api/admin/tournaments/${tournamentId}`; }
function roundRoute() { const r=round(); if(!r) throw new Error("Chọn vòng trước."); return `${route()}/rounds/${r.id}`; }
function forceOptions(forced) { return forced ? {force:true,force_reason:$("#forceReason").value.trim()} : {}; }
async function act(work, messageSelector=null) {
  try { await work(); setMessage("Đã lưu thao tác."); await refresh(); }
  catch(error) {
    setMessage(error.message,true);
    if(messageSelector) $(messageSelector).textContent=error.message;
  }
}

function localInput(value) {
  if(!value) return "";
  return new Date(Date.parse(value)+7*3600000).toISOString().slice(0,16);
}
function defaultDates(now=Date.now()) {
  const day=new Date(now+7*3600000);
  day.setUTCHours(0,0,0,0);
  day.setUTCDate(day.getUTCDate()+1);
  const opens_at=`${day.toISOString().slice(0,10)}T00:00`;
  day.setUTCDate(day.getUTCDate()+1);
  return {opens_at,closes_at:`${day.toISOString().slice(0,10)}T23:59`};
}
function inputTime(value) { if(!value) throw new Error("Cần giờ mở và đóng."); return new Date(`${value}:00+07:00`).toISOString(); }
function config() {
  const data=Object.fromEntries(new FormData($("#tournamentConfig")));
  data.max_group_size=Number(data.max_group_size); data.qualifiers=Number(data.qualifiers);
  data.auto_run=$("#tournamentConfig").elements.auto_run.checked;
  data.auto_submission_minutes=Number(data.auto_submission_minutes);
  data.registration_open=inputTime(data.registration_open); data.registration_close=inputTime(data.registration_close);
  return data;
}

function paintNotices() {
  const notices=tournament?.notices||[];
  $("#publishNotice").disabled=!tournamentId;
  $("#clearNotices").disabled=!notices.length;
  $("#adminNotices").innerHTML=notices.length ? notices.map(n=>`<article class="admin-notice">
    <p>${vnDate(n.created_at)} (UTC+7) · ${escapeHtml(n.kind)} · ${escapeHtml(n.actor)} · #${n.id}</p>
    <p class="notice-text">${escapeHtml(n.reason)}</p>
    <button class="button button-outline button-small" type="button" data-remove-notice="${n.id}">Gỡ</button></article>`).join("") : "<p>Chưa có thông báo công khai.</p>";
}

function blockingHold(r, encounter=null) {
  return (tournament?.holds||[]).some(h=>
    (h.round_id==null||h.round_id===r?.id) &&
    (!encounter||h.encounter_id==null||h.encounter_id===encounter.id) &&
    (h.appeal_until ? Date.parse(h.appeal_until)>Date.now() : !h.resolved_at));
}

function encounterNeedsForce(r, e) {
  return e.status==="provisional" && ((!r.level&&Date.parse(e.appeal_until)>Date.now())||blockingHold(r,e));
}

function paintRound() {
  const r=round();
  $("#roundState").textContent=r ? `${r.level ? tournamentRoundName(tournament, r.level) : "Vòng bảng và phân hạng"} · ${r.status} · ${vnDate(r.opens_at)} – ${vnDate(r.closes_at)} (UTC+7). ${r.level ? "Chốt kết quả ngay khi đủ ván và không có hold; chỉ mở nộp vòng sau khi đủ suất đi tiếp." : `Hạn chốt phân hạng: ${vnDate(r.appeal_until)}`}` : "Chưa xác nhận vòng.";
  const form=$("#windowForm");
  const key=r?`${tournamentId}:${r.id}`:"none";
  const schedule=JSON.stringify([r?.opens_at,r?.closes_at]);
  if(form.dataset.loaded!==key||form.dataset.schedule!==schedule) {
    const dates=defaultDates();
    $("#windowOpen").value=localInput(r?.opens_at)||dates.opens_at;
    $("#windowClose").value=localInput(r?.closes_at)||dates.closes_at;
    form.dataset.loaded=key;
    form.dataset.schedule=schedule;
  }
  const entries=tournament?.encounters.filter(e=>e.round_id===r?.id)||[];
  const scheduled=entries.filter(e=>e.status==="scheduled").map(e=>e.batch);
  const nextBatch=scheduled.length?Math.min(...scheduled):null;
  $("#nextBatch").textContent=nextBatch===null ? "Không còn lượt chưa chạy." : `Lượt tiếp theo: ${nextBatch}`;
  const reasons=[];
  if(r?.status==="submission"&&Date.parse(r.opens_at)>Date.now()) reasons.push("Chưa tới giờ mở nộp bot. Có thể Force mở nộp ngay; giữ giờ đóng đã chọn.");
  if(r?.status==="submission"&&Date.parse(r.closes_at)<Date.now()) reasons.push("Cửa sổ nộp đã đóng. Chọn giờ đóng mới trong tương lai, rồi Force mở nộp ngay.");
  if(r?.status==="submission") reasons.push("Khóa bot thường cần hết giờ đóng. Force khóa bot ngay sẽ đóng sớm; vẫn chờ mọi bài đã nhận được kiểm tra xong.");
  if(entries.some(e=>e.status==="paused")) reasons.push("Có lỗi hạ tầng: retry ván bị lỗi trước khi chốt.");
  if(!r?.level&&entries.some(e=>e.status==="provisional")) reasons.push("Kết quả tạm cần hết 30 phút và không có khiếu nại đang mở.");
  if(tournament?.holds.some(h=>h.appeal_until ? Date.parse(h.appeal_until)>Date.now() : !h.resolved_at)) reasons.push("Có hold hoặc hạn khiếu nại chưa hết; xem nhật ký để lấy ID.");
  $("#roundBlocked").textContent=reasons.join(" ");
  $("#windowForm button[type=submit]").disabled=r?.status!=="planned";
  const roundHeld=blockingHold(r);
  const windowBlocked=Boolean(r&&["planned","submission"].includes(r.status)&&
    (roundHeld||(r.status==="submission"&&(Date.parse(r.opens_at)>Date.now()||Date.parse(r.closes_at)<Date.now()))));
  $("#forceOpenWindow").classList.toggle("hidden",!windowBlocked);
  $("#forceOpenWindow").disabled=!windowBlocked;
  $("#forceOpenWindow").title=windowBlocked?"Mở nhận bài ngay, dùng giờ đóng trong biểu mẫu; ghi nhật ký force và giải phóng hold liên quan.":"";
  const allResults=entries.every(e=>["provisional","finalized","cancelled"].includes(e.status));
  const forced={
    "open-window":windowBlocked,
    "lock-bots":r?.status==="submission"&&(Date.parse(r.closes_at)>=Date.now()||roundHeld),
    "finalize-encounters":entries.some(e=>encounterNeedsForce(r,e)),
    "finalize":Boolean(r && allResults && (r.level ? ["locked","running"].includes(r.status) : r.status==="provisional") &&
      (roundHeld || entries.some(e=>encounterNeedsForce(r,e)) || (!r.level && Date.parse(r.appeal_until)>Date.now()))),
  };
  document.querySelectorAll("[data-round-action], [data-force-round-action]").forEach(button=>{
    const action=button.dataset.roundAction||button.dataset.forceRoundAction;
    const allowed={"lock-bots":["submission"],"start-batch":["locked","running"],"qualify":["locked","running"],"finalize":["provisional","locked","running"],"finalize-encounters":["locked","running","provisional"]};
    button.disabled=!r||!allowed[action].includes(r.status)||(action==="qualify"&&r.level!==0);
    if(action==="start-batch") button.disabled=button.disabled||nextBatch===null||startingBatch;
    if(button.dataset.forceRoundAction) button.classList.toggle("hidden",!forced[action]||button.disabled);
  });
  $("#forceControls").classList.toggle("hidden",!Object.values(forced).some(Boolean));
  $("#adminEncounters").innerHTML=entries.map(e=>`<details><summary>#${e.id} · ${escapeHtml(e.team_a)} — ${escapeHtml(e.team_b)} · ${tournamentRoundName(tournament, e.level)} · Lượt ${e.batch}${e.playoff ? " / phân hạng" : ""} · ${e.status}</summary>
    <p>Điểm ván chính: ${e.game_points.join(" – ")} · Điểm trận: ${e.encounter_points.join(" – ")} · Cặp phụ: ${escapeHtml(JSON.stringify(e.extra_scores))}. ${e.administrative ? "Hành chính, không Elo." : ""} ${e.excluded ? "Loại khỏi điểm bảng." : ""}</p>
    <p>${escapeHtml(e.reason)} · ${e.level ? "Chốt ngay khi đủ ván và không có hold." : `Hạn khiếu nại ${vnDate(e.appeal_until)} (UTC+7).`}</p>
    ${e.status==="provisional"?`<button class="button button-outline" type="button" data-finalize="${e.id}">Chốt encounter #${e.id}</button>${encounterNeedsForce(r,e)?`<button class="button button-outline force-button" type="button" data-force-finalize="${e.id}">Force chốt encounter #${e.id}</button>`:""}`:""}
    ${e.games.map(g=>`<p>Ván #${g.id} (${g.number}) · Nam đội ${g.south}, bot #${g.submission_south} · Bắc đội ${g.north}, bot #${g.submission_north} · ${g.status}${g.status==="done"?` · <a href="/api/matches/${g.id}" target="_blank" rel="noopener">Replay JSON</a>`:""}</p>`).join("")}</details>`).join("");
}

async function refresh() {
  if(!adminToken) return;
  const history=await api("/api/tournaments");
  if(!history.some(t=>t.id===tournamentId)) tournamentId=history[0]?.id||null;
  $("#adminTournament").innerHTML=history.map(t=>`<option value="${t.id}">${escapeHtml(t.name)} · ${t.status}</option>`).join("");
  $("#adminTournament").value=tournamentId;
  if(tournamentId) {
    tournament=await api(`/api/tournaments/${tournamentId}`);
    const t=tournament;
    $("#adminState").textContent=`${t.name} · ${t.status} · ${t.roster.length} đội${t.champion ? ` · Vô địch ${t.champion}` : ""}`;
    const form=$("#tournamentConfig");
    if(form.dataset.loaded!==String(t.id)) {
      const dates=defaultDates();
      for(const name of ["name","max_group_size","qualifiers","appeal_contact","incident_policy"]) form.elements[name].value=t[name];
      form.elements.auto_run.checked=t.auto_run??false;
      form.elements.auto_submission_minutes.value=t.auto_submission_minutes??1440;
      form.elements.registration_open.value=localInput(t.registration_open)||dates.opens_at;
      form.elements.registration_close.value=localInput(t.registration_close)||dates.closes_at;
      form.dataset.loaded=t.id;
    }
    $("#saveConfiguration").disabled=t.status!=="registration";
    const ended=["complete","cancelled"].includes(t.status);
    $("#saveAutomation").disabled=ended;
    $("#automationState").textContent=t.auto_run?`Tự động: bật. ${t.auto_status||"Worker sẽ xử lý khi đến hạn."}`:"Tự động: tắt. BTC điều hành bằng các nút bên dưới.";
    $("#closeTournament").disabled=ended;
    $("#closeTournamentReason").disabled=ended;
    const rosterCount=t.roster.filter(player=>player.status==="enrolled").length;
    const allowed={"lock-roster":"registration","confirm-groups":"allocated","confirm-bracket":"qualified"};
    document.querySelectorAll("[data-action]").forEach(b=>{
      const action=b.dataset.action;
      b.disabled=t.status!==allowed[action] || (action==="lock-roster"&&rosterCount<2);
    });
    const guidance={
      registration:rosterCount<2
        ? `Chưa thể lập bảng: hiện có ${rosterCount} đội tham gia, cần ít nhất 2 đội. Tạo thêm tài khoản đội, đăng nhập trang thi và bấm “Tham gia giải”.`
        : `Đã có ${rosterCount} đội tham gia. Có thể bấm “Khóa đăng ký / chia bảng” ngay, không cần chờ hạn đóng. ${rosterCount===2 ? "Giải hai đội dùng một bảng; cả hai đội còn đủ điều kiện đi tiếp vào chung kết." : `Hệ thống kiểm tra giới hạn ${t.max_group_size} đội mỗi bảng và ${t.qualifiers} suất đi tiếp mỗi bảng trước khi lập bảng.`}`,
      allocated:"Danh sách đăng ký đã khóa. Kiểm tra chia bảng bên dưới, rồi bấm “Xác nhận bảng”.",
      groups:"Bảng đã xác nhận. Mở cửa sổ nộp bot trong phần Điều hành vòng thi.",
      qualified:"Vòng bảng đã chốt. Kiểm tra các quyết định và khiếu nại trước khi xác nhận nhánh.",
      knockout:"Nhánh đã xác nhận. Điều hành từng vòng loại trực tiếp trong phần Điều hành vòng thi.",
      complete:"Giải đã kết thúc.", cancelled:"Giải đã hủy.",
    };
    $("#tournamentBlocked").textContent=guidance[t.status]||`Trạng thái ${t.status}.`;
    const names=Object.fromEntries(t.roster.map(p=>[p.player_id,p.name]));
    $("#adminRoster").innerHTML=`<p>${t.roster.map(p=>`#${p.player_id} ${escapeHtml(p.name)} (${p.status}; Elo khóa ${p.frozen_elo??"—"})`).join("<br>")}</p>${t.allocation?`<p>Bảng dự kiến: ${t.allocation.groups.map(ids=>ids.map(id=>escapeHtml(names[id])).join(", ")).join(" | ")}</p>`:""}<pre>${escapeHtml(JSON.stringify(t.bracket,null,2))}</pre>`;
    const oldRound=$("#adminRound").value;
    $("#adminRound").innerHTML=t.rounds.map(r=>`<option value="${r.id}">${tournamentRoundName(t, r.level)} · ${r.status} (#${r.id})</option>`).join("");
    $("#adminRound").value=t.rounds.some(r=>r.id===Number(oldRound))?oldRound:t.rounds.at(-1)?.id;
    paintRound();
    paintNotices();
    const audit=await api(`${route()}/audit`);
    $("#adminAudit").textContent=JSON.stringify(audit,null,2);
  } else { tournament=null; paintNotices(); }
  const workers=await api("/api/admin/workers");
  const online=workers.filter(w=>w.online).map(w=>w.id);
  $("#workersState").textContent=`Trực tuyến: ${online.join(", ")||"Không có worker."}`;
}

function logout() {
  clearTimeout(sessionExpiryTimer); sessionExpiryTimer=null;
  adminToken=""; sessionStorage.removeItem(sessionKey); setSignedIn(false);
  $("#adminPassword").value=""; $("#loginMessage").textContent="";
}
$("#adminLoginForm").addEventListener("submit",async event=>{
  event.preventDefault();
  try {
    const data=await api("/api/admin/login",{method:"POST",body:JSON.stringify({username:$("#adminUsername").value.trim(),password:$("#adminPassword").value})});
    adminToken=data.access_token; sessionStorage.setItem(sessionKey,adminToken); $("#adminPassword").value=""; $("#loginMessage").textContent=""; setSignedIn(true); scheduleSessionExpiry(); await refresh();
  } catch(error) { $("#loginMessage").textContent=error.message; }
});
$("#adminLogout").addEventListener("click",logout);
$("#adminRefresh").addEventListener("click",()=>act(refresh));
$("#adminTournament").addEventListener("change",event=>{tournamentId=Number(event.target.value); $("#tournamentConfig").dataset.loaded=""; $("#adminRound").replaceChildren(); act(refresh);});
$("#adminRound").addEventListener("change",paintRound);
$("#tournamentConfig").addEventListener("submit",event=>{event.preventDefault(); act(async()=>{const data=await api("/api/admin/tournaments",{method:"POST",body:JSON.stringify(config())});tournamentId=data.id;});});
$("#saveConfiguration").addEventListener("click",()=>act(()=>api(`${route()}/configuration`,{method:"PUT",body:JSON.stringify(config())})));
$("#saveAutomation").addEventListener("click",()=>act(()=>api(`${route()}/automation`,{method:"PUT",body:JSON.stringify({auto_run:$("#tournamentConfig").elements.auto_run.checked,auto_submission_minutes:Number($("#tournamentConfig").elements.auto_submission_minutes.value)})}),"#automationState"));
$("#closeTournamentForm").addEventListener("submit",event=>{
  event.preventDefault();
  act(async()=>{
    await api(`${route()}/close`,{method:"POST",body:JSON.stringify({reason:$("#closeTournamentReason").value})});
    $("#closeTournamentReason").value="";
  },"#tournamentBlocked");
});
$("#noticeForm").addEventListener("submit",event=>{
  event.preventDefault();
  const message=$("#noticeText").value, kind=$("#noticeKind").value;
  if(!message.trim()) { $("#noticeMessage").textContent=""; return; }
  act(async()=>{
    await api(`${route()}/notices`,{method:"POST",body:JSON.stringify({message,kind})});
    $("#noticeText").value="";
    $("#noticeMessage").textContent="Đã đăng thông báo.";
  },"#noticeMessage");
});
$("#clearNotices").addEventListener("click",()=>act(async()=>{
  const data=await api(`${route()}/notices`,{method:"DELETE"});
  $("#noticeMessage").textContent=`Đã gỡ ${data.cleared} thông báo.`;
},"#noticeMessage"));
$("#adminNotices").addEventListener("click",event=>{
  const button=event.target.closest("[data-remove-notice]");
  if(button) act(async()=>{
    await api(`${route()}/notices/${button.dataset.removeNotice}`,{method:"DELETE"});
    $("#noticeMessage").textContent="Đã gỡ thông báo.";
  },"#noticeMessage");
});
$("#tournamentActions").addEventListener("click",event=>{
  const b=event.target.closest("[data-action]");
  if(b) act(()=>api(`${route()}/${b.dataset.action}`,{method:"POST"}),"#tournamentBlocked");
});
$("#windowForm").addEventListener("submit",event=>{event.preventDefault();act(()=>api(`${roundRoute()}/window`,{method:"PUT",body:JSON.stringify({opens_at:inputTime($("#windowOpen").value),closes_at:inputTime($("#windowClose").value)})}));});
$("#forceOpenWindow").addEventListener("click",()=>act(()=>api(`${roundRoute()}/window`,{method:"PUT",body:JSON.stringify({opens_at:new Date().toISOString(),closes_at:inputTime($("#windowClose").value),...forceOptions(true)})}),"#roundBlocked"));
$("#roundActions").addEventListener("click",async event=>{
  const b=event.target.closest("[data-round-action], [data-force-round-action]");
  if(!b||b.disabled) return;
  const action=b.dataset.roundAction||b.dataset.forceRoundAction;
  if(action==="start-batch") { startingBatch=true; b.disabled=true; }
  try {
    await act(()=>api(`${roundRoute()}/${action}`,{method:"POST",body:JSON.stringify(forceOptions(Boolean(b.dataset.forceRoundAction)))}),"#roundBlocked");
  } finally {
    if(action==="start-batch") { startingBatch=false; paintRound(); }
  }
});
$("#adminEncounters").addEventListener("click",event=>{
  const b=event.target.closest("[data-finalize], [data-force-finalize]");
  if(b) act(()=>api(`${roundRoute()}/finalize-encounters`,{method:"POST",body:JSON.stringify({encounter_id:Number(b.dataset.finalize||b.dataset.forceFinalize),...forceOptions(Boolean(b.dataset.forceFinalize))})}),"#roundBlocked");
});
$("#exceptionForm").addEventListener("submit",event=>{
  event.preventDefault();
  const values=Object.fromEntries(new FormData(event.currentTarget));
  act(()=>{
    const players=values.players.split(",").map(x=>Number(x.trim())).filter(x=>x>0);
    const body={reason:values.reason,evidence:values.evidence,scope:values.scope,south_score:Number(values.south_score),player_id:players[0],player_ids:players};
    if(values.encounter_id)body.encounter_id=Number(values.encounter_id);
    if(values.action_id)body.action_id=Number(values.action_id);
    if(values.target_id)body[values.kind==="compile_retry"?"submission_id":"match_id"]=Number(values.target_id);
    const url=values.kind==="withdraw"&&!round()?`${route()}/enrollments/${players[0]}/withdraw`:`${roundRoute()}/exceptions/${values.kind}`;
    return api(url,{method:"POST",body:JSON.stringify(body)});
  });
});
const dates=defaultDates();
for(const selector of ['[name="registration_open"]',"#windowOpen"]) {
  if(!$(selector).value) $(selector).value=dates.opens_at;
}
for(const selector of ['[name="registration_close"]',"#windowClose"]) {
  if(!$(selector).value) $(selector).value=dates.closes_at;
}
setSignedIn(Boolean(adminToken));
scheduleSessionExpiry();
window.addEventListener("focus",scheduleSessionExpiry);
document.addEventListener("visibilitychange",()=>{if(!document.hidden)scheduleSessionExpiry();});
if(adminToken)refresh().catch(error=>setMessage(error.message,true));
setInterval(()=>{if(adminToken&&!document.querySelector("form :focus"))refresh().catch(error=>setMessage(error.message,true));},15000);
