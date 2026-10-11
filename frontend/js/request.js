const TYPES=["Customer Support","Sales","Data Analyst","Research","Workflow Automation","Custom"];
const f=document.getElementById("f"),sel=f.agent_type;
sel.innerHTML=TYPES.map(t=>`<option>${t}</option>`).join("");
const pre=new URLSearchParams(location.search).get("agent");if(TYPES.includes(pre))sel.value=pre;
const MIN={business_problem:10,tasks:5,required_output:3};
function check(){let ok=true;for(const el of f.elements){if(!el.name||el.name==="agent_type")continue;const e=el.parentElement.querySelector(".err")||el.nextElementSibling;
 let m="";const v=el.value.trim();if(!v)m="Required";else if(MIN[el.name]&&v.length<MIN[el.name])m=`At least ${MIN[el.name]} characters`;
 else if(el.type==="email"&&!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v))m="Enter a valid email";
 if(e)e.textContent=m;if(m)ok=false}return ok}
f.addEventListener("submit",async ev=>{ev.preventDefault();const top=document.getElementById("top");top.hidden=true;if(!check())return;
 const go=document.getElementById("go");go.disabled=true;go.textContent="Submitting...";
 try{const body=Object.fromEntries([...f.elements].filter(e=>e.name).map(e=>[e.name,e.value.trim()]));
  const r=await fetch("/api/requests",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j=await r.json();if(!r.ok)throw new Error(r.status===422?"Please check the form: "+j.detail.map(d=>d.loc.slice(-1)[0]+" - "+d.msg).join("; "):j.detail||"Server error");
  document.getElementById("box").innerHTML=`<div class="success"><h1>Request received</h1><p class="sub">Thanks - our team will review it. Keep this reference:</p><div class="id">${j.request_id}</div><p><a class="btn ghost" href="/">Back to home</a></p></div>`;
 }catch(e){top.textContent=e.message;top.hidden=false;go.disabled=false;go.textContent="Submit request"}});
