#!/usr/bin/env python3
"""Builds docs/diagrams/architecture.html from the Mermaid blocks in docs/architecture.md and docs/flows.md (single source of truth).
Open the HTML file locally in a browser; GitHub already renders the Markdown files directly."""
import html
import pathlib
import re

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
parts = []
for name in ("architecture.md", "flows.md"):
    text = (DOCS / name).read_text()
    for m in re.finditer(r"^(#{1,3}) ([^\n]+)\n(.*?)(?=^#{1,3} |\Z)", text, flags=re.M | re.S):
        blocks = re.findall(r"```mermaid\n(.*?)```", m.group(3), flags=re.S)
        if blocks:
            parts.append(f"<section><h2>{html.escape(m.group(2))}</h2>" + "".join(f'<pre class="mermaid">{html.escape(b)}</pre>' for b in blocks) + "</section>")
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nexara AI POC: architecture and flows</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:24px;color:#26181a;background:#fbf8f1}}h1{{color:#4a1019}}h2{{color:#6b1d2a;border-bottom:2px solid #c9a227;padding-bottom:4px;margin-top:40px}}
section{{background:#fff;border:1px solid #e4d9c3;border-radius:12px;padding:8px 20px 20px;margin:20px 0;overflow-x:auto}}.mermaid{{background:none}}</style></head><body>
<h1>Nexara AI POC: architecture and flows</h1><p>Generated from <code>docs/architecture.md</code> and <code>docs/flows.md</code> by <code>scripts/build_diagrams.py</code>. Do not edit by hand.</p>
{''.join(parts)}
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true,theme:'base',themeVariables:{{primaryColor:'#f6ecc9',primaryBorderColor:'#c9a227',primaryTextColor:'#26181a',lineColor:'#6b1d2a',secondaryColor:'#f7e9ec',tertiaryColor:'#fff'}},securityLevel:'strict'}});</script>
</body></html>"""
(DOCS / "diagrams" / "architecture.html").write_text(page)
print(f"wrote docs/diagrams/architecture.html with {len(re.findall('class=.mermaid', page))} diagrams")
