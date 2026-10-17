const $=id=>document.getElementById(id);
// 임시 도면: 칸 종류와 폭 (K 경차 56, S 소형 70, B 대형 92, P 기둥칸 70), 칸 높이 150
const ROWS=[{y:20,led:156,pil:125,items:['S','P','B','S','B','B']},{y:260,led:274,pil:272,items:['K','S','B','P','B']}], W={K:56,S:70,B:92,P:70}, H=150;
// 입구 위치와 가운데 통로의 높이(y)
const ENT={x:453,y:418}, AISLE=215;
let data=null, lastCurrent=null, lastSeq=null, pos={};
// 번호판의 공백, '-', '_'를 지우고 대문자로 통일 (서버 normalize_plate와 같은 규칙)
const normPlate=v=>String(v).replace(/[\s_-]+/g,'').toUpperCase();
// 화면에 글자를 넣을 때 HTML로 해석되지 않게 바꿔 줌
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const STATUS={free:'빈자리',assigned:'배정됨 (이동 중)',occupied:'주차 완료'};

// 입구 → 위로 올라가 가운데 통로 → 옆으로 이동 → 배정 칸 앞까지 경로와 화살표를 만듦
function routeTo(p){
 if(!p)return '';
 const end=p.top?185:275, tip=p.top?160:300;  // 위쪽 줄 칸이면 위로, 아래쪽 줄 칸이면 아래로 들어감
 return `<path class="route" d="M${ENT.x} ${ENT.y} V${AISLE} H${p.cx} V${end}"/><polygon class="head" points="${p.cx-14},${end} ${p.cx+14},${end} ${p.cx},${tip}"/>`;
}

// 도면 그리기. target: 강조할 칸 번호, route: 경로 안내 표시 여부
function makeLot(id,target='',route=false){
 let s='<rect width="526" height="450" fill="#4a4f55"/>',n=0;pos={};
 ROWS.forEach(r=>{let x=20;r.items.forEach(t=>{const w=W[t],cx=x+w/2;if(t==='P'){s+=`<rect class="bay" x="${x}" y="${r.y}" width="${w}" height="${H}"/><rect class="pil" x="${cx-14}" y="${r.pil}" width="28" height="28"/>`;}else{n++;const space=data?.spaces?.[n-1],sid=space?String(space.id):String(n),type=space?.type||t,status=space?.status||'free';const cls=status==='assigned'?'assigned':status==='occupied'?'occupied':'';pos[sid]={cx,top:r.y===20};s+=`<g class="b ${cls} ${target&&sid===String(target)?'target':''}" data-id="${esc(sid)}"><rect class="fill bay" x="${x}" y="${r.y}" width="${w}" height="${H}"/><text class="ty" x="${cx}" y="${r.y+H/2}">${type==='K'?'경':esc(type)}</text><text class="no" x="${cx}" y="${r.y===20?r.y+22:r.y+H-10}">${esc(sid)}</text><circle class="ledc" cx="${cx}" cy="${r.led}" r="6"/></g>`;}x+=w;});});
 s+='<path class="wall" d="M506 410 V20 H20 V410 H400 V260 H20 M20 170 H506"/>';
 if(route&&target)s+=routeTo(pos[String(target)]);
 s+='<text class="ent" x="453" y="441">입구</text>';
 $(id).innerHTML=s;$(id).classList.toggle('guide',route);
}

// 현재 차량(두 화면이 함께 보는 차량)의 정보 모으기
function cur(){
 const p=data.current||'',sid=p?data.assignments[p]||'':'';
 return {p,sid,space:data.spaces.find(s=>String(s.id)===String(sid)),v:data.vehicles.find(v=>v.plate===p)};
}

// 서버에서 최신 상태를 받아 화면을 다시 그림 (1초마다 실행해서 두 화면을 맞춤)
async function refresh(){const r=await fetch('/api/state');data=await r.json();if($('adm'))renderAdmin();if($('nav'))renderGuide();}
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.message||'요청 실패');return d;}
// 현재 차량 바꾸기 (관리자/내비 어느 쪽에서 불러도 두 화면이 함께 바뀜)
async function selectCar(p){const d=await post('/api/select',{plate:p});await refresh();return d;}

function renderAdmin(){
 const c=cur();makeLot('adm',c.sid);
 renderDetected();renderCamera();
 const spaces=data.spaces,free=spaces.filter(s=>s.status==='free').length;$('free').textContent=`${free} / ${spaces.length}`;
 $('summary').innerHTML=`<span class="pill">빈자리 ${free}</span><span class="pill">배정 ${spaces.filter(s=>s.status==='assigned').length}</span><span class="pill">주차 완료 ${spaces.filter(s=>s.status==='occupied').length}</span><span class="pill">너비 기준 ${data.width_limit}mm</span>`;
 // 현재 안내 차량
 $('current').innerHTML=!c.p?'<div class="message">안내 중인 차량이 없습니다. 차량을 배정하거나 내비 화면에서 번호를 입력하세요.</div>':
  `<div class="item"><span>차량번호</span><b>${esc(c.p)}</b></div>`+
  (c.v?`<div class="item"><span>차량 정보</span><b>${esc(c.v.type)} · ${c.v.width}mm · ${esc(c.v.need||'-')} 유형</b></div>`:'')+
  `<div class="item"><span>배정 공간</span><b>${c.sid?`${esc(c.sid)}번 · ${STATUS[c.space?.status]||''}`:'배정 없음'}</b></div>`;
 const assigned=spaces.filter(s=>s.status!=='free');
 $('assignments').innerHTML=assigned.length?assigned.map(s=>`<div class="item${s.plate===c.p?' on':''}"><span>${esc(s.plate)} · ${esc(s.id)}번 · ${s.status==='occupied'?'주차 완료':'배정됨'}</span><span class="btns"><button class="sim alt" onclick="pick('${encodeURIComponent(s.plate)}')">안내</button><button onclick="exitCar('${encodeURIComponent(s.plate)}')">출차</button></span></div>`).join(''):'<div class="message">배정된 차량이 없습니다.</div>';
 $('vehicles').innerHTML=data.vehicles.map(v=>`<div class="item"><span>${esc(v.plate)} · ${esc(v.type)} · ${v.width}mm</span><span>${data.assignments[v.plate]?esc(data.assignments[v.plate])+'번':''}</span></div>`).join('')||'<div class="message">등록 차량이 없습니다.</div>';
}
// 카메라(final.py)가 인식한 번호판을 보여 주고, 차량번호 칸에 채워 둠 (배정은 관리자가 [배정]을 눌러야 함)
function renderDetected(){
 const d=data.detected||{};
 if(!d.plate){$('detected').innerHTML='';lastSeq=d.seq;return;}
 // 새 인식이 들어왔을 때만 입력칸을 채움 (관리자가 다른 번호를 입력하는 중이면 건드리지 않음)
 if(d.seq!==lastSeq&&!(document.activeElement===$('plate')&&$('plate').value)){lastSeq=d.seq;$('plate').value=d.plate;}
 const sid=data.assignments[d.plate],reg=data.vehicles.some(v=>v.plate===d.plate);
 const note=sid?`이미 ${esc(sid)}번에 배정된 차량입니다. [배정]을 누르면 다시 안내합니다.`:reg?'등록 차량입니다. 번호를 확인한 뒤 [배정]을 누르세요.':'등록되지 않은 차량입니다. 번호가 맞는지 확인하세요.';
 $('detected').innerHTML=`<div class="detect${reg?'':' warn'}"><div><b>카메라 인식: ${esc(d.plate)}</b> <small>${esc(d.time)}</small><br><span>${note}</span></div><button class="sim alt" type="button" onclick="clearDetected()">무시</button></div>`;
}
// 입구 카메라 영상과 인식 상태 (final.py로 실행했을 때만 영상이 나옴)
function renderCamera(){
 const cam=data.camera||{},img=$('camImg');
 $('camStatus').textContent=cam.status||'-';$('camText').textContent=cam.on?(cam.text||'-'):'-';
 if(cam.on&&img.hidden){img.src='/camera.mjpg?'+Date.now();img.hidden=false;}  // 카메라가 켜지면 영상 연결
 if(!cam.on&&!img.hidden){img.hidden=true;img.removeAttribute('src');}
}
async function clearDetected(){try{await post('/api/detected/clear',{});$('plate').value='';await refresh();}catch(e){$('msg').textContent=e.message;}}
async function pick(p){try{const d=await selectCar(decodeURIComponent(p));$('msg').textContent=d.message;}catch(e){$('msg').textContent=e.message;}}
async function exitCar(p){try{await post('/api/exit',{plate:decodeURIComponent(p)});$('msg').textContent='출차 처리되었습니다.';await refresh();}catch(e){$('msg').textContent=e.message;}}

function renderGuide(){
 const c=cur(),parked=c.space?.status==='occupied';
 // 다른 화면에서 차량이 바뀌면 입력칸도 그 번호로 맞춤 (입력하는 중에는 건드리지 않음)
 if(c.p!==lastCurrent&&document.activeElement!==$('guidePlate')){lastCurrent=c.p;$('guidePlate').value=c.p;$('guideMsg').textContent='';}
 makeLot('nav',c.sid,!!c.sid&&!parked);  // 주차 완료 전까지만 경로를 보여 줌
 $('idle').classList.toggle('hide',!!c.sid);
 $('done').disabled=!c.sid||parked;
 if(!c.p){$('dist').textContent='대기 중';$('dest').textContent='차량번호를 입력하세요';}
 else if(!c.sid){$('dist').textContent='배정 없음';$('dest').textContent=`${c.p} 차량에 배정된 주차 공간이 없습니다`;}
 else if(parked){$('dist').textContent='주차 완료';$('dest').textContent=`${c.sid}번 공간에 주차했습니다. 안내를 종료합니다.`;}
 else{$('dist').textContent=`${c.sid}번`;$('dest').textContent=`입구에서 ${c.sid}번 공간까지 파란 선을 따라 이동하세요`;}
}

function initAdmin(){refresh();setInterval(refresh,1000);$('assignForm').onsubmit=async e=>{e.preventDefault();try{const d=await post('/api/assign',{plate:$('plate').value});$('msg').textContent=d.message;$('plate').value='';await refresh();}catch(err){$('msg').textContent=err.message;}};}
function initGuide(){refresh();setInterval(refresh,1000);
 $('findForm').onsubmit=async e=>{e.preventDefault();const p=normPlate($('guidePlate').value);try{await selectCar(p);lastCurrent=null;$('guidePlate').blur();renderGuide();}catch(err){$('guideMsg').textContent=err.message;}};
 $('done').onclick=async()=>{try{const d=await post('/api/complete',{plate:data.current});await refresh();$('guideMsg').textContent=d.message;}catch(e){$('guideMsg').textContent=e.message;}};
}
