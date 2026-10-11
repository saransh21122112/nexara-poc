"""Server-rendered public pages: one layout, content fragments in frontend/pages/, config-driven legal details.
Public text describes outcomes, never mechanics (docs/specs/007, enforced by tests/test_pages.py)."""
import html
import os
import re
from datetime import datetime, timezone
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
LAYOUT = (FRONTEND / "layout.html").read_text()

# path -> (fragment, title, description, scripts, public/indexed)
PAGES: dict[str, tuple[str, str, str, list[str], bool]] = {
    "/": ("home", "Nexara AI | Custom plugins, skills, MCP servers and tools",
          "We design, test and hand over custom plugins, skills, MCP servers and tools built around how your business works.", ["home.js"], True),
    "/about": ("about", "About | Nexara AI", "Who we are and what we believe: AI that is genuinely useful inside your company, private by default and always under your control.", [], True),
    "/what-we-do": ("what-we-do", "What we do | Nexara AI", "Plugins, skills, MCP servers and tools built around your business, delivered as a private repository.", [], True),
    "/how-we-work": ("how-we-work", "How we work | Nexara AI", "Tell us, we design, we verify, handover and evolve. Clear steps from your first message to a tool your team uses.", [], True),
    "/security": ("security", "Security | Nexara AI", "Private by default, least access and a person approving every change. The promises we hold ourselves to.", [], True),
    "/faq": ("faq", "FAQ | Nexara AI", "Short answers about what we build, how it is delivered, ownership, changes, timing and your information.", [], True),
    "/contact": ("contact", "Contact | Nexara AI", "Send us a message about a project, an existing build or anything else. A person reads every message.", ["contact.js"], True),
    "/privacy": ("privacy", "Privacy notice | Nexara AI", "What we collect, why we collect it, who we share it with and the choices you have.", [], True),
    "/terms": ("terms", "Terms of use | Nexara AI", "The ground rules for using this website and for working with us.", [], True),
    "/request": ("request", "Start a request | Nexara AI", "Describe what you need and our assistant fills in the form. We design, test and hand it over as a private repository.", ["request.js", "assist.js"], True),
    "/track": ("track", "Track your request | Nexara AI", "See where your request stands.", ["track.js"], False),
    "/verify": ("verify", "Confirm your email | Nexara AI", "Confirm your email address.", ["verify.js"], False),
}
NAV = [("/what-we-do", "What we do"), ("/how-we-work", "How we work"), ("/about", "About"), ("/security", "Security"), ("/faq", "FAQ"), ("/contact", "Contact")]
_FRAG: dict[str, str] = {}
_SPRITE: list[str] = []


def _e(v: str) -> str:
    return html.escape(v or "", quote=True)


def cfg() -> dict:
    env = os.environ.get
    return {"email": (env("CONTACT_EMAIL") or "").strip(), "name": (env("LEGAL_NAME") or "").strip(), "address": (env("LEGAL_ADDRESS") or "").strip(),
            "effective": (env("POLICY_EFFECTIVE") or "").strip(), "reviewed": (env("POLICY_REVIEWED") or "").strip().lower() == "true"}


def base_url(request) -> str:
    return (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or str(request.base_url)).rstrip("/")


def sprite() -> str:
    """The icon sprite is inlined so icons render without JavaScript. Built once from js/icons.js (the single source of icons)."""
    if not _SPRITE:
        src = (FRONTEND / "js" / "icons.js").read_text()
        symbols = "".join(
            f'<symbol id="i-{k}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">{v}</symbol>'
            for k, v in re.findall(r"^([a-z]+):'(.*)',?$", src, flags=re.M))
        _SPRITE.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">{symbols}</svg>')
    return _SPRITE[0]


def _fragment(name: str) -> str:
    if name not in _FRAG or os.environ.get("RELOAD_PAGES"):
        _FRAG[name] = (FRONTEND / "pages" / f"{name}.html").read_text()
    return _FRAG[name]


def _tokens(c: dict) -> dict:
    mail = f'<a href="mailto:{_e(c["email"])}">{_e(c["email"])}</a>' if c["email"] else ""
    return {
        "ENTITY": _e(c["name"]) or "Nexara AI",
        "ADDRESS_INLINE": f", {_e(c['address'])}" if c["address"] else "",
        "CONTACT_SENTENCE": (f'You can reach us at {mail} or through our <a href="/contact">contact page</a>.' if mail else 'You can reach us through our <a href="/contact">contact page</a>.'),
        "CONTACT_SENTENCE_SHORT": (f'Write to {mail} or use our <a href="/contact">contact page</a>.' if mail else 'Use our <a href="/contact">contact page</a>.'),
        "DRAFT_NOTE": "" if c["reviewed"] else '<p class="note">This text is a draft pending legal review.</p>',
        "EFFECTIVE_LINE": f"<p><strong>Effective:</strong> {_e(c['effective'])}</p>" if c["effective"] else "",
        "CONTACT_SIDE": (f"<p>Email: {mail}</p>" if mail else "") + (f"<p>{_e(c['address'])}</p>" if c["address"] else ""),
    }


def render(path: str, request, status_page: str | None = None) -> str:
    c = cfg()
    if status_page == "404":
        frag, title, desc, scripts, indexed = "404", "Page not found | Nexara AI", "The page you were looking for does not exist.", [], False
        canon = base_url(request) + "/"
    else:
        frag, title, desc, scripts, indexed = PAGES[path]
        canon = base_url(request) + ("/" if path == "/" else path)
    tok = _tokens(c)
    body = _fragment(frag)
    for k, v in tok.items():
        body = body.replace("{{" + k + "}}", v)
    cur = path if status_page != "404" else ""

    def link(p, label):
        return f'<a href="{p}"' + (' aria-current="page"' if p == cur else "") + f">{label}</a>"
    nav = "".join(link(p, l) for p, l in NAV)
    extra = (f'<a href="mailto:{_e(c["email"])}">{_e(c["email"])}</a>' if c["email"] else "") + (f"<span>{_e(c['address'])}</span>" if c["address"] else "")
    out = LAYOUT
    for k, v in {"TITLE": _e(title), "DESC": _e(desc), "CANON": _e(canon), "ROBOTS": "index,follow" if indexed else "noindex,nofollow",
                 "SPRITE": sprite(), "NAV": nav, "SHEET": nav, "BODY": body, "YEAR": str(datetime.now(timezone.utc).year),
                 "ENTITY": tok["ENTITY"], "FOOT_EXTRA": extra,
                 "SCRIPTS": "".join(f'<script src="/js/{s}"></script>' for s in scripts)}.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def sitemap(request) -> str:
    b = base_url(request)
    urls = "".join(f"<url><loc>{_e(b + ('/' if p == '/' else p))}</loc></url>" for p, v in PAGES.items() if v[4])
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


def robots(request) -> str:
    return ("User-agent: *\nAllow: /\nDisallow: /factory\nDisallow: /track\nDisallow: /verify\nDisallow: /api\n"
            f"Sitemap: {base_url(request)}/sitemap.xml\n")
