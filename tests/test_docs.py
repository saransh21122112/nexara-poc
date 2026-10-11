"""The docs are part of the product (docs/sdd.md): diagrams in sync, specs well-formed, links alive."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def test_diagram_html_is_in_sync_with_the_markdown():
    before = (DOCS / "diagrams" / "architecture.html").read_text()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_diagrams.py")], check=True, capture_output=True)
    assert (DOCS / "diagrams" / "architecture.html").read_text() == before, "run python scripts/build_diagrams.py and commit the result"


def test_every_spec_has_front_matter_and_an_acceptance_section():
    specs = sorted((DOCS / "specs").glob("[0-9][0-9][0-9]-*.md"))
    assert len(specs) >= 8
    ids = []
    for p in specs:
        if p.name.startswith("000"):
            continue                      # the template has placeholders by design
        t = p.read_text()
        m = re.match(r"---\nid: (\d{3})\ntitle: .+\nstatus: (draft|approved|implemented)\nowner: .+\n---\n", t)
        assert m, f"{p.name}: bad front matter"
        assert m.group(1) == p.name[:3]
        ids.append(m.group(1))
        assert "## Acceptance criteria" in t or "Superseded" in t, p.name
    assert len(ids) == len(set(ids)), "duplicate spec numbers"


def test_markdown_links_between_docs_resolve():
    for md in [ROOT / "CLAUDE.md", ROOT / "README.md", *DOCS.rglob("*.md")]:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", md.read_text()):
            if target.startswith(("http", "mailto:")):
                continue
            assert (md.parent / target).exists(), f"{md.relative_to(ROOT)} links to missing {target}"


def test_claude_md_points_at_the_docs_and_the_rules():
    t = (ROOT / "CLAUDE.md").read_text()
    for needle in ("docs/README.md", "docs/sdd.md", "pull request to `dev`", "docs/specs/", "Public content rule"):
        assert needle in t
