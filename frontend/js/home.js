// Decorative "in action" demo on the home page (canned text, not a real run).
(function(){
 var demo=document.getElementById("demo");if(!demo)return;
 var RM=matchMedia("(prefers-reduced-motion: reduce)").matches;
 var S=[["u","/acme:support-digest summarise this morning's new tickets"],["st","<b>&#10003;</b> read the new tickets"],["st","<b>&#10003;</b> grouped them by topic and urgency"],["a","12 new tickets. 4 are about late deliveries, and 2 need a person today. I drafted replies for all 12 for you to review before anything is sent."]];
 function play(){demo.innerHTML="";var i=0;(function next(){if(i>=S.length){if(!RM)setTimeout(play,6000);return}
  var k=S[i][0],t=S[i][1];i++;var d=document.createElement("div");d.className="msg "+k;demo.appendChild(d);
  if(k==="st"||RM){d.innerHTML=t;setTimeout(next,RM?0:800)}else{var n=0;d.classList.add("caret");(function tick(){d.textContent=t.slice(0,++n);if(n<t.length)setTimeout(tick,k==="u"?24:14);else{d.classList.remove("caret");setTimeout(next,700)}})()}})()}
 play();
})();
