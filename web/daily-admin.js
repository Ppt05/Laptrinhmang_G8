let dailySaving = false;
let dailyControlVersion = 0;
let dailyVisibleMatches = 10;
let dailyVisibleDraws = 10;
let dailyRefreshVersion = 0;
const dailyStatuses = {queued:'Đang chờ', scheduled:'Đã lên lịch', running:'Đang đấu', done:'Hoàn tất', failed:'Lỗi hạ tầng', void:'Đã hủy'};
const dailyStatus = s => `<span class="daily-badge daily-badge-${escapeHtml(s)}">${escapeHtml(dailyStatuses[s] || s)}</span>`;
const dailyDayText = day => day ? day.split('-').reverse().join('/') : '—';
function paintDailyControl(enabled) {
  $('#dailyEnabled').checked = enabled;
  $('#dailyEnabled').disabled = dailySaving;
  $('#dailyControlForm').classList.toggle('is-open', enabled);
  $('#dailyControlStatus').textContent = dailySaving ? 'Đang cập nhật…' : enabled ? 'Đang mở' : 'Tạm đóng';
  $('#dailyControlHelp').textContent = enabled ? 'Nhận bài và ghép các đội đang tìm trận.' : 'Dừng nhận bài và ghép mới; các trận đã ghép vẫn tiếp tục.';
}
function paintDailyMatches(matches) {
  if (!matches.length) { $('#dailyAdminMatches').innerHTML = '<p class="daily-empty">Chưa có trận Daily.</p>'; return; }
  $('#dailyAdminMatches').innerHTML = `<div class="daily-table-wrap"><table class="daily-table daily-matches-table"><thead><tr><th>Trận</th><th>Ngày</th><th>Đội Nam</th><th>Đội Bắc</th><th>Tỷ số</th><th>Trạng thái</th><th>Thao tác</th></tr></thead><tbody>${matches.map(m => `<tr>
    <td data-label="Trận"><strong>#${m.id}</strong></td><td data-label="Ngày">${escapeHtml(dailyDayText(m.day))}</td>
    <td data-label="Đội Nam" class="${m.status==='done' && m.winner_id===m.player_a ? 'daily-winner' : ''}">${escapeHtml(m.team_a)}</td>
    <td data-label="Đội Bắc" class="${m.status==='done' && m.winner_id===m.player_b ? 'daily-winner' : ''}">${escapeHtml(m.team_b)}</td>
    <td data-label="Tỷ số" class="daily-score">${m.status==='done' ? `${m.points_a}–${m.points_b}` : '—'}</td><td data-label="Trạng thái">${dailyStatus(m.status)}</td>
    <td data-label="Thao tác"><div class="daily-row-actions">${m.status==='done' ? `<a class="daily-link" href="/?replay=${m.id}" target="_blank" rel="noopener">Xem lại</a>` : `<button class="daily-link" data-daily-detail="${m.id}" type="button">Chi tiết</button>`}
    ${['done','failed'].includes(m.status) ? `<details class="daily-menu"><summary aria-label="Thao tác trận #${m.id}">⋯</summary><button class="daily-danger" data-daily-void="${m.id}" type="button">Hủy và trả lượt</button></details>` : ''}</div></td></tr>`).join('')}</tbody></table></div>`;
}
function paintDailyDraws(draws) {
  if (!draws.length) { $('#dailyDraws').innerHTML='<p class="daily-empty">Chưa có lượt ghép.</p>'; return; }
  $('#dailyDraws').innerHTML=draws.slice(0,dailyVisibleDraws).map(d=>{
    const name=id=>escapeHtml(d.names?.[String(id)] || `Đội #${id}`);
    return `<article class="daily-draw"><p class="daily-draw-heading">${escapeHtml(vnDate(d.created_at))} · Lượt ghép #${d.id}</p>
      <div class="daily-table-wrap"><table class="daily-table"><thead><tr><th>Trận</th><th>Đội Nam</th><th>Đội Bắc</th></tr></thead><tbody>${(d.result.matches||[]).map(m=>`<tr><td>#${m.match_id}</td><td>${name(m.south)}</td><td>${name(m.north)}</td></tr>`).join('')}</tbody></table></div>
      ${d.result.waiting?.length ? `<p class="daily-waiting">Đội còn chờ: ${d.result.waiting.map(name).join(', ')}</p>` : ''}
      <details class="daily-technical"><summary>Chi tiết kỹ thuật</summary><pre>${escapeHtml(JSON.stringify({seed:d.seed,method:d.method,choices:d.choices,result:d.result,reason:d.reason,actor:d.actor},null,2))}</pre></details></article>`;
  }).join('');
  $('#dailyMoreDraws').classList.toggle('hidden',draws.length<=dailyVisibleDraws);
}
async function refreshDailyAdmin() {
  if (!adminToken) return;
  const controlVersion=dailyControlVersion, refreshVersion=++dailyRefreshVersion;
  const [control,draws]=await Promise.all([api('/api/admin/daily'),api('/api/admin/daily/draws')]);
  const matches=[];
  let hasMore=false;
  while(matches.length<dailyVisibleMatches) {
    const page=await api(`/api/admin/daily/matches?offset=${matches.length}&limit=${Math.min(100,dailyVisibleMatches-matches.length)}`);
    matches.push(...page.items); hasMore=page.has_more;
    if(!hasMore || !page.items.length) break;
  }
  if(refreshVersion!==dailyRefreshVersion || !adminToken) return;
  if(!dailySaving && controlVersion===dailyControlVersion) paintDailyControl(control.enabled);
  if(!$('#dailyAdminMatches details[open]')) paintDailyMatches(matches);
  if(!$('#dailyDraws details[open]')) paintDailyDraws(draws);
  $('#dailyMoreMatches').classList.toggle('hidden',!hasMore);
}
$('#dailyEnabled').addEventListener('change',async()=>{
  if(dailySaving) return;
  const enabled=$('#dailyEnabled').checked;
  dailySaving=true; dailyControlVersion+=1; paintDailyControl(enabled);
  $('#dailyAdminMessage').textContent='';
  try {
    const control=await api('/api/admin/daily',{method:'PUT',body:JSON.stringify({enabled})});
    dailySaving=false; paintDailyControl(control.enabled);
    $('#dailyAdminMessage').textContent=control.enabled ? 'Daily đã mở.' : 'Daily đã tạm đóng.';
  } catch(error) {
    dailySaving=false; paintDailyControl(!enabled);
    $('#dailyAdminMessage').textContent=`Chưa lưu được. ${error.message}`;
  }
});
$('#dailyMoreMatches').addEventListener('click',async()=>{
  dailyVisibleMatches+=10;
  try {await refreshDailyAdmin();} catch(error) {$('#dailyAdminMessage').textContent=error.message;}
});
$('#dailyMoreDraws').addEventListener('click',async()=>{
  dailyVisibleDraws+=10;
  try {await refreshDailyAdmin();} catch(error) {$('#dailyAdminMessage').textContent=error.message;}
});
$('#dailyAdminMatches').addEventListener('click',async event=>{
  const detail=event.target.closest('[data-daily-detail]');
  if(detail) {
    const dialog=$('#dailyMatchDialog');
    $('#dailyMatchDetails').textContent='Đang tải…'; dialog.showModal();
    try {
      const m=await api(`/api/matches/${detail.dataset.dailyDetail}`);
      $('#dailyMatchDetails').innerHTML=`<h3>Trận #${m.id}</h3><p>${escapeHtml(m.team_a)} (Nam) — ${escapeHtml(m.team_b)} (Bắc)</p><p>${dailyStatus(m.status)}</p><p>Ngày thi: ${escapeHtml(dailyDayText(m.event_day))}</p>${m.detail?.error ? `<p>${escapeHtml(m.detail.error)}</p>` : ''}`;
    } catch(error) {$('#dailyMatchDetails').textContent=error.message;}
    return;
  }
  const button=event.target.closest('[data-daily-void]');
  if(!button) return;
  const reason=prompt('Lý do hủy trận do sự cố hạ tầng (loại điểm/Elo và trả một lượt):');
  if(reason===null) return;
  button.disabled=true;
  try {
    await api(`/api/admin/matches/${button.dataset.dailyVoid}/void`,{method:'POST',body:JSON.stringify({reason})});
    button.closest('details').open=false;
    await refreshDailyAdmin();
  } catch(error) {$('#dailyAdminMessage').textContent=error.message;button.disabled=false;}
});
$('#dailyCloseMatch').addEventListener('click',()=>$('#dailyMatchDialog').close());
$('#dailyMatchDialog').addEventListener('click',event=>{if(event.target===event.currentTarget) event.currentTarget.close();});
if(adminToken) refreshDailyAdmin().catch(error=>{$('#dailyAdminMessage').textContent=error.message;});
