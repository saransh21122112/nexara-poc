// Public-site behaviour. Loaded in <head> so the "js" class is set before first paint (reveal animations only hide content when JS runs).
document.documentElement.classList.add("js");
(function(){
 var rm=matchMedia("(prefers-reduced-motion: reduce)").matches;
 function ready(f){document.readyState==="loading"?document.addEventListener("DOMContentLoaded",f):f()}
 ready(function(){
  var top=document.getElementById("top"),bar=document.querySelector(".progress i"),ticking=false;
  function onScroll(){ticking=false;var y=window.scrollY||0,h=document.documentElement.scrollHeight-innerHeight;
   if(top)top.classList.toggle("scrolled",y>8);if(bar)bar.style.transform="scaleX("+(h>0?Math.min(1,y/h):0)+")"}
  addEventListener("scroll",function(){if(!ticking){ticking=true;requestAnimationFrame(onScroll)}},{passive:true});onScroll();
  // mobile menu: sheet, scroll lock, Esc, focus trap, closes on navigation and when the viewport becomes wide
  var btn=document.getElementById("menu"),sheet=document.getElementById("sheet");
  if(btn&&sheet){
   var items=function(){return [btn].concat([].slice.call(sheet.querySelectorAll("a")))};
   function set(open){btn.setAttribute("aria-expanded",open);btn.setAttribute("aria-label",open?"Close menu":"Open menu");sheet.classList.toggle("open",open);sheet.setAttribute("aria-hidden",!open);document.body.classList.toggle("lock",open);
    if(open)sheet.querySelector("a").focus();else if(document.activeElement&&sheet.contains(document.activeElement))btn.focus()}
   set(false);
   btn.addEventListener("click",function(){set(btn.getAttribute("aria-expanded")!=="true")});
   sheet.addEventListener("click",function(e){if(e.target.closest("a"))set(false)});
   document.addEventListener("keydown",function(e){if(btn.getAttribute("aria-expanded")!=="true")return;
    if(e.key==="Escape"){set(false);return}
    if(e.key==="Tab"){var l=items(),a=document.activeElement,i=l.indexOf(a);if(e.shiftKey&&i<=0){e.preventDefault();l[l.length-1].focus()}else if(!e.shiftKey&&i===l.length-1){e.preventDefault();l[0].focus()}}});
   matchMedia("(min-width: 960px)").addEventListener("change",function(m){if(m.matches)set(false)});
  }
  // reveal on scroll + count-up. Without IntersectionObserver everything is simply shown.
  var els=[].slice.call(document.querySelectorAll(".rv"));
  function show(el){el.classList.add("in");[].forEach.call(el.querySelectorAll("[data-to]"),count)}
  function count(el){var to=+el.dataset.to,sfx=el.dataset.suffix||"";if(rm||!to){el.textContent=to+sfx;return}var t0;
   (function f(t){t0=t0||t;var p=Math.min((t-t0)/900,1);el.textContent=Math.round(to*(1-Math.pow(1-p,3)))+sfx;if(p<1)requestAnimationFrame(f)})(performance.now())}
  if(!("IntersectionObserver" in window)||rm){els.forEach(show)}else{
   var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){show(e.target);io.unobserve(e.target)}})},{threshold:.12,rootMargin:"0px 0px -4% 0px"});
   els.forEach(function(el){io.observe(el)});
   setTimeout(function(){els.forEach(function(el){if(!el.classList.contains("in")){var r=el.getBoundingClientRect();if(r.top<innerHeight&&r.bottom>0)show(el)}})},1500)}
  // pointer spotlight on cards: fine pointers only
  if(matchMedia("(hover: hover) and (pointer: fine)").matches&&!rm){
   [].forEach.call(document.querySelectorAll(".card"),function(c){c.addEventListener("pointermove",function(e){var r=c.getBoundingClientRect();c.style.setProperty("--mx",(e.clientX-r.left)+"px");c.style.setProperty("--my",(e.clientY-r.top)+"px")})})}
 });
})();
