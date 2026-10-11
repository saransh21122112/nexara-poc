// Contact form: fetches a signed ticket, validates, posts, shows a clear success or error. Works without storing anything in the browser.
(function(){
 var f=document.getElementById("cf");if(!f)return;
 var send=document.getElementById("csend"),msg=document.getElementById("cmsg"),ticket="",EM=/^[^@\s]+@[^@\s]+\.[^@\s]+$/;
 function getTicket(){return fetch("/api/contact/ticket",{cache:"no-store"}).then(function(r){return r.json()}).then(function(j){ticket=j.ticket}).catch(function(){})}
 getTicket();
 function err(n,t){var e=f.querySelector('.err[data-for="'+n+'"]');if(e)e.textContent=t}
 function check(){var ok=true,v=function(n){return f.elements[n].value.trim()};
  ["name","email","message"].forEach(function(n){err(n,"")});
  if(!v("name")){err("name","Please enter your name");ok=false}
  if(!EM.test(v("email"))){err("email","Enter a valid email address");ok=false}
  if(v("message").length<10){err("message","Please write at least 10 characters");ok=false}
  return ok}
 f.addEventListener("submit",function(ev){ev.preventDefault();msg.hidden=true;if(!check())return;
  send.disabled=true;var old=send.innerHTML;send.textContent="Sending...";
  var body={name:f.elements.name.value.trim(),email:f.elements.email.value.trim(),topic:f.elements.topic.value,message:f.elements.message.value.trim(),website:f.elements.website.value,ticket:ticket};
  fetch("/api/contact",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(function(r){return r.json().catch(function(){return{}}).then(function(j){return[r,j]})}).then(function(a){var r=a[0],j=a[1];
   if(r.ok){f.outerHTML='<div class="box" role="status"><div class="in2" style="text-align:center;padding:36px 20px"><div class="check">'+(window.ico?ico("check"):"")+'</div><h2 style="font-size:1.5rem">Message sent</h2><p class="muted">Thank you. A person will read it and get back to you.</p><p><a class="btn ghost" href="/">Back to home</a></p></div></div>';return}
   if(r.status===400)getTicket();
   throw new Error(r.status===422?"Please check your details and try again.":j.detail||"Something went wrong. Please try again.")})
  .catch(function(e){msg.textContent=e.message;msg.hidden=false;send.disabled=false;send.innerHTML=old})});
})();
