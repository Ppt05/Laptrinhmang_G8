let dailyState=null, dailyRequest=null, dailySearchRequest=null;
let dailySubmitting=false, dailySearching=false, dailyRefreshVersion=0;
const dailyDrafts=new Map();
let dailyLanguage='python';
const dailyForm=document.querySelector('#dailyForm');
const panelDailyLeaderboard = new DailyLeaderboard({body: '#dailyLeaderboard', day: '#dailyDay', prefix: 'dailyStanding'});
dailyForm.elements.source.value=botTemplates.python;
dailyForm.elements.language.addEventListener('change',event=>{
  dailyDrafts.set(dailyLanguage,dailyForm.elements.source.value);
  dailyLanguage=event.target.value;
  dailyForm.elements.source.value=dailyDrafts.get(dailyLanguage) ?? botTemplates[dailyLanguage];
});
async function refreshDaily() {
  const session=token(), signed=Boolean(session), version=++dailyRefreshVersion;
  $('#dailyGuest').classList.toggle('hidden',signed);
  for(const selector of ['#dailyForm','#dailySearchForm','#dailyBotHistory']) $(selector).classList.toggle('hidden',!signed);
  dailyForm.querySelector('button').disabled=true;
  $('#dailyFind').disabled=true;
  $('#dailyCancel').classList.add('hidden'); $('#dailyRetry').classList.add('hidden');
  if(!signed) { dailyState=null; $('#dailyQuota').textContent=''; $('#dailyBots').textContent=''; }
  try {
    const [,s]=await Promise.all([panelDailyLeaderboard.refresh(),request(signed ? '/api/daily/state' : '/api/meta')]);
    if(version!==dailyRefreshVersion || token()!==session) return;
    if(!signed) { $('#dailyState').textContent=s.modes.daily ? 'Daily đang mở. Đăng nhập để tham gia.' : 'Daily đang tắt. BTC có thể bật trong trang quản trị.'; return; }
    dailyState=s;
    const parts=[`Ngày ${s.day} (UTC+7)`,s.enabled ? 'Daily đang mở' : 'Daily đang tắt'];
    if(s.search) parts.push(`Đang tìm đối thủ với bot #${s.search.submission_id}`);
    else if(s.match) parts.push(`Trận #${s.match.id}: ${s.match.status==='failed' ? 'Lỗi hạ tầng — chạy lại đúng trận này' : statusText[s.match.status] || s.match.status}`);
    else parts.push('Chọn một bot sẵn sàng để tìm trận mới.');
    if(s.tournament_busy) parts.push('Đội đang bận Tournament; chưa thể tìm trận Daily.');
    $('#dailyState').textContent=parts.join(' · ');
    $('#dailyQuota').textContent=`Trận: ${s.matches_used}/${s.match_limit} (còn ${s.matches_remaining}) · Bài nộp: ${s.submissions_used}/${s.submission_limit} (còn ${s.submissions_remaining}). Trận chờ/đang chạy giữ một lượt; hủy vì hạ tầng trả lượt.`;
    dailyForm.querySelector('button').disabled=!s.can_submit || dailySubmitting;
    const select=$('#dailyBotSelect'), previous=select.value;
    const ready=s.submissions.filter(bot=>bot.status==='ready');
    select.innerHTML=ready.length ? ready.map(bot=>`<option value="${bot.id}">#${bot.id} · ${bot.language==='cpp' ? 'C++17' : 'Python'}</option>`).join('') : '<option value="">Chưa có bot Daily sẵn sàng hôm nay</option>';
    if(s.search) select.value=String(s.search.submission_id);
    else if(ready.some(bot=>String(bot.id)===previous)) select.value=previous;
    select.disabled=Boolean(s.search || s.match || dailySearching);
    $('#dailyFind').disabled=!s.can_search || !ready.length || dailySearching;
    $('#dailyCancel').classList.toggle('hidden',!s.search);
    $('#dailyRetry').classList.toggle('hidden',s.match?.status!=='failed');
    $('#dailySearchHelp').textContent=s.matches_remaining===0 ? 'Đã dùng hết lượt hôm nay. Ngày mới bắt đầu lúc 00:00 UTC+7.' : s.search ? 'Có thể hủy tìm trước khi ghép. Đấu xong cần bấm tìm lại để chơi tiếp.' : 'Mỗi lần tìm chỉ ghép một trận. Hệ thống ưu tiên đối thủ khác và cân bằng phe khi có thể.';
    $('#dailyBots').innerHTML=s.submissions.length ? s.submissions.map(bot=>`<p class="daily-bot-row"><strong>#${bot.id}</strong> · ${bot.language==='cpp' ? 'C++17' : 'Python'} · ${escapeHtml(statusText[bot.status] || bot.status)}${bot.compile_error ? `<small class="row-error">${escapeHtml(bot.compile_error)}</small>` : ''}${bot.status==='infrastructure_error' ? ` <button type="button" class="text-button" data-daily-compile-retry="${bot.id}">Kiểm tra lại lỗi hạ tầng</button>` : ''}</p>`).join('') : '<p class="muted">Chưa nộp bot Daily hôm nay.</p>';
  } catch(error) { if(version===dailyRefreshVersion) $('#dailyState').textContent=error.message; }
}
dailyForm.addEventListener('submit',async event=>{
  event.preventDefault(); if(dailySubmitting) return;
  const body=JSON.stringify(Object.fromEntries(new FormData(dailyForm)));
  if(!dailyRequest || dailyRequest.body!==body || dailyRequest.session!==token()) dailyRequest={body,key:crypto.randomUUID(),session:token()};
  dailySubmitting=true; dailyForm.querySelector('button').disabled=true;
  try {
    const result=await request('/api/daily/submissions',{method:'POST',headers:{'Idempotency-Key':dailyRequest.key},body});
    dailyRequest=null; $('#dailyMessage').textContent=`Đã nhận bot Daily #${result.submission_id}. Chờ sẵn sàng rồi chọn Tìm trận.`;
    await refreshDashboard();
  } catch(error) { $('#dailyMessage').textContent=error.message; }
  finally { dailySubmitting=false; await refreshDaily(); }
});
$('#dailySearchForm').addEventListener('submit',async event=>{
  event.preventDefault(); if(dailySearching) return;
  const sid=Number($('#dailyBotSelect').value); if(!sid) return;
  if(!dailySearchRequest || dailySearchRequest.sid!==sid || dailySearchRequest.session!==token()) dailySearchRequest={sid,key:crypto.randomUUID(),session:token()};
  dailySearching=true; $('#dailyFind').disabled=true;
  try {
    await request('/api/daily/queue',{method:'POST',headers:{'Idempotency-Key':dailySearchRequest.key},body:JSON.stringify({submission_id:sid})});
    dailySearchRequest=null; $('#dailyMessage').textContent=`Đang tìm trận với bot #${sid}.`;
  } catch(error) { $('#dailyMessage').textContent=error.message; }
  finally { dailySearching=false; await refreshDaily(); }
});
async function dailyAction(url,method='POST') {
  try { await request(url,{method}); $('#dailyMessage').textContent='Đã cập nhật Daily.'; await refreshDaily(); }
  catch(error) { $('#dailyMessage').textContent=error.message; }
}
$('#dailyCancel').addEventListener('click',()=>dailyAction('/api/daily/queue','DELETE'));
$('#dailyRetry').addEventListener('click',()=>dailyState?.match && dailyAction(`/api/daily/matches/${dailyState.match.id}/retry`));
$('#dailyBots').addEventListener('click',event=>{const b=event.target.closest('[data-daily-compile-retry]');if(b) dailyAction(`/api/daily/submissions/${b.dataset.dailyCompileRetry}/retry`);});
$('#dailyRefresh').addEventListener('click',refreshDaily);
refreshDaily();
