// Public progress page. Shows step names and states only (the server sends nothing else).
const ID=/^REQ-[A-Za-z0-9]{8}$/;let timer=null;
const f=document.getElementById("tf"),inp=document.getElementById("tid"),err=document.getElementById("terr");
const STATE={done:["check","Done"],active:["activity","In progress"],problem:["shield","Needs attention"],pending:["",""]};
const SUM={none:"Waiting to start",running:"We are building it now",ready:"Ready for handover",failed:"One step needs a person to look at it",interrupted:"One step needs a person to look at it"};
async function load(id,quiet){
 clearTimeout(timer);err.textContent="";
 try{const r=await fetch("/api/track/"+encodeURIComponent(id),{cache:"no-store"});
  if(r.status===404){if(!quiet)err.textContent="We could not find that reference. Check it and try again.";document.getElementById("res").hidden=true;return}
  if(r.status===429)throw new Error("Too many checks. Please wait a few minutes.");
  if(!r.ok)throw new Error("Something went wrong. Please try again.");
  const j=await r.json();draw(j);
  history.replaceState(null,"","/track?id="+encodeURIComponent(id));
  if(j.status==="running"||j.status==="none")timer=setTimeout(()=>load(id,true),j.status==="running"?3000:8000);
 }catch(e){err.textContent=e.message}}
function draw(j){
 document.getElementById("res").hidden=false;
 document.getElementById("tsum").textContent=SUM[j.status]||"Status";
 document.getElementById("tlive").textContent=j.status==="running"?"updating live":"";
 document.getElementById("tsteps").innerHTML=j.steps.map((s,i)=>{const [ic,lab]=STATE[s.state]||STATE.pending;
  return `<li class="ts ${s.state}"><span class="dot">${ic?ico(ic):i+1}</span><div><b>${s.name}</b>${lab?`<span class="tag ${s.state==="done"?"ok":s.state==="problem"?"fail":""}">${lab}</span>`:""}<p>${s.state==="problem"?"This step needs a person to look at it. Your request is not lost.":s.text}</p></div></li>`}).join("");
 document.getElementById("tchk").textContent=j.checks?`${j.checks.passed} of ${j.checks.total} checks passed so far.`:"";
}
f.onsubmit=e=>{e.preventDefault();const id=inp.value.trim().toUpperCase();if(!ID.test(id)){err.textContent="That does not look like a reference (REQ- and 8 letters or digits).";return}load(id)};
const pre=new URLSearchParams(location.search).get("id");if(pre){inp.value=pre;if(ID.test(pre.toUpperCase()))load(pre.toUpperCase())}
