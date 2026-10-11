// Factory extras: friendly timeline, stacked-card tables on phones, Messages view, Test lab. Loaded before factory.js (uses its globals at call time).
const EVT={email_verified:"Email verified",verification_sent:"Verification email sent",verification_not_sent:"Verification email not sent",repo_created:"Repository created",agent_added:"Agent added to repository",
 agent_proposed:"Agent proposed (pull request)",synced:"Repository updated",sync_proposed:"Update proposed",sync_conflict:"Update blocked by hand edits",pr_opened:"Pull request opened",pr_updated:"Pull request updated",
 pr_merged:"Pull request merged",pr_closed:"Pull request closed",invited:"Invitation sent",invite_failed:"Invitation failed",invite_accepted:"Invitation accepted",invite_pending:"Invitation pending",invite_expired:"Invitation expired",
 deliver_failed:"Delivery failed",request_attached:"Request linked to customer",note:"Note"};
const KEYS={pr:"Pull request",url:"Link",files:"Files",repo:"Repository",private:"Private",agent:"Agent",slug:"Command",commit:"Commit",user:"GitHub user",status:"Status",request:"Request",via:"Verified by",note:"Note",mode:"Mode",paths:"Files",error:"Error"};
const isUrl=v=>typeof v==="string"&&/^https:\/\/[^\s]+$/.test(v);
function kvValue(v){if(Array.isArray(v))return v.map(kvValue).join("<br>");if(v===null||v===undefined||v==="")return '<span class="muted">none</span>';if(typeof v==="boolean")return v?"yes":"no";
 if(isUrl(v))return `<a href="${esc(v)}" target="_blank" rel="noopener">${esc(v.replace(/^https:\/\//,""))}</a>`;if(/^[0-9a-f]{40}$/.test(String(v)))return `<code title="${esc(v)}">${esc(String(v).slice(0,7))}</code>`;
 if(typeof v==="object")return esc(JSON.stringify(v));return esc(String(v))}
function eventDetail(d){if(d===null||d===undefined||d==="")return "";if(typeof d==="string")return `<div class="kv"><b>${esc(d)}</b></div>`;
 return `<div class="kvs">${Object.entries(d).filter(([k,v])=>v!==null&&v!==""&&!(Array.isArray(v)&&!v.length)).map(([k,v])=>`<div class="kv"><span>${esc(KEYS[k]||k.replace(/_/g," "))}</span><b>${kvValue(v)}</b></div>`).join("")}</div>`}
function eventsTable(events){
 if(!events.length)return '<p class="muted">No events yet.</p>';
 return `<div class="tl">${events.map(e=>`<div class="te ${/failed|conflict|expired|closed/.test(e.kind)?"bad":e.kind==="note"?"note":"ok"}"><div class="th"><span class="tag ${/failed|conflict|expired/.test(e.kind)?"fail":e.kind==="note"?"":"ok"}">${esc(EVT[e.kind]||e.kind.replace(/_/g," "))}</span><time class="muted">${esc(fmt(e.at))}</time></div>${eventDetail(e.detail)}</div>`).join("")}</div>`}

// Tables become stacked cards on phones: each cell gets its column name as a label (CSS shows it under 640px).
function cardify(root){(root||document).querySelectorAll("table").forEach(t=>{const hs=[...t.querySelectorAll("tr:first-child th")].map(h=>h.textContent.trim());
 if(hs.length)t.classList.add("stack");t.querySelectorAll("tr").forEach(r=>[...r.children].forEach((c,i)=>{if(c.tagName==="TD"&&hs[i])c.setAttribute("data-label",hs[i])}))})}

// ---------- Messages (from the public contact form) ----------
async function messages(v){
 const ms=await api("/messages");const unread=ms.filter(m=>!m.read).length;
 v.innerHTML=`<h2>Messages</h2><p class="muted">From the public contact form. ${ms.length} message(s), ${unread} unread. Reply from your own email.</p>
 ${ms.length?`<div class="msgs">${ms.map(m=>`<article class="panel mrow ${m.read?"":"unread"}" data-id="${esc(m.id)}"><div class="row" style="justify-content:space-between"><div><b>${esc(m.name)}</b> <a href="mailto:${esc(m.email)}">${esc(m.email)}</a></div><div class="row"><span class="tag">${esc(m.topic)}</span><time class="muted">${esc(fmt(m.created_at))}</time></div></div><p class="mtext">${esc(m.message)}</p>${m.read?'<span class="tag ok">read</span>':`<button class="btn sec sm" data-read="${esc(m.id)}">Mark as read</button>`}</article>`).join("")}</div>`:'<div class="empty">No messages yet. They appear here when someone uses the contact page.</div>'}`;
 v.querySelectorAll("[data-read]").forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api(`/messages/${b.dataset.read}/read`,{method:"POST"});messages(v)}catch(e){b.textContent=e.message}})}

// ---------- Test lab: AI-written test cases, no sample data ----------
function handoverSteps(p,name){
 const plugin=p&&p.plugin||"<plugin-name>",repo=p&&p.repo?`${p.repo.owner}/${p.repo.name}`:"<owner>/<repository>",cmd=p&&p.command||`/${plugin}:<skill>`;
 return `<ol class="steps3"><li>The customer accepts the GitHub invitation to the private repository <code>${esc(repo)}</code>.</li>
  <li>They install it in Claude Code with one command:<pre>gh repo clone ${esc(repo)} ~/.claude/skills/${esc(plugin)}</pre>or from inside Claude Code: <code>/plugin marketplace add ${esc(repo)}</code> then <code>/plugin install ${esc(plugin)}@nexara-${esc(plugin)}</code>.</li>
  <li>They run <code>/reload-plugins</code> (or open a new session), then use it: <code>${esc(cmd)}</code> followed by what they want done.</li>
  <li>No GitHub? Download the plugin zip from the customer page and unzip it into <code>~/.claude/skills/${esc(plugin)}</code>.</li></ol>`}
async function playground(v){
 const agents=await api("/agents");if(!SEL.agent||!agents.find(a=>a.id===SEL.agent))SEL.agent=agents[0]?.id||null;
 if(!agents.length){v.innerHTML='<h2>Test lab</h2><div class="empty">Nothing to test yet. Build something from a request first.</div>';return}
 v.innerHTML=`<h2>Test lab</h2><p class="muted">Every test case carries its own example input, so nothing depends on sample data. Run the cases that matter, or skip straight to handover.</p>${cfgBanner()}
 <div class="panel"><label style="margin-top:0">Build</label><select id="ag">${agents.map(a=>`<option value="${a.id}" ${a.id===SEL.agent?"selected":""}>${esc(a.spec.agent_name)} · ${a.id}</option>`).join("")}</select></div>
 <div class="panel"><div class="row" style="justify-content:space-between"><h3>Test cases</h3><div class="row"><button class="btn sec sm" id="tlgen">${ico("wand")}Generate more with AI</button><button class="btn sm" id="tlrun">Run selected</button></div></div>
 <div class="row" style="margin-top:8px"><input id="tlfocus" placeholder="Optional focus, e.g. angry customers, missing data" maxlength="300" style="max-width:420px"><select id="tlcount" style="width:auto"><option>2</option><option selected>3</option><option>5</option></select></div>
 <div id="tlst" class="muted" style="margin-top:6px"></div><div id="tlcases"></div><div id="tlprev"></div></div>
 <div id="tlout"></div>
 <div class="panel"><div class="row" style="justify-content:space-between"><h3>Skip testing? Hand it over</h3><button class="btn sec sm" id="tlhand">Show the steps</button></div><p class="muted">How the customer adds this to their Claude from the private repository.</p><div id="tlsteps"></div></div>
 <div id="hist"></div>`;
 const cur=()=>agents.find(a=>a.id===SEL.agent);
 const caseHtml=(s,i,pre)=>`<label class="case"><input type="checkbox" data-i="${i}" ${pre?"data-pre":"checked"}><span><b>${esc(s.name)}</b><small class="muted">${esc(s.expected_behavior||"")}</small><span class="ctask">${esc(s.task)}</span></span></label>`;
 const draw=()=>{const sc=cur().spec.test_scenarios||[];$("#tlcases").innerHTML=sc.length?sc.map((s,i)=>caseHtml(s,i)).join(""):'<p class="muted">No test cases yet. Generate some with AI.</p>'};
 draw();
 $("#ag").onchange=e=>{SEL.agent=e.target.value;$("#tlprev").innerHTML="";$("#tlout").innerHTML="";$("#tlsteps").innerHTML="";draw();hist()};
 const hist=async()=>{const rs=await api("/runs?agent_id="+SEL.agent);$("#hist").innerHTML=rs.length?`<div class="panel"><h3>Execution history</h3>${rs.slice(0,8).map(logHtml).join("")}</div>`:""};
 $("#tlgen").onclick=async()=>{const b=$("#tlgen");b.disabled=true;$("#tlst").textContent="Writing test cases - this can take up to a minute...";$("#tlprev").innerHTML="";
  try{const x=await api(`/agents/${SEL.agent}/generate-tests`,{method:"POST",body:JSON.stringify({count:+$("#tlcount").value,focus:$("#tlfocus").value.trim()})});
   $("#tlst").textContent="";$("#tlprev").innerHTML=`<h3 style="margin-top:14px">Proposed new cases</h3>${x.scenarios.map((s,i)=>caseHtml(s,i,true)).join("")}<div class="row" style="margin-top:10px"><button class="btn" id="tlsave">Add selected to this build</button><span class="muted">They are saved to the specification and shipped with the next update.</span></div><div id="tlsm"></div>`;
   $("#tlprev").querySelectorAll("[data-pre]").forEach(c=>c.checked=true);
   $("#tlsave").onclick=async()=>{const pick=[...$("#tlprev").querySelectorAll("[data-pre]")].filter(c=>c.checked).map(c=>x.scenarios[+c.dataset.i]);if(!pick.length)return;
    try{const a=cur();const spec={...a.spec,test_scenarios:[...(a.spec.test_scenarios||[]),...pick]};const saved=await api(`/agents/${a.id}`,{method:"PUT",body:JSON.stringify(spec)});a.spec=saved.spec||spec;$("#tlprev").innerHTML='<div class="ok-box">Added.</div>';draw()}catch(e){$("#tlsm").innerHTML=`<div class="bad">${esc(e.message)}</div>`}}}
  catch(e){$("#tlst").textContent="";$("#tlprev").innerHTML=`<div class="bad">${e.status===503?"<b>Configuration needed.</b> ":""}${esc(e.message)}</div>`}b.disabled=false};
 $("#tlrun").onclick=async()=>{const sc=cur().spec.test_scenarios||[];const picks=[...$("#tlcases").querySelectorAll("input:checked")].map(c=>sc[+c.dataset.i]).filter(Boolean);
  if(!picks.length){$("#tlst").textContent="Select at least one case.";return}
  const b=$("#tlrun");b.disabled=true;$("#tlout").innerHTML="";const rows=[];
  for(const [n,s] of picks.entries()){$("#tlst").textContent=`Running case ${n+1} of ${picks.length}...`;
   try{const r=await api(`/agents/${SEL.agent}/run`,{method:"POST",body:JSON.stringify({task:s.task})});const low=(r.response||"").toLowerCase();const miss=(s.must_contain||[]).filter(k=>!low.includes(k.toLowerCase()));
    rows.push({s,r,ok:r.status==="success"&&!!(r.response||"").trim()&&!miss.length,why:r.status!=="success"?"execution error":!(r.response||"").trim()?"empty response":miss.length?"missing: "+miss.join(", "):"keywords present"})}
   catch(e){rows.push({s,r:{response:e.message,status:"error",task:s.task,kind:"run",latency_ms:0,tool_calls:[],created_at:new Date().toISOString(),provider:"",model:"",agent_id:SEL.agent},ok:false,why:e.message});if(e.status===503)break}}
  $("#tlst").textContent="";const p=rows.filter(x=>x.ok).length;
  $("#tlout").innerHTML=`<div class="panel"><h3>Results: ${p} of ${rows.length} passed</h3><p class="muted">Checks are basic (not empty, expected keywords). Read the answers yourself too.</p>${rows.map(x=>`<details class="rcase ${x.ok?"ok":"no"}"><summary><span class="tag ${x.ok?"ok":"fail"}">${x.ok?"pass":"fail"}</span><b>${esc(x.s.name)}</b><small class="muted"> ${esc(x.why)}</small></summary><div class="rbody"><p class="muted">Input</p><pre>${esc(x.s.task)}</pre><p class="muted">Answer</p><pre>${esc(x.r.response||"")}</pre></div></details>`).join("")}</div>`;
  b.disabled=false;hist()};
 $("#tlhand").onclick=async()=>{let p=null;try{p=await api(`/agents/${SEL.agent}/delivery-preview`)}catch(e){}
  $("#tlsteps").innerHTML=(p?"":'<p class="muted">Repository details appear once GitHub is connected; these are the generic steps.</p>')+handoverSteps(p,cur().spec.agent_name)};
 hist()}

async function refreshUnread(){try{const n=(await api("/messages")).filter(m=>!m.read).length;const d=document.getElementById("unread");if(d){d.hidden=!n;d.title=n+" unread"}}catch(e){}}

// Accessibility pass for the dynamically rendered dashboard: associate labels with controls, name unlabeled controls, make scroll areas keyboard-reachable.
function a11y(root){root=root||document;let n=0;
 root.querySelectorAll("label:not([for])").forEach(l=>{const c=l.nextElementSibling&&l.nextElementSibling.matches("input,select,textarea")?l.nextElementSibling:l.querySelector("input,select,textarea");
  if(c&&!c.closest("label")){if(!c.id)c.id="a11y"+(++n)+"_"+Math.random().toString(36).slice(2,6);l.htmlFor=c.id}});
 root.querySelectorAll("input,select,textarea").forEach(c=>{if(c.type==="hidden"||c.getAttribute("aria-label")||c.getAttribute("aria-labelledby")||(c.labels&&c.labels.length))return;
  const nm=c.getAttribute("placeholder")||c.getAttribute("title")||(c.closest("div,section")&&c.closest("div,section").querySelector("label,.muted,h3,h2")||{}).textContent||c.name||c.id||"Field";c.setAttribute("aria-label",nm.trim().slice(0,60))});
 root.querySelectorAll(".panel,.log,.tree,.dv,pre,.pipe-panel").forEach(e=>{if(!e.hasAttribute("tabindex")&&(e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1))e.setAttribute("tabindex","0")})}
