const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let TOKEN=sessionStorage.getItem("ft"),VIEW="overview",SEL={req:null,agent:null},PROV=null;
async function api(path,opt={}){const r=await fetch("/api"+path,{...opt,headers:{"Content-Type":"application/json","X-Factory-Token":TOKEN||"",...(opt.headers||{})}});
 if(r.status===401&&path!=="/factory/login"){logout();throw new Error("Session expired")}
 if(opt.blob&&r.ok)return r.blob();const j=await r.json().catch(()=>({}));
 if(!r.ok){const d=j.detail;throw Object.assign(new Error(Array.isArray(d)?d.map(x=>x.loc.slice(1).join(".")+": "+x.msg).join("; "):d||"Request failed"),{status:r.status})}return j}
function logout(){sessionStorage.removeItem("ft");TOKEN=null;$("#app").hidden=true;$("#login").hidden=false}
$("#lf").onsubmit=async e=>{e.preventDefault();$("#lerr").hidden=true;try{const j=await api("/factory/login",{method:"POST",body:JSON.stringify({password:$("#pw").value})});$("#lerr").textContent="";TOKEN=j.token;sessionStorage.setItem("ft",TOKEN);start()}catch(x){$("#lerr").textContent=x.message;$("#lerr").hidden=false}};
$("#signout").onclick=logout;
document.querySelectorAll("button.nav").forEach(b=>b.onclick=()=>go(b.dataset.v));
function go(v){VIEW=v;document.querySelectorAll("button.nav").forEach(b=>b.classList.toggle("on",b.dataset.v===v));render()}
async function start(){$("#login").hidden=true;$("#app").hidden=false;await loadProv();render()}
async function loadProv(){PROV=await api("/provider");const L={anthropic:"Anthropic API",openai:"OpenAI API",claude_subscription:"Claude subscription (local demo only)"};
 $("#prov").innerHTML=Object.entries(PROV.providers).map(([k,v])=>`<option value="${k}" ${k===PROV.current?"selected":""}>${L[k]}${v.configured?"":" - not configured"}</option>`).join("");
 const c=PROV.providers[PROV.current];$("#provnote").textContent=c.configured?c.model:"";$("#provnote").title=c.issue||""}
$("#prov").onchange=async e=>{PROV=await api("/provider",{method:"PUT",body:JSON.stringify({provider:e.target.value})});await loadProv();if(VIEW!=="overview")render()};
function cfgBanner(){const c=PROV.providers[PROV.current];return c.configured?"":`<div class="bad"><b>Provider not configured.</b> ${esc(c.issue)} Or switch provider in the header.</div>`}
const fmt=t=>t?new Date(t).toLocaleString():"";
async function render(){const v=$("#view");v.innerHTML='<p class="muted">Loading...</p>';
 try{await ({overview,requests,builder,playground}[VIEW])(v)}catch(e){v.innerHTML=`<div class="bad">${esc(e.message)}</div>`}}

async function overview(v){const s=await api("/stats");
 v.innerHTML=`<h2>Overview</h2><p class="muted">Live counts from the database.</p><div class="tiles">
 <div class="tile"><div class="n">${s.total_requests}</div>Client requests</div><div class="tile"><div class="n">${s.agents_generated}</div>Agents generated</div>
 <div class="tile"><div class="n">${s.agents_tested}</div>Agents tested</div><div class="tile"><div class="n">${s.agents_dispatched}</div>Agents dispatched</div></div>
 <div class="panel"><h3>Recent activity</h3>${s.recent_activity.length?`<table>${s.recent_activity.map(a=>`<tr><td><span class="tag">${a.type}</span>${esc(a.text)}</td><td class="muted">${fmt(a.at)}</td></tr>`).join("")}</table>`:'<div class="empty">No activity yet. Submit a request on the public site to begin.</div>'}</div>`}

async function requests(v){const rs=await api("/requests");if(!SEL.req&&rs[0])SEL.req=rs[0].id;
 v.innerHTML=`<h2>Client Requests</h2><p class="muted">${rs.length} submitted</p>${rs.length?`<div class="split"><div>${rs.map(r=>`<div class="item ${r.id===SEL.req?"on":""}" data-id="${r.id}"><b>${esc(r.company_name)}</b><br><small>${r.id} · ${esc(r.agent_type)}${r.agent_count?` · ${r.agent_count} agent(s)`:""}</small></div>`).join("")}</div><div class="panel" id="detail"></div></div>`:'<div class="empty">No requests yet. Open the public site and submit one.</div>'}`;
 if(!rs.length)return;const show=()=>{const r=rs.find(x=>x.id===SEL.req)||rs[0];
  $("#detail").innerHTML=`<h3>${esc(r.company_name)} <span class="tag">${esc(r.agent_type)}</span></h3><p class="muted">${r.id} · ${fmt(r.created_at)}</p><dl>
  ${[["Industry","industry"],["Business problem","business_problem"],["Tasks","tasks"],["Existing tools","existing_tools"],["Required output","required_output"],["Contact","contact_email"]].map(([l,k])=>`<dt>${l}</dt><dd>${esc(r[k])}</dd>`).join("")}</dl>
  <div class="row" style="margin-top:16px"><button class="btn" id="gen">Build agent from this request</button>${r.agent_count?`<span class="muted">${r.agent_count} agent(s) already generated for this request</span>`:""}</div>`;
  $("#gen").onclick=()=>{SEL.req=r.id;SEL.autogen=true;go("builder")}};
 show();document.querySelectorAll(".item").forEach(i=>i.onclick=()=>{SEL.req=i.dataset.id;document.querySelectorAll(".item").forEach(x=>x.classList.toggle("on",x===i));show()})}

const L=(a)=>(a||[]).join("\n"),J=o=>JSON.stringify(o,null,2);
async function builder(v){const [rs,agents]=await Promise.all([api("/requests"),api("/agents")]);
 const reqAgents=agents.filter(a=>a.request_id===SEL.req);if(!SEL.agent||!agents.find(a=>a.id===SEL.agent))SEL.agent=reqAgents[0]?.id||agents[0]?.id||null;
 v.innerHTML=`<h2>Agent Builder</h2><p class="muted">Turn a client requirement into an editable agent specification.</p>${cfgBanner()}
 <div class="panel"><div class="row"><select id="rq" style="max-width:380px"><option value="">Select a client request...</option>${rs.map(r=>`<option value="${r.id}" ${r.id===SEL.req?"selected":""}>${r.id} - ${esc(r.company_name)} (${esc(r.agent_type)})</option>`).join("")}</select>
 <button class="btn" id="gen">Generate Agent</button><span id="gst" class="muted"></span></div><div id="gerr"></div></div>
 <div class="panel"><div class="row"><b>Agents</b><select id="ag" style="max-width:420px"><option value="">${agents.length?"Select an agent...":"No agents yet"}</option>${agents.map(a=>`<option value="${a.id}" ${a.id===SEL.agent?"selected":""}>${a.id} - ${esc(a.spec.agent_name)}</option>`).join("")}</select></div></div><div id="spec"></div>`;
 $("#rq").onchange=e=>SEL.req=e.target.value;$("#ag").onchange=e=>{SEL.agent=e.target.value;showSpec(agents.find(a=>a.id===SEL.agent))};
 $("#gen").onclick=async()=>{if(!$("#rq").value){$("#gerr").innerHTML='<div class="bad">Select a client request first.</div>';return}
  SEL.req=$("#rq").value;$("#gen").disabled=true;$("#gst").textContent="Calling "+PROV.current+" - this can take 10-60s...";$("#gerr").innerHTML="";
  try{const a=await api(`/requests/${SEL.req}/generate-agent`,{method:"POST"});SEL.agent=a.id;await builder(v)}catch(e){$("#gerr").innerHTML=`<div class="bad">${e.status===503?"<b>Configuration needed.</b> ":e.status===502?"<b>Model/provider error.</b> ":""}${esc(e.message)}</div>`;$("#gen").disabled=false;$("#gst").textContent=""}};
 if(SEL.autogen){SEL.autogen=false;$("#gen").click()}else showSpec(agents.find(a=>a.id===SEL.agent))}

function showSpec(a){const el=$("#spec");if(!a){el.innerHTML="";return}const s=a.spec;
 el.innerHTML=`<div class="panel"><div class="row" style="justify-content:space-between"><h3>${esc(s.agent_name)} <span class="tag">${a.id}</span></h3><span class="muted">Generated by ${esc(a.provider)} / ${esc(a.model)}</span></div>
 <label>Agent name</label><input id="f_agent_name" value="${esc(s.agent_name)}">
 <label>Purpose</label><textarea id="f_purpose">${esc(s.purpose)}</textarea>
 <label>Business problem</label><textarea id="f_business_problem">${esc(s.business_problem)}</textarea>
 <label>Instructions (system prompt)</label><textarea id="f_instructions" style="min-height:160px">${esc(s.instructions)}</textarea>
 <label>Capabilities (one per line)</label><textarea id="f_capabilities">${esc(L(s.capabilities))}</textarea>
 <label>Required tools (one per line: calculator, lookup_business_data)</label><textarea id="f_required_tools" style="min-height:50px">${esc(L(s.required_tools))}</textarea>
 <label>Data sources (one per line)</label><textarea id="f_data_sources" style="min-height:50px">${esc(L(s.data_sources))}</textarea>
 <label>Safety restrictions (one per line)</label><textarea id="f_safety_restrictions">${esc(L(s.safety_restrictions))}</textarea>
 <label>Success criteria (one per line)</label><textarea id="f_success_criteria">${esc(L(s.success_criteria))}</textarea>
 <label>Input schema (JSON)</label><textarea class="code" id="f_input_schema">${esc(J(s.input_schema))}</textarea>
 <label>Output schema (JSON)</label><textarea class="code" id="f_output_schema">${esc(J(s.output_schema))}</textarea>
 <label>Test scenarios (JSON: name, task, expected_behavior, must_contain[], must_call_tool)</label><textarea class="code" id="f_test_scenarios" style="min-height:200px">${esc(J(s.test_scenarios))}</textarea>
 <div id="serr"></div><div class="row" style="margin-top:16px"><button class="btn" id="save">Save Changes</button><button class="btn" id="dsp">Dispatch to customer</button><button class="btn sec" id="pg">Open in Playground</button>
 <button class="btn sec" data-ex="portable">Export: portable</button><button class="btn sec" data-ex="crewai">Export: CrewAI scaffold</button><span id="sok" class="muted"></span></div>
 <p class="muted" style="font-size:12px">Exports are starter scaffolds for client handover, not tested production deployments.</p></div><div id="dispatch"></div>`;
 $("#save").onclick=async()=>{$("#serr").innerHTML="";$("#sok").textContent="";const g=k=>$("#f_"+k).value,ls=k=>g(k).split("\n").map(x=>x.trim()).filter(Boolean);
  try{const spec={agent_name:g("agent_name"),purpose:g("purpose"),business_problem:g("business_problem"),instructions:g("instructions"),capabilities:ls("capabilities"),
   input_schema:JSON.parse(g("input_schema")),output_schema:JSON.parse(g("output_schema")),required_tools:ls("required_tools"),data_sources:ls("data_sources"),
   safety_restrictions:ls("safety_restrictions"),test_scenarios:JSON.parse(g("test_scenarios")),success_criteria:ls("success_criteria")};
   await api("/agents/"+a.id,{method:"PUT",body:JSON.stringify(spec)});$("#sok").textContent="Saved ✓"}catch(e){$("#serr").innerHTML=`<div class="bad">${e instanceof SyntaxError?"Invalid JSON: ":""}${esc(e.message)}</div>`}};
 $("#pg").onclick=()=>go("playground");
 const dl=async(path,name)=>{const blob=await api(path,{blob:true});const u=URL.createObjectURL(blob),l=document.createElement("a");l.href=u;l.download=name;l.click();URL.revokeObjectURL(u)};
 const loadKeys=async d=>{const ks=await api(`/dispatches/${d.id}/keys`);const h=await fetch("/api/health").then(r=>r.json());const base=h.public_base_url||location.origin;
  const sub=PROV.current==="claude_subscription";
  $("#keys").innerHTML=`<h3>Customer API access</h3><p class="muted">Give the customer a key to call this agent on Nexara's server: <code>POST ${esc(base)}/v1/run</code>. Keys are shown once and stored only as a hash.</p>
  ${sub?'<div class="warn" style="background:#2a2108;border:1px solid #6b5416;color:#f5d98a;border-radius:8px;padding:10px 12px;font-size:13px;margin:10px 0">The customer API is <b>disabled</b> while the Claude-subscription provider is selected (local demo only). Switch to the Anthropic or OpenAI provider in the header.</div>':""}
  ${/localhost|127\.0\.0\.1/.test(base)?'<div class="warn" style="background:#2a2108;border:1px solid #6b5416;color:#f5d98a;border-radius:8px;padding:10px 12px;font-size:13px;margin:10px 0">This server is on <b>localhost</b>, so a customer cannot reach it. Deploy it publicly and set PUBLIC_BASE_URL, or use the package instead.</div>':""}
  <div class="row"><input id="k_label" placeholder="Label, e.g. Acme production" style="max-width:300px" maxlength="80"><button class="btn" id="k_new">Create API key</button><span id="k_msg" class="muted"></span></div><div id="k_new_box"></div>
  ${ks.length?`<table style="margin-top:12px"><tr><th>Label</th><th>Key</th><th>Calls</th><th>Last used</th><th>Status</th><th></th></tr>${ks.map(k=>`<tr><td>${esc(k.label)}</td><td><code>${esc(k.prefix)}…</code></td><td>${k.request_count}</td><td class="muted">${k.last_used_at?fmt(k.last_used_at):"never"}</td><td><span class="tag ${k.revoked_at?"fail":"ok"}">${k.revoked_at?"revoked":"active"}</span></td><td>${k.revoked_at?"":`<button class="btn sec" data-rev="${k.id}">Revoke</button>`}</td></tr>`).join("")}</table>`:'<p class="muted">No keys yet.</p>'}`;
  $("#k_new").onclick=async()=>{$("#k_new").disabled=true;try{const k=await api(`/dispatches/${d.id}/keys`,{method:"POST",body:JSON.stringify({label:$("#k_label").value.trim()||"Customer key"})});
    const curl=`curl -s ${base}/v1/run -H "Authorization: Bearer ${k.key}" -H "Content-Type: application/json" -d '{"task": "your task here"}'`;
    await loadKeys(d);$("#k_new_box").innerHTML=`<div class="panel" style="border-color:var(--lime)"><b>New key - copy it now, it will not be shown again</b><pre id="k_val">${esc(k.key)}</pre><pre>${esc(curl)}</pre><button class="btn sec" id="k_copy">Copy key</button> <button class="btn sec" id="k_copycurl">Copy curl example</button></div>`;
    $("#k_copy").onclick=()=>navigator.clipboard.writeText(k.key).then(()=>$("#k_copy").textContent="Copied");$("#k_copycurl").onclick=()=>navigator.clipboard.writeText(curl).then(()=>$("#k_copycurl").textContent="Copied")}
   catch(e){$("#k_msg").textContent=e.message;$("#k_new").disabled=false}};
  $("#keys").querySelectorAll("[data-rev]").forEach(b=>b.onclick=async()=>{if(!confirm("Revoke this key? The customer's calls will start failing immediately."))return;try{await api(`/keys/${b.dataset.rev}/revoke`,{method:"POST"});loadKeys(d)}catch(e){$("#k_msg").textContent=e.message}})};
 const drawDispatch=d=>{const sent=d.status==="sent";$("#dispatch").innerHTML=`<div class="panel"><h3>Dispatch ${d.id} <span class="tag ${sent?"ok":""}">${sent?"marked sent "+fmt(d.sent_at):"draft - not sent"}</span></h3>
  <p class="muted">Nexara never sends this automatically. Review the email, attach the package, send it yourself, then mark it as sent.</p>
  <label>To</label><input id="d_to" value="${esc(d.to_email)}"><label>Subject</label><input id="d_sub" value="${esc(d.subject)}"><label>Body (editable draft)</label><textarea id="d_body" style="min-height:240px">${esc(d.body)}</textarea>
  <div class="row" style="margin-top:12px"><button class="btn" id="d_pkg">Download package (.zip)</button><button class="btn sec" id="d_copy">Copy email</button><button class="btn sec" id="d_mail">Open in my mail app</button>${sent?"":'<button class="btn sec" id="d_sent">I sent it - mark as sent</button>'}<span id="d_msg" class="muted"></span></div></div><div class="panel" id="keys"></div>`;
  loadKeys(d);
  $("#d_pkg").onclick=()=>dl(`/dispatches/${d.id}/package`,`${d.id}-agent-package.zip`).catch(e=>$("#d_msg").textContent=e.message);
  $("#d_copy").onclick=async()=>{try{await navigator.clipboard.writeText(`To: ${$("#d_to").value}\nSubject: ${$("#d_sub").value}\n\n${$("#d_body").value}`);$("#d_msg").textContent="Copied"}catch{$("#d_msg").textContent="Copy failed - select the text manually"}};
  $("#d_mail").onclick=()=>{location.href=`mailto:${encodeURIComponent($("#d_to").value)}?subject=${encodeURIComponent($("#d_sub").value)}&body=${encodeURIComponent($("#d_body").value)}`};
  if(!sent)$("#d_sent").onclick=async()=>{try{drawDispatch({...d,...await api(`/dispatches/${d.id}/mark-sent`,{method:"POST"})})}catch(e){$("#d_msg").textContent=e.message}}};
 $("#dsp").onclick=async()=>{$("#dsp").disabled=true;try{drawDispatch(await api(`/agents/${a.id}/dispatch`,{method:"POST"}))}catch(e){$("#serr").innerHTML=`<div class="bad">${esc(e.message)}</div>`}$("#dsp").disabled=false};
 api(`/agents/${a.id}/dispatches`).then(ds=>{if(ds.length&&!$("#dispatch").innerHTML)$("#dispatch").innerHTML=`<div class="panel"><h3>Previous dispatches</h3><table>${ds.map(x=>`<tr><td>${x.id}</td><td><span class="tag ${x.status==="sent"?"ok":""}">${x.status}</span></td><td class="muted">${esc(x.to_email)} · ${fmt(x.created_at)}</td><td><button class="btn sec" data-open="${x.id}">Open</button></td></tr>`).join("")}</table></div>`;$("#dispatch").querySelectorAll("[data-open]").forEach(b=>b.onclick=async()=>drawDispatch(await api(`/dispatches/${b.dataset.open}`)))}).catch(()=>{});
 el.querySelectorAll("[data-ex]").forEach(b=>b.onclick=async()=>{try{const blob=await api(`/agents/${a.id}/export?format=${b.dataset.ex}`,{blob:true});const u=URL.createObjectURL(blob),l=document.createElement("a");l.href=u;l.download=`${a.id}-${b.dataset.ex}.zip`;l.click();URL.revokeObjectURL(u)}catch(e){$("#serr").innerHTML=`<div class="bad">${esc(e.message)}</div>`}})}

const EXAMPLES={"Customer Support":"Draft a response to a customer whose order ORD-1001 has been delayed.","Data Analyst":"Analyze the sales figures and identify the best-performing product by revenue."};
async function playground(v){const agents=await api("/agents");if(!SEL.agent||!agents.find(a=>a.id===SEL.agent))SEL.agent=agents[0]?.id||null;
 if(!agents.length){v.innerHTML='<h2>Agent Playground</h2><div class="empty">No agents yet. Generate one in the Agent Builder.</div>';return}
 v.innerHTML=`<h2>Agent Playground</h2><p class="muted">Run real tasks through a saved agent. Tools: calculator, lookup_business_data (sales, customers, orders).</p>${cfgBanner()}
 <div class="panel"><label style="margin-top:0">Agent</label><select id="ag">${agents.map(a=>`<option value="${a.id}" ${a.id===SEL.agent?"selected":""}>${a.id} - ${esc(a.spec.agent_name)}</option>`).join("")}</select>
 <label>Task</label><textarea id="task" placeholder="Enter a test task..."></textarea><div class="chips" id="chips"></div>
 <div class="row"><button class="btn" id="run">Run task</button><button class="btn sec" id="test">Run Test (scenarios)</button><span id="st" class="muted"></span></div></div><div id="out"></div><div id="hist"></div>`;
 const cur=()=>agents.find(a=>a.id===SEL.agent);
 const chips=()=>{const a=cur();$("#chips").innerHTML=[...Object.values(EXAMPLES),...a.spec.test_scenarios.map(t=>t.task)].slice(0,5).map(t=>`<button class="chip">${esc(t.slice(0,70))}</button>`).join("");
  $("#chips").querySelectorAll(".chip").forEach((c,i)=>c.onclick=()=>$("#task").value=[...Object.values(EXAMPLES),...a.spec.test_scenarios.map(t=>t.task)][i])};
 chips();$("#ag").onchange=e=>{SEL.agent=e.target.value;chips();hist()};
 const hist=async()=>{const rs=await api("/runs?agent_id="+SEL.agent);$("#hist").innerHTML=rs.length?`<div class="panel"><h3>Execution history</h3>${rs.slice(0,8).map(logHtml).join("")}</div>`:""};
 $("#run").onclick=async()=>{const t=$("#task").value.trim();if(!t)return;busy(true,"Running...");$("#out").innerHTML="";
  try{const r=await api(`/agents/${SEL.agent}/run`,{method:"POST",body:JSON.stringify({task:t})});$("#out").innerHTML=`<div class="panel"><h3>Execution log</h3>${logHtml(r,true)}</div>`;hist()}
  catch(e){$("#out").innerHTML=`<div class="bad">${e.status===503?"<b>Configuration needed.</b> ":""}${esc(e.message)}</div>`}busy(false)};
 $("#test").onclick=async()=>{busy(true,"Running scenarios (up to 3 model runs)...");$("#out").innerHTML="";
  try{const r=await api(`/agents/${SEL.agent}/evaluate`,{method:"POST"});
   $("#out").innerHTML=`<div class="panel"><h3>Test results · pass rate ${Math.round(r.pass_rate*100)}%</h3><p class="muted">${esc(r.note)}</p><table><tr><th>Test</th><th>Expected</th><th>Actual</th><th>Result</th></tr>${r.results.map(x=>`<tr><td>${esc(x.name)}<br><small class="muted">${esc(x.task)}</small></td><td>${esc(x.expected_behavior)}</td><td><pre>${esc(x.actual.slice(0,500))}</pre>${x.tool_calls.map(c=>`<span class="tag">${esc(c.tool)}</span>`).join("")}</td><td><span class="tag ${x.passed?"ok":"fail"}">${x.passed?"PASS":"FAIL"}</span><br><small class="muted">${esc(x.reason)}</small></td></tr>`).join("")}</table></div>`;hist()}
  catch(e){$("#out").innerHTML=`<div class="bad">${e.status===503?"<b>Configuration needed.</b> ":""}${esc(e.message)}</div>`}busy(false)};
 const busy=(b,m="")=>{["#run","#test"].forEach(s=>$(s).disabled=b);$("#st").textContent=m};hist()}

function logHtml(r,open){return `<details ${open?"open":""} style="margin:8px 0"><summary><span class="tag ${r.status==="success"?"ok":"fail"}">${r.status}</span><span class="tag">${r.kind}</span>${esc(r.task.slice(0,80))} <small class="muted">· ${r.latency_ms} ms · ${fmt(r.created_at)}</small></summary>
 <dl style="margin-top:8px"><dt>Agent</dt><dd>${r.agent_id}</dd><dt>Input task</dt><dd>${esc(r.task)}</dd><dt>Provider / model</dt><dd>${esc(r.provider)} / ${esc(r.model)}</dd><dt>Status</dt><dd>${r.status}</dd>
 <dt>Latency</dt><dd>${r.latency_ms} ms</dd><dt>Tokens</dt><dd>${r.input_tokens!=null?`in ${r.input_tokens} / out ${r.output_tokens}`:"not reported"}</dd>
 <dt>Tool calls</dt><dd>${r.tool_calls.length?r.tool_calls.map(c=>`<pre>${esc(c.tool)}(${esc(JSON.stringify(c.args))})\n→ ${c.ok?esc(JSON.stringify(c.result)):"ERROR: "+esc(c.error)}</pre>`).join(""):"none"}</dd>
 <dt>Response</dt><dd>${esc(r.response)}</dd>${r.error?`<dt>Error</dt><dd style="color:var(--err)">${esc(r.error)}</dd>`:""}</dl></details>`}
if(TOKEN)api("/stats").then(start).catch(()=>logout());
