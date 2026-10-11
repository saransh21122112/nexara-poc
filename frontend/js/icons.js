// One SVG sprite for every page. Use: <svg class="i"><use href="#i-plugin"/></svg>, or ico("plugin") from script.
(()=>{const P={
plugin:'<path d="M9 3h3v3a2 2 0 1 0 4 0V3h3a1 1 0 0 1 1 1v4h-3a2 2 0 1 0 0 4h3v4a1 1 0 0 1-1 1h-4v-3a2 2 0 1 0-4 0v3H5a1 1 0 0 1-1-1v-4h3a2 2 0 1 0 0-4H4V4a1 1 0 0 1 1-1z"/>',
skill:'<path d="M12 3l1.9 4.6L18.5 9l-4.6 1.4L12 15l-1.9-4.6L5.5 9l4.6-1.4z"/><path d="M18 14l.9 2.1L21 17l-2.1.9L18 20l-.9-2.1L15 17l2.1-.9z"/><path d="M6 15l.7 1.6L8.3 17.3 6.7 18 6 19.6 5.3 18 3.7 17.3l1.6-.7z"/>',
mcp:'<path d="M9 3v5M15 3v5"/><path d="M6 8h12v3a6 6 0 0 1-12 0z"/><path d="M12 17v4"/>',
tool:'<path d="M14.7 6.3a4 4 0 0 0-5.4 5.1L3.5 17.2a1.8 1.8 0 0 0 2.6 2.6l5.8-5.8a4 4 0 0 0 5.1-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>',
shield:'<path d="M12 3l7 3v5c0 5-3 8.5-7 10-4-1.5-7-5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
branch:'<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M18 10c0 4-6 3-12 7"/>',
mail:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
activity:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',
inbox:'<path d="M3 13l2.5-7a2 2 0 0 1 1.9-1.4h9.2a2 2 0 0 1 1.9 1.4L21 13v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 13h5l1 3h6l1-3h5"/>',
users:'<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><path d="M16 5a3.2 3.2 0 0 1 0 6M18 14c2 .7 3 2.7 3 6"/>',
flask:'<path d="M9 3h6M10 3v6L4.5 18.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3"/><path d="M7.5 15h9"/>',
home:'<path d="M3 11l9-8 9 8"/><path d="M5 10v10h5v-6h4v6h5V10"/>',
copy:'<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
check:'<path d="M5 12.5l4.5 4.5L19 7.5"/>',
undo:'<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
wand:'<path d="M5 19L16 8"/><path d="M14 6l4 4"/><path d="M18 3v3M16.5 4.5h3M6 5v2.5M4.75 6.25h2.5M19 15v3M17.5 16.5h3"/>',
arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>',
terminal:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12.5 15H17"/>',
lock:'<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
bolt:'<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/>',
box:'<path d="M3.5 7.5L12 3l8.5 4.5v9L12 21l-8.5-4.5z"/><path d="M3.5 7.5L12 12l8.5-4.5M12 12v9"/>'};
const s='<svg xmlns="http://www.w3.org/2000/svg" style="display:none">'+Object.entries(P).map(([k,v])=>`<symbol id="i-${k}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${v}</symbol>`).join("")+'</svg>';
if(!document.getElementById("i-plugin"))document.body.insertAdjacentHTML("afterbegin",s);   // public pages inline the sprite on the server
window.ico=(n,c="")=>`<svg class="i ${c}" aria-hidden="true"><use href="#i-${n}"/></svg>`;})();
