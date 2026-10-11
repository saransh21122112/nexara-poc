// Request-form assistant: "describe it" fills the boxes; "Ask AI" edits one box. Nothing is submitted by it, and every change can be undone.
(()=>{
const form=document.getElementById("f"),note=document.getElementById("anote"),desc=document.getElementById("desc"),fill=document.getElementById("fill");
const BOXES=["company_name","industry","agent_type","business_problem","tasks","existing_tools","required_output","examples"];
const CHIPS=["Make it shorter","Add more detail","Mention Slack and Jira"];
const prev={};
const say=(t,k="")=>{note.textContent=t;note.className="note "+k};
const current=()=>Object.fromEntries(BOXES.map(k=>[k,(form.elements[k]?.value||"").trim()]).filter(([,v])=>v));
async function call(body,btn){
 const label=btn.innerHTML;btn.disabled=true;btn.textContent="Working...";say("");
 try{const r=await fetch("/api/requests/assist",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(r.status===422?"Please write a little more, then try again.":j.detail||"The assistant is unavailable. You can fill the form in by hand.");
  return j.fields||{};
 }catch(e){say(e.message,"bad");return null}finally{btn.disabled=false;btn.innerHTML=label}}
function apply(fields){
 let n=0;
 for(const [k,v] of Object.entries(fields)){
  const el=form.elements[k];if(!el||!BOXES.includes(k))continue;
  if(el.tagName==="SELECT"&&![...el.options].some(o=>o.value===v))continue;
  const box=el.closest(".fld");prev[k]=el.value;   // Undo reverts the latest AI change only
  el.value=v;box.classList.add("chg");const u=box.querySelector("[data-undo]");if(u)u.hidden=false;
  el.dispatchEvent(new Event("input",{bubbles:true}));n++}
 return n}
fill.onclick=async()=>{
 if(desc.value.trim().length<10){say("Write a sentence or two about what you need first.","bad");return}
 const f=await call({description:desc.value.trim(),current:current()},fill);if(!f)return;
 const n=apply(f);say(n?`Filled ${n} box${n>1?"es":""}. Review the highlighted ones and change anything with "Ask AI".`:"Nothing to change. Try adding more detail.",n?"ok":"bad");
 if(n)document.getElementById("company_name").closest(".box").scrollIntoView({behavior:"smooth",block:"start"})};
document.querySelectorAll("[data-eg]").forEach(b=>b.onclick=()=>{desc.value=b.dataset.eg;desc.focus()});
document.querySelectorAll(".fld[data-f]").forEach(box=>{
 const k=box.dataset.f,ask=box.querySelector("[data-ask]");if(!ask)return;
 const row=box.querySelector(".askrow"),inp=row.querySelector("input"),go=row.querySelector("[data-apply]");
 const chips=document.createElement("div");chips.className="eg2";chips.style.cssText="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px";
 chips.innerHTML=CHIPS.map(c=>`<button type="button" class="tl">${c}</button>`).join("");row.after(chips);chips.hidden=true;
 ask.onclick=()=>{const on=!row.classList.contains("on");row.classList.toggle("on",on);chips.hidden=!on;if(on)inp.focus()};
 chips.querySelectorAll("button").forEach(b=>b.onclick=()=>{inp.value=b.textContent;go.click()});
 const run=async()=>{const ins=inp.value.trim();if(!ins){inp.focus();return}
  const f=await call({instruction:ins,field:k,current:current()},go);if(!f)return;
  if(apply(f)){inp.value="";say("Updated. Use Undo to go back.","ok")}else say("Nothing needed to change.","bad")};
 go.onclick=run;inp.onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();run()}};
 const undo=box.querySelector("[data-undo]");
 undo.onclick=()=>{const el=form.elements[k];if(k in prev){el.value=prev[k];delete prev[k]}box.classList.remove("chg");undo.hidden=true;el.dispatchEvent(new Event("input",{bubbles:true}))}});
// typing by hand clears the "changed by AI" highlight
form.addEventListener("input",e=>{if(e.isTrusted)e.target.closest?.(".fld")?.classList.remove("chg")});
})();
