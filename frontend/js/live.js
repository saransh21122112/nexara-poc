// Live build view: a real pipeline (see backend/build_service.py) drawn as it happens. Every fact on screen came from a recorded event.
const STAGES=[
 ["received","Received","inbox","We read the request and pull out the facts."],
 ["analyse","Plan","wand","We decide exactly what to ask the model to build."],
 ["specify","Specify","skill","The model writes the specification: name, purpose, tools and safety limits."],
 ["test","Test","flask","Each test scenario runs on the real model and is checked."],
 ["package","Package","box","The files that would go to the customer's private repo are laid out."],
 ["ready","Ready","shield","A person reviews it, then delivers. Nothing is sent automatically."]];
let LIVE={timer:null,tick:null,pick:null,last:""};
const fmtS=ms=>{const s=Math.max(0,Math.floor(ms/1000));return `${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`};
function stopLive(){clearInterval(LIVE.timer);clearInterval(LIVE.tick);LIVE.timer=LIVE.tick=null}

async function live(v){
 stopLive();
 const rs=await api("/requests");
 if(!rs.length){v.innerHTML='<h2>Live build</h2><div class="empty">No client requests yet. Submit one on the public site first.</div>';return}
 const id=SEL.live&&rs.some(r=>r.id===SEL.live)?SEL.live:rs[0].id;SEL.live=id;
 v.innerHTML=`<h2>Live build</h2><p class="muted">Watch a build happen: what is done, what is running and what comes next.</p>
 <div class="panel"><div class="row"><select id="lrq" style="max-width:420px">${rs.map(r=>`<option value="${r.id}" ${r.id===id?"selected":""}>${esc(r.company_name)} · ${esc(r.agent_type)} · ${r.id}</option>`).join("")}</select>
 <button class="btn" id="lgo">Start build</button><span id="lst" class="badge"></span><span id="lel" class="muted"></span></div><div id="lerr"></div></div>
 <div class="panel pipe-panel"><div class="pipe" id="pipe"></div></div>
 <div class="split live"><div class="panel" id="ldet"></div><div class="panel"><h3>Event log</h3><div id="llog" class="log" tabindex="0" role="region" aria-label="Event log"></div></div></div>`;
 $("#lrq").onchange=()=>{SEL.live=$("#lrq").value;LIVE.pick=null;LIVE.last="";poll(true)};
 $("#lgo").onclick=async()=>{$("#lerr").innerHTML="";$("#lgo").disabled=true;
  try{await api(`/requests/${SEL.live}/build`,{method:"POST"});LIVE.pick=null;LIVE.last="";await poll(true)}
  catch(e){$("#lerr").innerHTML=`<div class="bad">${esc(e.message)}</div>`;$("#lgo").disabled=false}};
 let B=null;
 async function poll(force){
  if(!document.body.contains($("#pipe"))||VIEW!=="live"){stopLive();return}
  try{B=await api(`/builds/${SEL.live}`)}catch(e){$("#lerr").innerHTML=`<div class="bad">${esc(e.message)}</div>`;return}
  const key=JSON.stringify([B.events.length,B.status,LIVE.pick]);
  if(force||key!==LIVE.last){LIVE.last=key;draw(B)}
  if(B.status==="running"){if(!LIVE.timer)LIVE.timer=setInterval(()=>poll(false),1500)}else{clearInterval(LIVE.timer);LIVE.timer=null}
  $("#lgo").disabled=B.status==="running";$("#lgo").textContent=B.status==="none"?"Start build":B.status==="running"?"Building...":"Build again"}
 function elapsed(){const el=$("#lel");if(!el||!B||!B.started_at){return}const end=B.status==="running"?Date.now():new Date(B.events[B.events.length-1].at).getTime();el.textContent="Elapsed "+fmtS(end-new Date(B.started_at).getTime())}
 LIVE.tick=setInterval(()=>{if(!document.body.contains($("#pipe"))){stopLive();return}elapsed()},1000);
 function stageState(b,k){return (b.stages[k]||{}).state||"pending"}
 function draw(b){
  const active=STAGES.find(([k])=>["running","failed","interrupted"].includes(stageState(b,k)));
  const shown=LIVE.pick||(active?active[0]:b.status==="ready"?"ready":b.status==="none"?"received":"received");
  $("#lst").className="tag "+({ready:"ok",failed:"fail",interrupted:"fail"}[b.status]||"");$("#lst").textContent={none:"Not started",running:"Running",ready:"Ready for review",failed:"Stopped",interrupted:"Interrupted"}[b.status];
  $("#lerr").innerHTML=["failed","interrupted"].includes(b.status)?`<div class="bad"><b>${b.status==="failed"?"The build stopped.":"The build was interrupted."}</b> ${esc((b.stages[active?active[0]:"received"]||{}).title||"")} Press <b>Build again</b> to retry.</div>`:"";
  $("#pipe").innerHTML=STAGES.map(([k,name,ic,why],i)=>{
   const st=stageState(b,k),cls=st==="done"?"done":st==="running"?"active":["failed","interrupted"].includes(st)?"bad":"next";
   const nextOne=!active&&b.status==="running"&&cls==="next"&&STAGES.findIndex(([x])=>stageState(b,x)==="pending")===i;
   return `${i?`<div class="conn ${cls==="done"?"done":cls==="active"?"flow":""}"></div>`:""}<button class="node ${cls} ${shown===k?"sel":""}" data-k="${k}" title="${esc(why)}"><span class="ring">${st==="done"?ico("check"):ico(ic)}</span><b>${name}</b><small>${st==="done"?"done":st==="running"?"working":cls==="bad"?"stopped":"next"}</small></button>`}).join("");
  $("#pipe").querySelectorAll(".node").forEach(n=>n.onclick=()=>{LIVE.pick=n.dataset.k;LIVE.last="";draw(b)});
  $("#ldet").innerHTML=detail(b,shown);
  const dl=(b.delivery||[]).map(e=>({at:e.at,stage:"delivery",title:e.kind.replace(/_/g," "),state:"done"}));
  const log=[...b.events.filter(e=>e.state!=="running"||e.stage==="specify"||e.stage==="test").map(e=>({at:e.at,stage:e.stage,title:e.title,state:e.state})),...dl].sort((x,y)=>y.at.localeCompare(x.at));
  $("#llog").innerHTML=log.length?log.map(e=>`<div class="le ${e.state}"><span>${new Date(e.at).toLocaleTimeString()}</span><b>${esc(e.stage)}</b> ${esc(e.title)}</div>`).join(""):'<p class="muted">Nothing yet. Press Start build.</p>';
  elapsed()}
 function detail(b,k){
  const [,name,,why]=STAGES.find(s=>s[0]===k),s=b.stages[k]||{state:"pending"},d=s.detail||{};
  let body="";
  if(s.state==="pending")body=`<p class="muted"><b>Next:</b> ${esc(why)}</p>`;
  else if(k==="received")body=`<div class="chips"><span class="tag">${esc(d.kind||"")}</span><span class="tag">${esc(d.company||"")}</span><span class="tag">uses: ${esc(d.tools||"")}</span></div>`;
  else if(k==="analyse")body=`<p class="muted">What we will ask the model to do:</p><ul class="steps2">${(d.will_ask||[]).map((x,i)=>`<li style="--i:${i}">${esc(x)}</li>`).join("")}</ul>`;
  else if(k==="specify"){
   if(s.state==="running")body=`<div class="shimmer"></div><p class="muted">${esc(s.title)}. This is one model call, so there is no step-by-step view of its thinking. The result appears here the moment it lands.</p>`;
   else if(s.state==="done")body=`<div class="specard"><h3>${esc(d.name)}</h3><p>${esc(d.purpose)}</p><div class="chips">${(d.capabilities||[]).map((c,i)=>`<span class="tag" style="--i:${i}">${esc(c)}</span>`).join("")}</div>
     <p class="muted" style="margin:10px 0 4px">Tools: ${(d.tools||[]).length?esc(d.tools.join(", ")):"none (read-only)"} · model ${esc(d.model||"")}</p><p class="muted" style="margin:8px 0 4px">Safety limits</p><ul class="steps2">${(d.safety||[]).map((x,i)=>`<li style="--i:${i}">${ico("shield")}${esc(x)}</li>`).join("")}</ul></div>`;
  }else if(k==="test"){
   const total=(b.stages.test.detail||{}).total||s.progress.length;
   body=`<div class="tests">${s.progress.map((p,i)=>`<div class="trow ${p.detail.passed?"ok":"no"}" style="--i:${i}"><span class="tag ${p.detail.skipped?"":p.detail.passed?"ok":"fail"}">${p.detail.skipped?"not run":p.detail.passed?"pass":"fail"}</span><b>${esc(p.detail.name)}</b><small class="muted">${p.detail.ms!=null?(p.detail.ms/1000).toFixed(1)+"s":""} · ${esc(p.detail.reason)}</small></div>`).join("")}
    ${s.state==="running"?Array.from({length:Math.max(0,total-s.progress.length)},(_,i)=>`<div class="trow wait"><span class="tag">${i?"queued":"running"}</span><div class="shimmer slim"></div></div>`).join(""):""}</div>`;
  }else if(k==="package"){
   if(s.state==="running")body='<div class="shimmer"></div>';
   else{const f=d.files||[];body=`<p class="muted">${f.length} files would be created in the customer's private repository. Nothing has been pushed.</p><div class="tree">${f.map((x,i)=>`<div style="--i:${i}">${ico("check")}<code>${esc(x.path)}</code><small class="muted">${x.bytes} B</small></div>`).join("")}</div>`}
  }else if(k==="ready"){
   body=`<p>Ready for your review. Nothing has been sent anywhere.</p><div class="row"><button class="btn" id="lopen">Open in Builder</button></div>`;
   if((b.delivery||[]).length)body+=`<p class="muted" style="margin-top:14px">Handover so far</p><ul class="steps2">${b.delivery.map(e=>`<li>${ico("check")}${esc(e.kind.replace(/_/g," "))}</li>`).join("")}</ul>`;
  }
  setTimeout(()=>{const o=$("#lopen");if(o)o.onclick=()=>{SEL.agent=(b.stages.ready.detail||{}).agent_id||SEL.agent;go("builder")}},0);
  return `<h3>${name} <span class="tag ${s.state==="done"?"ok":["failed","interrupted"].includes(s.state)?"fail":""}">${s.state==="pending"?"next":s.state}</span></h3><p class="muted why">${esc(why)}</p>${body}`}
 if(SEL.start){SEL.start=false;$("#lgo").click()}else poll(true);
}
