// The token lives in the URL fragment (#...), which browsers never send to servers, so it stays out of logs and referrers.
// Confirming needs a click (POST), so mail scanners that pre-open links cannot confirm for the user.
const token=location.hash.slice(1);const go=document.getElementById("go"),msg=document.getElementById("msg");
if(!token){msg.textContent="This link is incomplete. Use the link from your email.";go.hidden=true}
go.onclick=async()=>{go.disabled=true;go.textContent="Confirming...";
 try{const r=await fetch("/api/verify",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token})});const j=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(j.detail||"Could not confirm.");
  history.replaceState(null,"",location.pathname);
  document.querySelector("h1").textContent="Email confirmed";msg.textContent="Thank you. Our team will now build your agent and email you when your private repository is ready.";go.hidden=true;
 }catch(e){msg.textContent=e.message;go.disabled=false;go.textContent="Try again"}};
