'use strict';
const $ = (selector) => document.querySelector(selector);
const token = $('meta[name="local-token"]').content;
let config, live = null, viewed = null, cursor = 0, follow = true, polling = false, replayMode = false, listKey = '';
const phases = {compiling:'Đang biên dịch…',paused:'Đã tạm dừng',running:'Đang đấu',done:'Đã kết thúc',error:'Lỗi chuẩn bị bot',cancelled:'Đã dừng'};
const reasons = {goal:'Chạm đích',elimination:'Loại hết một loại quân',threefold_repetition_draw:'Lặp lại thế cờ ba lần',no_legal_moves_draw:'Không còn nước đi',move_limit_draw:'Đạt giới hạn lượt',invalid_move_or_timeout:'Bot lỗi, nước đi sai hoặc quá thời gian'};
function notice(message=''){ $('#notice').textContent=message; $('#notice').hidden=!message; }
async function api(path, body){
  const response=await fetch('/api/'+path,{method:body?'POST':'GET',headers:{'X-Local-Token':token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  const result=await response.json(); if(!response.ok)throw new Error(result.error||'Local runner không phản hồi.'); return result;
}
function guard(fn){return async(...args)=>{try{await fn(...args)}catch(error){notice(error.message)}}}
function readBot(suffix){return {name:$('#name-'+suffix).value.trim()||'Bot '+suffix,language:$('#language-'+suffix).value,source:$('#source-'+suffix).value}}
function fillBot(suffix,bot){$('#name-'+suffix).value=bot.name;$('#language-'+suffix).value=bot.language;$('#source-'+suffix).value=bot.source;}
function sample(suffix){const key=$('#sample-'+suffix).value;if(key!=='custom'&&config)fillBot(suffix,config.samples[key]);}
function backendNote(){ $('#backend-note').textContent=$('#backend').value==='docker'?'Docker: cùng image sandbox, 256 MiB RAM, 1 CPU và giới hạn thời gian như BTC. Cần Docker đang chạy và image đã build.':'Native chạy code với quyền tài khoản máy bạn. Dùng source của bạn hoặc bot mẫu; chọn Docker để thử code chưa tin cậy.'; }
for(const suffix of ['a','b']){
  $('#sample-'+suffix).onchange=()=>sample(suffix);
  $('#source-'+suffix).oninput=()=>{$('#sample-'+suffix).value='custom'};
  $('#file-'+suffix).onchange=guard(async(event)=>{const file=event.target.files[0];if(!file)return;if(file.size>100000)throw new Error('Source tối đa 100.000 byte.');fillBot(suffix,{name:file.name,language:/\.(cpp|cc|cxx)$/i.test(file.name)?'cpp':'python',source:await file.text()});$('#sample-'+suffix).value='custom';});
}
$('#backend').onchange=backendNote;
$('#swap').onclick=()=>{const a=readBot('a'),b=readBot('b');fillBot('a',b);fillBot('b',a);$('#sample-a').value=$('#sample-b').value='custom';};
$('#prepare').onclick=guard(async()=>{notice();const bots=[readBot('a'),readBot('b')];if(bots.some(bot=>!bot.source.trim()))throw new Error('Chọn hoặc nhập source cho cả hai bot.');await api('session',{bots,backend:$('#backend').value,max_moves:Number($('#max-moves').value)});replayMode=false;follow=true;cursor=0;await refresh();});
async function control(action){if(!live||replayMode)return;notice();follow=true;await api('control',{id:live.id,action,delay:Number($('#speed').value)});await refresh();}
for(const action of ['run','step','pause','stop'])$('#'+action).onclick=guard(()=>control(action));
$('#timeline').oninput=()=>{follow=false;cursor=Number($('#timeline').value);render()};
$('#previous').onclick=()=>{follow=false;cursor=Math.max(0,cursor-1);render()};
$('#next').onclick=()=>{follow=false;cursor=Math.min((viewed?.history.length||1)-1,cursor+1);render()};
for(const tab of document.querySelectorAll('[data-tab]'))tab.onclick=()=>{for(const other of document.querySelectorAll('[data-tab]')){const selected=other===tab;other.setAttribute('aria-selected',String(selected));$('#'+other.dataset.tab).hidden=!selected;}};
function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('#download').onclick=guard(async()=>{const data=replayMode?viewed:await api('replay');if(data)download(data,'ott-local-'+data.id+'.json');});
function validateReplay(data){
  if(data?.format!=='ott-local-v1'||!Array.isArray(data.history)||!data.history.length||data.history.length>201||!Array.isArray(data.bots)||data.bots.length!==2)throw new Error('Replay không đúng định dạng ott-local-v1.');
  for(const frame of data.history){if(!Array.isArray(frame.game?.pieces)||frame.game.pieces.length>20)throw new Error('Bàn cờ replay không hợp lệ.');for(const piece of frame.game.pieces){if(!Number.isInteger(piece.x)||piece.x<0||piece.x>8||!Number.isInteger(piece.y)||piece.y<0||piece.y>8||!['north','south'].includes(piece.side)||!['rock','paper','scissors'].includes(piece.type))throw new Error('Quân trong replay không hợp lệ.');}}
  return data;
}
$('#replay-file').onchange=guard(async(event)=>{const file=event.target.files[0];if(!file)return;if(live?.busy)throw new Error('Dừng ván đang chạy trước khi mở replay.');if(file.size>8000000)throw new Error('Replay tối đa 8 MB.');viewed=validateReplay(JSON.parse(await file.text()));replayMode=true;follow=false;cursor=0;notice();render();});
async function refresh(){if(polling)return;polling=true;try{live=await api('session');if(!replayMode){viewed=live;if(follow)cursor=(live?.history.length||1)-1;render();}}finally{polling=false}}
function draw(frame){
  const canvas=$('#board'),ctx=canvas.getContext('2d'),size=720,pad=36,cell=72;
  ctx.clearRect(0,0,size,size);ctx.fillStyle='#f6f8fb';ctx.fillRect(0,0,size,size);
  ctx.font='13px system-ui';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='#778aa3';
  for(let y=0;y<9;y++)for(let x=0;x<9;x++){ctx.fillStyle=(x+y)%2?'#dce5ef':'#edf2f8';ctx.fillRect(pad+x*cell,pad+(8-y)*cell,cell,cell);}
  const move=frame?.action;const squarePoint=(square)=>[pad+(square.charCodeAt(0)-97)*cell,pad+(9-Number(square.slice(1)))*cell];
  if(move?.from&&move?.to){for(const [square,color]of [[move.from,'#e4c06c99'],[move.to,'#e4c06cbb']]){const [x,y]=squarePoint(square);ctx.fillStyle=color;ctx.fillRect(x,y,cell,cell);}}
  for(let i=0;i<9;i++){ctx.fillStyle='#778aa3';ctx.fillText('abcdefghi'[i],pad+(i+.5)*cell,702);ctx.fillText(String(9-i),17,pad+(i+.5)*cell);}
  const pieces=frame?.game.pieces||config?.initial?.pieces||[];
  for(const piece of pieces){const x=pad+(piece.x+.5)*cell,y=pad+(8-piece.y+.5)*cell,south=piece.side==='south';ctx.beginPath();ctx.arc(x,y+3,25,0,Math.PI*2);ctx.fillStyle='#10294b18';ctx.fill();ctx.beginPath();ctx.arc(x,y,25,0,Math.PI*2);ctx.fillStyle=south?'#70d1e5':'#df91be';ctx.fill();ctx.strokeStyle=south?'#299fb9':'#bd6298';ctx.lineWidth=2;ctx.stroke();ctx.font='27px "Segoe UI Emoji",system-ui';ctx.fillStyle='#fff';ctx.fillText({rock:'✊',paper:'✋',scissors:'✌️'}[piece.type],x,y-1);ctx.font='9px system-ui';ctx.fillStyle=south?'#086c85':'#8f356b';ctx.fillText(String(piece.id),x,y+36);}
}
function render(){
  const data=viewed,frames=data?.history||[],max=Math.max(0,frames.length-1);cursor=Math.min(cursor,max);const frame=frames[cursor];draw(frame);
  const active=!!live?.busy,ready=!replayMode&&['paused','running'].includes(live?.status);
  $('#prepare').disabled=active;$('#stop').disabled=!active||replayMode;
  for(const action of ['run','step','pause'])$('#'+action).disabled=!ready;
  $('#phase').textContent=replayMode?'Xem replay':phases[data?.status]||'Sẵn sàng';
  $('#team-a').textContent=data?.bots?.[0]?.name||'Bot A';$('#team-b').textContent=data?.bots?.[1]?.name||'Bot B';
  $('#timeline').max=max;$('#timeline').value=cursor;$('#counter').textContent=cursor+' / '+max;$('#previous').disabled=cursor===0;$('#next').disabled=cursor===max;$('#download').disabled=!data;
  $('#turn-label').textContent=frame?`Lượt ${cursor} · ${frame.game.turn==='south'?'South':'North'} ${frame.game.win_reason?'':'đến lượt'}`:'Tạo ván để bắt đầu';
  $('#piece-label').textContent=(frame?.game.pieces.length||20)+' quân · 9 × 9';
  $('#timing').textContent=frame?.elapsed_ms!=null?frame.elapsed_ms.toFixed(1)+' ms':'— ms';
  $('#observation-json').textContent=frame?.observation?JSON.stringify(frame.observation,null,2):'Chưa có observation.';
  $('#action-detail').textContent=frame?.error|| (frame?.action?`${frame.observation.side}: ${frame.action.piece_id}\n${frame.action.from} → ${frame.action.to}${frame.action.capture?' · Ăn '+frame.action.capture:''}\n${frame.elapsed_ms?.toFixed(2)} ms`:'Chưa có nước đi.');
  $('#log-a').textContent=data?.logs?.south||'Chưa có log.';$('#log-b').textContent=data?.logs?.north||'Chưa có log.';
  const list=$('#move-list'), key=[data?.id,frames.length,cursor].join(':');if(key!==listKey){const scroll=list.scrollTop;listKey=key;list.replaceChildren();frames.slice(1).forEach((entry,index)=>{const li=document.createElement('li'),button=document.createElement('button'),label=document.createElement('span'),time=document.createElement('small');button.type='button';button.className=entry.observation?.side||'';button.setAttribute('aria-current',String(cursor===index+1));label.textContent=entry.action?`${index+1}. ${entry.action.piece_id} ${entry.action.from} → ${entry.action.to}`:`${index+1}. Bot lỗi`;time.textContent=entry.elapsed_ms!=null?entry.elapsed_ms.toFixed(1)+' ms':'—';button.append(label,time);button.onclick=()=>{follow=false;cursor=index+1;render()};li.append(button);list.append(li);});list.scrollTop=scroll;}
  const reason=frame?.game.win_reason;$('#result').hidden=!reason;$('#result').textContent=reason?`${frame.game.winner?(frame.game.winner==='south'?'Bot A':'Bot B')+' thắng':'Hòa'} · ${reasons[reason]||reason}`:'';
  if(data?.error)notice(data.error);
}
guard(async()=>{config=await api('config');$('#backend').value=config.backend;sample('a');sample('b');backendNote();await refresh();if(!config.cpp)$('#backend-note').textContent+=' Máy chưa tìm thấy g++; Python vẫn chạy được.';setInterval(()=>{if(!replayMode)refresh().catch(error=>notice(error.message))},500);})();
render();
