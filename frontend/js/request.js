const TYPES=["Plugin","Skill","MCP server","Tool","Custom"];
const f=document.getElementById("f"),sel=f.agent_type;
sel.innerHTML=TYPES.map(t=>`<option>${t}</option>`).join("");
const pre=new URLSearchParams(location.search).get("agent");if(TYPES.includes(pre))sel.value=pre;
const MIN={business_problem:10,tasks:5,required_output:3};
const GH=/^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;
function check(){let ok=true;for(const el of f.elements){if(!el.name||el.name==="agent_type"||el.name==="claude_status"||el.name==="os")continue;const nx=el.nextElementSibling;const e=nx&&nx.classList.contains("err")?nx:null; // the error box that directly follows this field
 let m="";const v=el.value.trim();if(!v)m="Required";else if(MIN[el.name]&&v.length<MIN[el.name])m=`At least ${MIN[el.name]} characters`;
 else if(el.name==="github_username"&&!GH.test(v.replace(/^@/,"")))m="Enter just your GitHub username (letters, digits, single hyphens)";
 else if(el.type==="email"&&!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v))m="Enter a valid email";
 if(e)e.textContent=m;if(m)ok=false}return ok}
f.addEventListener("submit",async ev=>{ev.preventDefault();const top=document.getElementById("top");top.hidden=true;if(!check())return;
 const go=document.getElementById("go");go.disabled=true;go.textContent="Submitting...";
 try{const body=Object.fromEntries([...f.elements].filter(e=>e.name&&e.value.trim()!=="").map(e=>[e.name,e.value.trim()]));
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));const r=await fetch("/api/requests",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j=await r.json();if(!r.ok)throw new Error(r.status===422?"Please check the form: "+j.detail.map(d=>d.loc.slice(-1)[0]+" - "+d.msg).join("; "):j.detail||"Server error");
  document.getElementById("box").innerHTML=`<div class="formcard"><div class="success"><div class="check">${ico("check")}</div><h1>Request received</h1><p class="sub">Thanks - our team will review it. ${j.verification_required?'<b>Check your inbox for a confirmation link from us and click it - we cannot start until you do.</b> ':''}Once your agent is built we create a private GitHub repository for ${esc(body.company_name)} and invite <b>@${esc(body.github_username.replace(/^@/,""))}</b>. Keep this reference:</p><div class="id">${j.request_id}</div><p><button class="btn ghost" type="button" id="cp">Copy reference</button></p><p><a class="btn ghost" href="/">Back to home</a></p></div></div>`;
  document.getElementById("cp").onclick=e=>{navigator.clipboard?.writeText(j.request_id).then(()=>{e.target.textContent="Copied ✓"}).catch(()=>{})};
 }catch(e){top.textContent=e.message;top.hidden=false;go.disabled=false;go.textContent="Submit request"}});
