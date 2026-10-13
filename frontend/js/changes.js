// Changes to delivered work: describe a change, review it, put it on the customer repo's dev branch and open a pull request.
const SPEC_FIELDS=[["agent_name","Name"],["purpose","Purpose"],["business_problem","Problem"],["instructions","Instructions"],["capabilities","Capabilities"],["required_tools","Tools"],["data_sources","Data sources"],["safety_restrictions","Safety limits"],["success_criteria","Success criteria"]];
function listDiff(a,b){const A=new Set(a||[]),B=new Set(b||[]);return [...(b||[]).map(x=>[x,A.has(x)?"same":"add"]),...(a||[]).filter(x=>!B.has(x)).map(x=>[x,"del"])]}
function specDiffHtml(cur,prop){
 const rows=SPEC_FIELDS.map(([k,label])=>{
  const a=cur[k],b=prop[k];if(JSON.stringify(a)===JSON.stringify(b))return "";
  if(Array.isArray(a)||Array.isArray(b))return `<div class="sd"><b>${label}</b>${listDiff(a,b).map(([x,s])=>`<div class="ln ${s}">${s==="add"?"+":s==="del"?"−":" "} ${esc(x)}</div>`).join("")}</div>`;
  return `<div class="sd"><b>${label}</b><div class="ln del">− ${esc(a)}</div><div class="ln add">+ ${esc(b)}</div></div>`}).join("");
 const nt=JSON.stringify(cur.test_scenarios)!==JSON.stringify(prop.test_scenarios)?`<div class="sd"><b>Test scenarios</b><div class="ln add">+ updated (${(prop.test_scenarios||[]).length} scenarios)</div></div>`:"";
 return rows+nt||'<p class="muted">The proposal changes nothing.</p>'}
function fileDiffHtml(f){
 const lines=(f.diff||"").split("\n").map(l=>`<div class="ln ${l.startsWith("+")&&!l.startsWith("+++")?"add":l.startsWith("-")&&!l.startsWith("---")?"del":l.startsWith("@@")?"hunk":""}">${esc(l)||" "}</div>`).join("");
 return `<details class="fd"><summary><span class="tag ${f.status==="added"?"ok":f.status==="removed"?"fail":""}">${f.status}</span><code>${esc(f.path)}</code></summary><div class="dv">${lines||'<p class="muted">Binary or empty.</p>'}${f.truncated?'<p class="muted">Diff shortened.</p>':""}</div></details>`}

function changesPanel(d){
 const ch=d.change,behind=d.agents.filter(a=>a.behind);
 let state,cls="";
 if(ch&&ch.state==="open"){state=`PR #${ch.pr_number} open`;cls=""}
 else if(behind.length){state="update ready to propose";cls="fail"}
 else{state=ch&&ch.state==="merged"?"merged, up to date":"up to date";cls="ok"}
 return `<div class="panel"><h3>${ico("branch")} Changes <span class="tag ${cls}">${esc(state)}</span></h3>
  <p class="muted">Updates go to the <b>dev</b> branch of this private repo and open a pull request into <b>main</b>. Nothing is merged by Nexara: a person reviews and merges on GitHub, then press <b>Refresh from GitHub</b>.</p>
  ${ch&&ch.state==="open"?`<p><a href="${esc(ch.pr_url)}" target="_blank" rel="noopener">${ico("branch")} Open pull request #${ch.pr_number} on GitHub</a><br><small class="muted">${esc((ch.summary||[]).join("; "))}</small></p>`:ch?`<p class="muted">Last proposal: PR #${ch.pr_number}, ${esc(ch.state)}.</p>`:""}
  <div class="row"><button class="btn sec" id="rv" ${behind.length||ch&&ch.state==="open"?"":"disabled"}>Review files</button><button class="btn" id="pr" ${behind.length?"":"disabled"}>${ch&&ch.state==="open"?"Update pull request":"Open pull request"}</button></div>
  <div id="rvout"></div></div>`}

function wireChanges(el,d,id,r,msg,redo,live){
 const out=$("#rvout");
 $("#rv").onclick=async()=>{out.innerHTML='<div class="sk big"></div>';
  try{const x=await api(`/customers/${id}/diff`);out.innerHTML=(x.conflicts.length?`<div class="warn"><b>Hand edits in the repo</b> will block the proposal unless you choose to overwrite them:<pre>${esc(x.conflicts.map(c=>c.path+": "+c.why).join("\n"))}</pre></div>`:"")+(x.files.length?x.files.map(fileDiffHtml).join(""):'<p class="muted">No file changes.</p>')}
  catch(e){out.innerHTML=`<div class="bad">${esc(e.message)}</div>`}};
 const go=async(o)=>{try{const x=await api(`/customers/${id}/sync`,{method:"POST",body:JSON.stringify({confirm:true,overwrite_conflicts:!!o.conflicts,overwrite_dev:!!o.dev,unverified_code_ack:!!o.code})});
   msg(x.status==="proposed"?`Pull request #${x.pull_request.number} is ready for review on GitHub.`:"Already up to date");redo(live)}
  catch(e){const b=e.body||{};
   if(e.status===409&&b.conflicts){$("#cact").innerHTML=`<div class="warn" style="margin-top:12px"><b>Files in the repo were changed by hand.</b> Nothing was proposed.<pre>${esc(b.conflicts.map(x=>x.path+": "+x.why).join("\n"))}</pre><div class="row"><button class="btn sec" id="ovn">Cancel</button><button class="btn" id="ov">Overwrite them in the proposal</button></div></div>`;
    $("#ovn").onclick=()=>$("#cact").innerHTML="";$("#ov").onclick=()=>confirmBar($("#cact"),"Replace the hand-edited files with Nexara's version in the proposed update (you still review the pull request).","Overwrite",()=>go({...o,conflicts:true}))}
   else if(e.status===409&&/'dev' branch/.test(e.message)){$("#cact").innerHTML=`<div class="warn" style="margin-top:12px"><b>${esc(e.message)}</b><div class="row"><button class="btn sec" id="dn">Cancel</button><button class="btn" id="dy">Replace dev</button></div></div>`;
    $("#dn").onclick=()=>$("#cact").innerHTML="";$("#dy").onclick=()=>confirmBar($("#cact"),"This replaces the other commits on the repo's dev branch with Nexara's update.","Replace dev",()=>go({...o,dev:true}))}
   else if(e.status===409&&/unverified_code_ack/.test(e.message)){$("#cact").innerHTML=`<div class="warn" style="margin-top:12px"><b>${esc(e.message)}</b><div class="row"><button class="btn sec" id="un">Cancel</button><button class="btn" id="uy">I reviewed the code</button></div></div>`;
    $("#un").onclick=()=>$("#cact").innerHTML="";$("#uy").onclick=()=>confirmBar($("#cact"),"Propose code that has not been run and checked on this host. You still review the pull request.","Propose anyway",()=>go({...o,code:true}))}
   else msg(e.message)}};
 $("#pr").onclick=()=>confirmBar($("#cact"),`Put the latest ${d.agents.filter(a=>a.behind).map(a=>a.agent_name).join(", ")} on branch dev of ${r.owner}/${r.name} and open a pull request into main. Nothing is merged.`,"Open pull request",()=>go({}));
 el.querySelectorAll("[data-rev]").forEach(b=>b.onclick=()=>revisePanel(d.agents.find(a=>a.agent_id===b.dataset.rev),$("#revhost"),()=>redo(live)))}

async function revisePanel(agent,host,done){
 host.innerHTML=`<div class="panel revise"><h3>${ico("wand")} Change ${esc(agent.agent_name)}</h3><p class="muted">Describe the change in plain words. The assistant proposes an updated specification; you review it before anything is saved or sent.</p>
  <textarea id="rvi" maxlength="1500" placeholder="e.g. Also check the refund policy before drafting a reply, and never promise a refund amount."></textarea>
  <div class="row"><button class="btn" id="rvp">Propose change</button><button class="btn sec" id="rvx">Close</button><span id="rvs" class="muted"></span></div><div id="rvr"></div></div>`;
 host.scrollIntoView({behavior:"smooth",block:"nearest"});
 $("#rvx").onclick=()=>host.innerHTML="";
 $("#rvp").onclick=async()=>{const t=$("#rvi").value.trim();if(t.length<3)return;$("#rvp").disabled=true;$("#rvs").textContent="Asking the model - this can take up to a minute...";$("#rvr").innerHTML="";
  try{const x=await api(`/agents/${agent.agent_id}/revise`,{method:"POST",body:JSON.stringify({instruction:t})});
   $("#rvr").innerHTML=`<h3 style="margin-top:14px">Proposed changes to the specification</h3>${specDiffHtml(x.current,x.proposed)}<div class="row" style="margin-top:12px"><button class="btn" id="rvs2">Save to specification</button><span class="muted">Saving does not touch GitHub. Then open the pull request below.</span></div><div id="rvm"></div>`;
   $("#rvs2").onclick=async()=>{try{await api(`/agents/${agent.agent_id}`,{method:"PUT",body:JSON.stringify(x.proposed)});$("#rvm").innerHTML='<div class="ok-box">Saved. The agent is now marked as behind the repository.</div>';setTimeout(done,900)}catch(e){$("#rvm").innerHTML=`<div class="bad">${esc(e.message)}</div>`}}}
  catch(e){$("#rvr").innerHTML=`<div class="bad">${e.status===503?"<b>Configuration needed.</b> ":""}${esc(e.message)}</div>`}$("#rvp").disabled=false;$("#rvs").textContent=""}}
