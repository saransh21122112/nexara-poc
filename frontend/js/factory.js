const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let TOKEN=sessionStorage.getItem("ft"),VIEW="overview",SEL={req:null,agent:null,cust:null},PROV=null;
async function api(path,opt={}){const r=await fetch("/api"+path,{...opt,headers:{"Content-Type":"application/json","X-Factory-Token":TOKEN||"",...(opt.headers||{})}});
 if(r.status===401&&path!=="/factory/login"){logout();throw new Error("Session expired")}
 if(opt.blob&&r.ok)return r.blob();const j=await r.json().catch(()=>({}));
 if(!r.ok){const d=j.detail;throw Object.assign(new Error(Array.isArray(d)?d.map(x=>x.loc.slice(1).join(".")+": "+x.msg).join("; "):d||"Request failed"),{status:r.status,body:j})}return j}
function logout(){sessionStorage.removeItem("ft");TOKEN=null;$("#app").hidden=true;$("#login").hidden=false}
$("#lf").onsubmit=async e=>{e.preventDefault();$("#lerr").hidden=true;try{const j=await api("/factory/login",{method:"POST",body:JSON.stringify({password:$("#pw").value})});$("#lerr").textContent="";TOKEN=j.token;sessionStorage.setItem("ft",TOKEN);start()}catch(x){$("#lerr").textContent=x.message;$("#lerr").hidden=false}};
$("#signout").onclick=logout;
document.querySelectorAll("button.nav").forEach(b=>b.onclick=()=>go(b.dataset.v));
function go(v){VIEW=v;document.querySelectorAll("button.nav").forEach(b=>b.classList.toggle("on",b.dataset.v===v));render()}
async function loadGh(){try{const g=await api("/github/status"),m=await api("/mail/status");$("#ghnote").textContent=(g.ok?`GitHub: ${g.owner} ✓`:(g.configured?"GitHub: token problem":"GitHub: not configured"))+" · "+(m.verification_required?(m.configured?`Mail: ${m.mode} ✓`:"Mail: not configured"):"Email verification OFF");$("#ghnote").title=[g.issue,m.verification_required?m.issue:"REQUIRE_EMAIL_VERIFICATION=false: requests are not email-verified"].filter(Boolean).join(" | ")}catch{}}
async function start(){$("#login").hidden=true;$("#app").hidden=false;await loadProv();loadGh();render()}
async function loadProv(){PROV=await api("/provider");const L={anthropic:"Anthropic API",openai:"OpenAI API",claude_subscription:"Claude subscription (local demo only)"};
 $("#prov").innerHTML=Object.entries(PROV.providers).map(([k,v])=>`<option value="${k}" ${k===PROV.current?"selected":""}>${L[k]}${v.configured?"":" - not configured"}</option>`).join("");
 const c=PROV.providers[PROV.current];$("#provnote").textContent=c.configured?c.model:"";$("#provnote").title=c.issue||""}
$("#prov").onchange=async e=>{PROV=await api("/provider",{method:"PUT",body:JSON.stringify({provider:e.target.value})});await loadProv();if(VIEW!=="overview")render()};
function cfgBanner(){const c=PROV.providers[PROV.current];return c.configured?"":`<div class="bad"><b>Provider not configured.</b> ${esc(c.issue)} Or switch provider in the header.</div>`}
const fmt=t=>t?new Date(t).toLocaleString():"";
function countUp(root){root.querySelectorAll(".tile .n").forEach(el=>{const to=+el.textContent;if(!isFinite(to)||matchMedia("(prefers-reduced-motion:reduce)").matches)return;let t0;const f=t=>{t0??=t;const p=Math.min((t-t0)/700,1);el.textContent=Math.round(to*(1-Math.pow(1-p,3)));if(p<1)requestAnimationFrame(f)};requestAnimationFrame(f)})}
async function render(){const v=$("#view");v.innerHTML='<div class="sk big"></div><div class="sk"></div><div class="sk" style="width:70%"></div>';
 try{await ({overview,requests,customers,builder,playground}[VIEW])(v);countUp(v)}catch(e){v.innerHTML=`<div class="bad">${esc(e.message)}</div>`}}

async function overview(v){const s=await api("/stats");
 v.innerHTML=`<h2>Overview</h2><p class="muted">Live counts from the database.</p><div class="tiles">
 <div class="tile"><div class="n">${s.total_requests}</div>Client requests</div><div class="tile"><div class="n">${s.agents_generated}</div>Agents generated</div>
 <div class="tile"><div class="n">${s.agents_tested}</div>Agents tested</div><div class="tile"><div class="n">${s.agents_delivered}</div>Agents delivered</div><div class="tile"><div class="n">${s.customers}</div>Customers</div></div>
 <div class="panel"><h3>Recent activity</h3>${s.recent_activity.length?`<table>${s.recent_activity.map(a=>`<tr><td><span class="tag">${a.type}</span>${esc(a.text)}</td><td class="muted">${fmt(a.at)}</td></tr>`).join("")}</table>`:'<div class="empty">No activity yet. Submit a request on the public site to begin.</div>'}</div>`}

async function requests(v){const [rs,cs]=await Promise.all([api("/requests"),api("/customers")]);if(!SEL.req&&rs[0])SEL.req=rs[0].id;
 v.innerHTML=`<h2>Client Requests</h2><p class="muted">${rs.length} submitted</p>${rs.length?`<div class="split"><div>${rs.map(r=>`<div class="item ${r.id===SEL.req?"on":""}" data-id="${r.id}"><b>${esc(r.company_name)}</b><br><small>${r.id} · ${esc(r.agent_type)}${r.agent_count?` · ${r.agent_count} agent(s)`:""}</small></div>`).join("")}</div><div class="panel" id="detail"></div></div>`:'<div class="empty">No requests yet. Open the public site and submit one.</div>'}`;
 if(!rs.length)return;const show=()=>{const r=rs.find(x=>x.id===SEL.req)||rs[0];
  $("#detail").innerHTML=`<h3>${esc(r.company_name)} <span class="tag">${esc(r.agent_type)}</span></h3><p class="muted">${r.id} · ${fmt(r.created_at)}</p><dl>
  ${[["Industry","industry"],["Business problem","business_problem"],["Tasks","tasks"],["Existing tools","existing_tools"],["Required output","required_output"],["Contact","contact_email"],["GitHub","github_username"],["Claude","claude_status"],["OS","os"]].map(([l,k])=>`<dt>${l}</dt><dd>${esc(r[k])}</dd>`).join("")}</dl>
  <div class="row" style="margin-top:12px"><span class="muted">Customer: <b>${esc((cs.find(c=>c.id===r.customer_id)||{}).company_name||"-")}</b> ${esc(r.customer_id||"")}</span><select id="att" style="max-width:420px">${cs.map(c=>`<option value="${c.id}" ${c.id===r.customer_id?"selected":""}>${esc(c.company_name)} · ${esc(c.contact_email)}${c.github_username?" · @"+esc(c.github_username):""} (${esc(c.id)})</option>`).join("")}</select><button class="btn sec" id="attb">Attach to this customer</button></div><div id="attc"></div>
  <div class="row" style="margin-top:12px"><span class="muted">Requester email: ${r.email_verified?`<span class="tag ok">verified (${esc(r.email_verified_via||"")})</span>`:'<span class="tag fail">not verified</span>'}</span>${r.email_verified?"":'<button class="btn sec" id="vrs">Resend confirmation email</button><button class="btn sec" id="vmn">Mark verified...</button>'}</div><div id="vfc"></div>
  <div class="row" style="margin-top:16px"><button class="btn" id="gen">Build agent from this request</button>${r.agent_count?`<span class="muted">${r.agent_count} agent(s) already generated for this request</span>`:""}</div>`;
  $("#attb").onclick=()=>{const to=$("#att").value;if(to===r.customer_id){$("#attc").innerHTML='<p class="muted">Already attached to that customer.</p>';return}
   confirmBar($("#attc"),`Link request ${r.id} (from ${r.contact_email}) to the customer you selected. Its agents can then be delivered into THAT customer's repository. Only do this if you have verified they are the same company.`,"Attach",async()=>{try{await api(`/requests/${r.id}/attach-customer`,{method:"POST",body:JSON.stringify({customer_id:to,confirm:true})});requests(v)}catch(e){$("#attc").innerHTML=`<div class="bad">${esc(e.message)}</div>`}})};
  if($("#vrs")){
   $("#vrs").onclick=()=>confirmBar($("#vfc"),`This emails a confirmation link to ${r.contact_email}.`,"Send email",async()=>{try{const x=await api(`/requests/${r.id}/resend-verification`,{method:"POST",body:JSON.stringify({confirm:true})});$("#vfc").innerHTML=`<p class="muted">${x.status==="logged"?"Logged to the server log (MAIL_MODE=log).":"Email sent."}</p>`}catch(e){$("#vfc").innerHTML=`<div class="bad">${esc(e.message)}</div>`}});
   $("#vmn").onclick=()=>{$("#vfc").innerHTML=`<div class="row" style="margin-top:8px"><input id="vnote" placeholder="How did you verify them? (e.g. confirmed by phone on 11 Oct)" maxlength="500" style="max-width:520px"><button class="btn sec" id="vrev">Review</button></div>`;
    $("#vrev").onclick=()=>{const n=$("#vnote").value.trim();if(n.length<5){$("#vnote").placeholder="Please write a few words about how you verified them";return}
     confirmBar($("#vfc"),`Record that YOU verified ${r.contact_email} yourself (${n}). Agents can then be delivered for this request.`,"Mark verified",async()=>{try{await api(`/requests/${r.id}/verify-manually`,{method:"POST",body:JSON.stringify({confirm:true,note:n})});requests(v)}catch(e){$("#vfc").innerHTML=`<div class="bad">${esc(e.message)}</div>`}})}}}
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
 <div id="serr"></div><div class="row" style="margin-top:16px"><button class="btn" id="save">Save Changes</button><button class="btn" id="dlv">Deliver to customer repo</button><button class="btn sec" id="pg">Open in Playground</button><span id="sok" class="muted"></span></div>
 <p class="muted" style="font-size:12px">Delivery creates a private GitHub repo for this customer (or adds to their existing one). Nothing happens until you confirm on the next screen.</p></div><div id="delivery"></div>`;
 $("#save").onclick=async()=>{$("#serr").innerHTML="";$("#sok").textContent="";const g=k=>$("#f_"+k).value,ls=k=>g(k).split("\n").map(x=>x.trim()).filter(Boolean);
  try{const spec={agent_name:g("agent_name"),purpose:g("purpose"),business_problem:g("business_problem"),instructions:g("instructions"),capabilities:ls("capabilities"),
   input_schema:JSON.parse(g("input_schema")),output_schema:JSON.parse(g("output_schema")),required_tools:ls("required_tools"),data_sources:ls("data_sources"),
   safety_restrictions:ls("safety_restrictions"),test_scenarios:JSON.parse(g("test_scenarios")),success_criteria:ls("success_criteria")};
   await api("/agents/"+a.id,{method:"PUT",body:JSON.stringify(spec)});$("#sok").textContent="Saved ✓"}catch(e){$("#serr").innerHTML=`<div class="bad">${e instanceof SyntaxError?"Invalid JSON: ":""}${esc(e.message)}</div>`}};
 $("#pg").onclick=()=>go("playground");
 $("#dlv").onclick=()=>showDelivery(a);
}

// ---------- delivery: preview -> explicit confirm -> private GitHub repo ----------
const ghNote=()=>fetch("/api/health").then(r=>r.json()).then(h=>h.github_configured);
async function showDelivery(a){const box=$("#delivery");box.innerHTML='<div class="panel"><p class="muted">Checking GitHub...</p></div>';
 let p;try{p=await api(`/agents/${a.id}/delivery-preview`)}catch(e){box.innerHTML=`<div class="panel"><div class="bad">${e.status===503?"<b>GitHub is not configured.</b> ":""}${esc(e.message)}</div></div>`;return}
 const label={create:"Create a private repository and deliver",add:"Add this agent to the customer's existing repository",update:"This agent is already in the repository: use Sync on the Customers page"}[p.mode];
 box.innerHTML=`<div class="panel"><h3>Deliver: ${esc(p.customer.company_name)}</h3><p class="muted">${esc(label)}. Nothing is created until you press the confirm button.</p>
  <dl><dt>Repository</dt><dd><b>${esc(p.repo.owner)}/${esc(p.repo.name)}</b> <span class="tag">private</span> ${p.repo.exists?'<span class="tag ok">exists</span>':'<span class="tag">will be created</span>'}</dd>
  <dt>Request from</dt><dd>${esc(p.request.id)} · ${esc(p.request.contact_email)} · ${p.request.github_username?"@"+esc(p.request.github_username):"no GitHub user"}<br><span class="muted">${esc(p.request.problem)}</span></dd>
  <dt>Command</dt><dd><code>${esc(p.command)}</code> <span class="muted">(inside the company plugin <code>${esc(p.plugin)}</code>)</span></dd>
  <dt>GitHub invite</dt><dd>${p.invite.blocked_reason?`<span class="tag fail">no invitation will be sent</span><br><span class="muted">${esc(p.invite.blocked_reason)}</span>`:p.invite.username?`@${esc(p.invite.username)} - ${p.invite.will_invite?"will be invited":"already invited"}<br><span class="muted">${esc(p.invite.access)}. GitHub emails the invitation.</span>`:"No GitHub username on file - nobody will be invited"}</dd>
  <dt>Customer Claude</dt><dd>${esc(p.customer.claude_status||"unknown")}${p.customer.os?" / "+esc(p.customer.os):""}</dd></dl>
  ${p.blocked.map(b=>`<div class="bad"><b>Cannot deliver yet.</b> ${esc(b)}</div>`).join("")}
  ${p.warnings.map(w=>`<div class="warn">${esc(w)}</div>`).join("")}
  <details style="margin:10px 0"><summary>${p.files.length} files will be written</summary><pre>${esc(p.files.join("\n"))}</pre></details>
  <label>Onboarding message (a draft for you to send - the system never emails the customer)</label><textarea id="ob" style="min-height:220px">${esc(p.onboarding_draft)}</textarea>
  <div class="row" style="margin-top:12px">${p.mode==="update"?"":`<label style="margin:0;text-transform:none"><input type="checkbox" id="inv" style="width:auto" ${p.invite.will_invite?"checked":""} ${p.invite.username?"":"disabled"}> send the GitHub invitation</label>
   <button class="btn" id="go1">${p.mode==="create"?"Create repo and deliver":"Add to repo"}</button>`}<button class="btn sec" id="cp">Copy onboarding message</button><span id="dmsg" class="muted"></span></div><div id="dres"></div></div>`;
 $("#cp").onclick=()=>navigator.clipboard.writeText($("#ob").value).then(()=>$("#dmsg").textContent="Copied",()=>$("#dmsg").textContent="Copy failed - select the text manually");
 if(!$("#go1"))return;if(p.blocked.length){$("#go1").disabled=true;$("#go1").title=p.blocked[0]}
 $("#go1").onclick=()=>confirmBar($("#dres"),`${p.mode==="add"?`This adds to the EXISTING repository of ${p.customer.company_name}, based on a request from ${p.request.contact_email}${p.request.github_username?" (@"+p.request.github_username+")":""}. Only continue if you have checked it really comes from this customer. `:""}This will ${p.mode==="create"?`create the PRIVATE repository ${p.repo.owner}/${p.repo.name}`:`push a new commit to ${p.repo.owner}/${p.repo.name}`}${$("#inv")&&$("#inv").checked?` and email a GitHub invitation to @${p.invite.username}`:""}.`,"Yes, do it",async()=>{
  $("#go1").disabled=true;try{const r=await api(`/agents/${a.id}/deliver`,{method:"POST",body:JSON.stringify({confirm:true,invite:!!($("#inv")&&$("#inv").checked),existing_repo_ack:p.mode==="add"})});
   $("#dres").innerHTML=`<div class="ok-box"><b>${r.status==="pushed"?"Delivered":"Already up to date"}.</b> <a href="${esc(r.repo.url)}" target="_blank" rel="noopener">${esc(r.repo.owner)}/${esc(r.repo.name)}</a> · commit ${esc(r.commit.slice(0,7))} · ${r.changed.length} files
    ${r.invite?`<br>Invite: ${esc(r.invite.status)}${r.invite.error?" - "+esc(r.invite.error):""}`:"<br>No invitation sent."}
    <div class="row" style="margin-top:8px"><button class="btn" id="tocust">Open customer page</button></div></div>`;
   $("#tocust").onclick=()=>{SEL.cust=p.customer.id;go("customers")}}
  catch(e){$("#go1").disabled=false;$("#dres").innerHTML=`<div class="bad">${e.status===503?"<b>GitHub is not configured.</b> ":""}${esc(e.message)}${e.body&&e.body.conflicts?"<pre>"+esc(e.body.conflicts.map(c=>c.path+": "+c.why).join("\n"))+"</pre>":""}</div>`}})}

function confirmBar(el,text,yesLabel,onYes){el.innerHTML=`<div class="warn" style="margin-top:12px"><b>Confirm.</b> ${esc(text)}<div class="row" style="margin-top:8px"><button class="btn" id="cb_yes">${esc(yesLabel)}</button><button class="btn sec" id="cb_no">Cancel</button></div></div>`;
 $("#cb_no").onclick=()=>el.innerHTML="";$("#cb_yes").onclick=()=>{el.innerHTML="";onYes()}}

// ---------- customers ----------
const STATUS={have_claude_code:"has Claude Code",have_claude_account:"has a Claude account",no_claude:"no Claude yet",not_sure:"not sure"};
const invTag=s=>`<span class="tag ${s==="accepted"?"ok":(s==="expired"||s==="failed")?"fail":""}">invite: ${esc(s||"none")}</span>`;
async function customers(v){const cs=await api("/customers");if(!SEL.cust&&cs[0])SEL.cust=cs[0].id;if(SEL.cust&&!cs.find(c=>c.id===SEL.cust))SEL.cust=cs[0]?.id||null;
 v.innerHTML=`<h2>Customers</h2><p class="muted">One private GitHub repository per company. ${cs.length} customer(s).</p>${cs.length?`<div class="split"><div>${cs.map(c=>`<div class="item ${c.id===SEL.cust?"on":""}" data-id="${c.id}"><b>${esc(c.company_name)}</b><br><small>${c.repo?esc(c.repo.owner+"/"+c.repo.name):"no repo yet"} · ${c.delivered_agents} agent(s) · ${c.requests} request(s)</small><br>${c.repo?invTag(c.repo.invite_status):""}</div>`).join("")}</div><div id="cdetail"></div></div>`:'<div class="empty">No customers yet. They appear when someone submits a request.</div>'}`;
 if(!cs.length)return;v.querySelectorAll(".item").forEach(i=>i.onclick=()=>{SEL.cust=i.dataset.id;customers(v)});await customerDetail(SEL.cust,null)}

async function customerDetail(id,live){const d=await api(`/customers/${id}`);const c=d.customer,r=d.repo,el=$("#cdetail");
 const ob=c.onboarding||{};const inv=live?live.invite_status:(r&&r.invite_status);
 const step=(label,done,key)=>`<label style="display:flex;gap:8px;align-items:center;margin:4px 0;text-transform:none;font-size:14px;color:var(--text)"><input type="checkbox" style="width:auto" ${done?"checked":""} ${key?`data-ob="${key}"`:"disabled"}> ${label}</label>`;
 el.innerHTML=`<div class="panel"><h3>${esc(c.company_name)} <span class="tag">${esc(c.id)}</span></h3>
  <dl><dt>Contact</dt><dd>${esc(c.contact_email)}</dd><dt>GitHub</dt><dd>${c.github_username?"@"+esc(c.github_username):"-"}</dd><dt>Claude</dt><dd>${esc(STATUS[c.claude_status]||"unknown")}${c.os?" / "+esc(c.os):""}</dd>
  <dt>Repository</dt><dd>${r?`<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.owner)}/${esc(r.name)}</a> <span class="tag">private</span> <span class="tag ${r.state==="ready"?"ok":"fail"}">${esc(r.state)}</span> ${invTag(inv)}${r.last_error?`<div class="bad">${esc(r.last_error)}</div>`:""}`:"none yet - deliver an agent from the Agent Builder"}</dd></dl>
  ${r?`<div class="row" style="margin-top:12px"><button class="btn sec" id="rf">Refresh from GitHub</button><button class="btn" id="sy" ${d.agents.some(a=>a.behind)?"":"disabled"}>Sync to repo</button><button class="btn sec" id="ri">Re-send invite</button><button class="btn sec" id="zp" title="For customers without GitHub: the current plugin as a zip (no automatic updates)">Download plugin zip</button><button class="btn sec" id="ac">Invite another person</button><span id="cmsg" class="muted"></span></div><div id="cact"></div>`:""}</div>
  ${r?`<div class="panel"><h3>Agents in this repo</h3><table><tr><th>Agent</th><th>Command</th><th>State</th></tr>${d.agents.map(a=>`<tr><td>${esc(a.agent_name)}</td><td><code>${esc(a.command||"/"+a.plugin_slug)}</code></td><td>${a.behind?'<span class="tag fail">repo behind - sync</span>':'<span class="tag ok">up to date</span>'}</td></tr>`).join("")||'<tr><td colspan="3" class="muted">none</td></tr>'}</table></div>
  <div class="panel"><h3>Onboarding</h3>${step("Repo created and agents pushed",true)}${step("Invitation accepted (detected from GitHub)",inv==="accepted")}${step("Customer connected their project / cloned the repo","cloned"in ob&&ob.cloned,"cloned")}${step("Customer used an agent","first_use"in ob&&ob.first_use,"first_use")}${step("Onboarding complete","done"in ob&&ob.done,"done")}</div>
  <div class="panel" id="issues"><h3>Open issues</h3>${live?(live.issues.length?`<table>${live.issues.map(i=>`<tr><td><a href="${esc(i.url)}" target="_blank" rel="noopener">#${i.number} ${esc(i.title)}</a></td><td class="muted">${esc(i.user)}</td></tr>`).join("")}</table>`:'<p class="muted">No open issues.</p>'):'<p class="muted">Press "Refresh from GitHub" to load them.</p>'}${live&&live.manual_changes.length?`<div class="warn">Files changed by hand in the repo: <code>${live.manual_changes.map(esc).join(", ")}</code>. The next sync will ask before overwriting them.</div>`:""}${live&&live.errors.length?`<div class="bad">${live.errors.map(esc).join("<br>")}</div>`:""}</div>`:""}
  <div class="panel"><h3>Requests</h3>${d.requests.map(q=>`<div>${esc(q.id)} · ${esc(q.agent_type)} · ${fmt(q.created_at)} · ${q.agent_count} agent(s) · ${q.email_verified?'<span class="tag ok">email verified</span>':'<span class="tag fail">email not verified</span>'}</div>`).join("")}<p class="muted">A new request from this company becomes another agent in the same repo.</p></div>
  <div class="panel"><h3>Timeline and notes</h3><div class="row"><input id="nt" placeholder="Add a support note..." maxlength="2000"><button class="btn sec" id="na" style="white-space:nowrap">Add note</button></div>
  <table style="margin-top:8px">${d.events.map(e=>`<tr><td class="muted" style="white-space:nowrap">${fmt(e.at)}</td><td><span class="tag ${/failed|conflict|expired/.test(e.kind)?"fail":e.kind==="note"?"":"ok"}">${esc(e.kind.replace(/_/g," "))}</span></td><td>${esc(typeof e.detail==="string"?e.detail:e.detail?Object.entries(e.detail).map(([k,v])=>k+": "+(Array.isArray(v)?v.join(", "):v)).join(" · "):"")}</td></tr>`).join("")||'<tr><td class="muted">No events yet.</td></tr>'}</table></div>`;
 const msg=t=>$("#cmsg")&&($("#cmsg").textContent=t);const redo=l=>customerDetail(id,l);
 el.querySelectorAll("[data-ob]").forEach(b=>b.onchange=async()=>{try{await api(`/customers/${id}/onboarding`,{method:"POST",body:JSON.stringify({step:b.dataset.ob,value:b.checked})})}catch(e){msg(e.message)}});
 {const it=document.querySelector(".item.on .tag");if(it&&r)it.outerHTML=invTag(inv)} // keep the list badge in step with the detail
 $("#na").onclick=async()=>{const t=$("#nt").value.trim();if(!t)return;try{await api(`/customers/${id}/notes`,{method:"POST",body:JSON.stringify({text:t})});redo(live)}catch(e){$("#nt").value=e.message}};
 if(!r)return;
 $("#rf").onclick=async()=>{msg("Asking GitHub...");try{redo(await api(`/customers/${id}/refresh`,{method:"POST"}))}catch(e){msg(e.message)}};
 $("#zp").onclick=async()=>{try{const blob=await api(`/customers/${id}/plugin-zip`,{blob:true});const u=URL.createObjectURL(blob),l=document.createElement("a");l.href=u;l.download=`${(d.agents[0]&&d.agents[0].command||"plugin").split(":")[0].replace("/","")}.zip`;l.click();URL.revokeObjectURL(u);msg("Zip downloaded (a fallback: it does not update itself)")}catch(e){msg(e.message)}};
 $("#ri").onclick=()=>confirmBar($("#cact"),`GitHub will email a NEW invitation to @${r.invited_user||c.github_username||"?"}.`,"Send invite",async()=>{try{await api(`/customers/${id}/reinvite`,{method:"POST",body:JSON.stringify({confirm:true})});redo(live)}catch(e){msg(e.message)}});
 $("#ac").onclick=()=>{$("#cact").innerHTML=`<div class="row" style="margin-top:12px"><input id="au" placeholder="GitHub username" style="max-width:240px"><button class="btn sec" id="au_go">Review invite</button></div>`;
  $("#au_go").onclick=()=>{const u=$("#au").value.trim().replace(/^@/,"");if(!u)return;confirmBar($("#cact"),`GitHub will email an invitation to @${u}. They get WRITE access to this customer's private repo.`,"Invite",async()=>{try{await api(`/customers/${id}/collaborators`,{method:"POST",body:JSON.stringify({username:u,confirm:true})});redo(live)}catch(e){msg(e.message)}})}};
 const doSync=async(over)=>{try{const x=await api(`/customers/${id}/sync`,{method:"POST",body:JSON.stringify({confirm:true,overwrite_conflicts:over})});msg(x.status==="pushed"?`Pushed ${x.changed.length} file(s)`:"Already up to date");redo(live)}
  catch(e){if(e.status===409&&e.body&&e.body.conflicts){$("#cact").innerHTML=`<div class="warn" style="margin-top:12px"><b>Files in the repo were changed by hand.</b> Nothing was pushed.<pre>${esc(e.body.conflicts.map(x=>x.path+": "+x.why).join("\n"))}</pre>Overwriting replaces those files with Nexara's version (the customer's <code>custom/</code> folder is never touched).<div class="row" style="margin-top:8px"><button class="btn" id="ov">Overwrite and sync</button><button class="btn sec" id="ovn">Leave as is</button></div></div>`;
   $("#ovn").onclick=()=>$("#cact").innerHTML="";$("#ov").onclick=()=>confirmBar($("#cact"),"Overwrite the hand-edited files listed above.","Overwrite",()=>doSync(true))}else msg(e.message)}};
 $("#sy").onclick=()=>confirmBar($("#cact"),`Push the latest version of ${d.agents.filter(a=>a.behind).map(a=>a.agent_name).join(", ")} to ${r.owner}/${r.name}. The customer's Claude Code picks it up through auto-update.`,"Push update",()=>doSync(false))}

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
