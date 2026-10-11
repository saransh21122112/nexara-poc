// The token lives in the URL fragment (#...), which browsers never send to servers, so it stays out of logs and referrers.
// Confirming needs a click (POST), so mail scanners that pre-open links cannot confirm for the user.
var token=location.hash.slice(1);var go=document.getElementById("go"),msg=document.getElementById("msg");
if(!token){msg.textContent="This link is incomplete. Use the link from your email.";go.hidden=true}
go.onclick=async function(){go.disabled=true;go.textContent="Confirming...";
 try{var r=await fetch("/api/verify",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token:token})});var j=await r.json().catch(function(){return{}});
  if(!r.ok)throw new Error(j.detail||"Could not confirm.");
  history.replaceState(null,"",location.pathname);
  document.querySelector("h1").textContent="Email confirmed";msg.textContent="Thank you. Our team will now build your request and email you when your private repository is ready.";go.hidden=true;
 }catch(e){msg.textContent=e.message;go.disabled=false;go.textContent="Try again"}};
