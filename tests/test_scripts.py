"""Runs the generated customer scripts (setup.sh / doctor.sh) in a sandbox with fake claude/gh/git/curl on PATH.
Windows .ps1 twins are NOT executed here (no PowerShell on this host)."""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from backend import repo_gen
from tests.test_repo_gen import CUST, REPO, SPEC, SPEC2, build

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
MKT = "nexara-acme-corp"
SLUGS = "acme-code-reviewer acme-sales-analyst"


def script(path: Path, body: str):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def box(tmp_path):
    files, _ = build((SPEC, SPEC2))
    repo = tmp_path / "repo"
    for p, c in files.items():
        f = repo / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(c)
        if repo_gen.is_executable(p):
            f.chmod(0o755)
    home, bin_, state, proj = tmp_path / "home", tmp_path / "bin", tmp_path / "state", tmp_path / "proj"
    for d in (home, bin_, state, proj):
        d.mkdir()
    # fake git: `ls-remote` succeeds only when $STATE/access exists; everything else is a no-op
    script(bin_ / "git", f'[ "$1" = "ls-remote" ] && {{ [ -e "{state}/access" ]; exit $?; }}\nexit 0\n')
    class Box:
        pass
    b = Box()
    b.repo, b.home, b.bin, b.state, b.proj, b.tmp = repo, home, bin_, state, proj, tmp_path

    def fake_claude():
        script(bin_ / "claude", f'echo "$@" >> "{state}/claude.log"\n[ "$1" = "--version" ] && echo "2.1.0 (Claude Code)"\nexit 0\n')

    def fake_gh(works=True):
        script(bin_ / "gh", f'echo "$@" >> "{state}/gh.log"\n[ "$1 $2" = "auth login" ] && touch "{state}/access"\nexit 0\n' if works else 'exit 1\n')

    def run(name, *args, expect_ok=None, access=False, extra_path=True):
        if access:
            (state / "access").touch()
        sys_path = ":".join([str(bin_), str(Path(shutil.which("python3")).parent), "/usr/bin", "/bin"])
        env = {"HOME": str(home), "PATH": sys_path, "TERM": "dumb"}
        r = subprocess.run(["bash", str(repo / "scripts" / name), *args], capture_output=True, text=True, env=env, cwd=str(proj),
                           timeout=60, start_new_session=True, stdin=subprocess.DEVNULL)   # no controlling tty -> prompts default to "no"
        b.out = r.stdout + r.stderr
        return r
    b.run, b.fake_claude, b.fake_gh = run, fake_claude, fake_gh
    return b


def test_scripts_are_executable_and_parse(box):
    for n in ("setup.sh", "doctor.sh"):
        assert os.access(box.repo / "scripts" / n, os.X_OK)
        assert subprocess.run(["bash", "-n", str(box.repo / "scripts" / n)]).returncode == 0


def test_setup_unknown_option_and_missing_project_arg(box):
    assert box.run("setup.sh", "--bogus").returncode == 2
    assert box.run("setup.sh", "--project").returncode == 2


def test_setup_without_claude_declines_install_by_default(box):
    r = box.run("setup.sh")                       # no --yes, no tty: must NOT install anything
    assert r.returncode == 1 and "Claude Code is not installed" in box.out and "Skipped" in box.out
    assert "claude.ai/install.sh" in box.out and not (box.home / ".local/bin/claude").exists()


def test_setup_installs_claude_only_with_consent(box):
    # fake curl serves an "installer" that drops a claude binary into ~/.local/bin
    script(box.bin / "curl", 'echo \'mkdir -p "$HOME/.local/bin"; printf "#!/bin/sh\\necho 2.1.0 (Claude Code)\\n" > "$HOME/.local/bin/claude"; chmod +x "$HOME/.local/bin/claude"\'\n')
    box.fake_gh()
    r = box.run("setup.sh", "--yes", access=True)
    assert r.returncode == 0, box.out
    assert (box.home / ".local/bin/claude").exists() and "installed" in box.out


def test_setup_stops_with_instructions_when_repo_not_readable(box):
    box.fake_claude()
    r = box.run("setup.sh")                       # no access, no gh
    assert r.returncode == 1 and "Accept the GitHub invitation" in box.out and "gh auth login" in box.out and "invitations" in box.out
    assert not (box.proj / ".claude").exists()    # changed nothing


def test_setup_logs_in_with_gh_when_consenting_then_continues(box):
    box.fake_claude()
    box.fake_gh()
    r = box.run("setup.sh", "--yes")
    assert r.returncode == 0, box.out
    log = (box.state / "gh.log").read_text()
    assert "auth login" in log and "auth setup-git" in log and "access works now" in box.out


def test_setup_project_scope_creates_settings_with_autoupdate(box):
    box.fake_claude()
    r = box.run("setup.sh", access=True)
    assert r.returncode == 0, box.out
    s = json.loads((box.proj / ".claude/settings.json").read_text())
    assert s["extraKnownMarketplaces"][MKT]["autoUpdate"] is True
    assert s["extraKnownMarketplaces"][MKT]["source"] == {"source": "github", "repo": f"{REPO['owner']}/{REPO['name']}"}
    assert set(s["enabledPlugins"]) == {f"{p}@{MKT}" for p in SLUGS.split()}
    assert "trust" in box.out and "doctor.sh" in box.out


def test_setup_merges_into_existing_settings_without_losing_anything(box):
    box.fake_claude()
    (box.proj / ".claude").mkdir()
    mine = {"permissions": {"deny": ["Read(./.env)"]}, "enabledPlugins": {"other@else": True}, "extraKnownMarketplaces": {"else": {"source": {"source": "github", "repo": "x/y"}}}}
    (box.proj / ".claude/settings.json").write_text(json.dumps(mine))
    r = box.run("setup.sh", access=True)
    assert r.returncode == 0 and "backup" in box.out
    s = json.loads((box.proj / ".claude/settings.json").read_text())
    assert s["permissions"] == mine["permissions"] and s["enabledPlugins"]["other@else"] is True and "else" in s["extraKnownMarketplaces"]
    assert MKT in s["extraKnownMarketplaces"] and f"acme-code-reviewer@{MKT}" in s["enabledPlugins"]
    assert json.loads((box.proj / ".claude/settings.json.bak").read_text()) == mine
    box.run("setup.sh", access=True)               # idempotent
    assert json.loads((box.proj / ".claude/settings.json").read_text()) == s


def test_setup_corrupt_settings_are_restored_and_manual_steps_shown(box):
    box.fake_claude()
    (box.proj / ".claude").mkdir()
    (box.proj / ".claude/settings.json").write_text("{ not json")
    r = box.run("setup.sh", access=True)
    assert (box.proj / ".claude/settings.json").read_text() == "{ not json"      # untouched
    assert "Could not merge automatically" in box.out and "by hand" in box.out


def test_setup_project_flag_targets_another_folder_and_rejects_missing(box):
    box.fake_claude()
    other = box.tmp / "other"
    other.mkdir()
    assert box.run("setup.sh", "--project", str(other), access=True).returncode == 0 and (other / ".claude/settings.json").exists()
    r = box.run("setup.sh", "--project", str(box.tmp / "nope"), access=True)
    assert r.returncode == 1 and "project folder not found" in box.out


def test_setup_user_scope_uses_claude_cli(box):
    box.fake_claude()
    r = box.run("setup.sh", "--user", access=True)
    assert r.returncode == 0, box.out
    calls = (box.state / "claude.log").read_text()
    assert f"plugin marketplace add {REPO['owner']}/{REPO['name']}" in calls
    for p in SLUGS.split():
        assert f"plugin install {p}@{MKT}" in calls
    assert not (box.proj / ".claude").exists() and "Enable auto-update" in box.out


def test_doctor_reports_each_problem_with_a_fix(box):
    r = box.run("doctor.sh")                       # nothing installed, no access, no registration
    assert r.returncode == 1
    for needle in ("Claude Code missing", "git cannot read", "invitations", "no registration", "paid Claude account"):
        assert needle in box.out, needle


def test_doctor_all_good_in_a_connected_project(box):
    box.fake_claude()
    box.run("setup.sh", access=True)
    r = box.run("doctor.sh", access=True)
    assert r.returncode == 0 and "[!!]" not in box.out and "Everything checked looks good" in box.out
    assert "auto-update is on" in box.out


def test_doctor_flags_autoupdate_off(box):
    box.fake_claude()
    box.run("setup.sh", access=True)
    p = box.proj / ".claude/settings.json"
    p.write_text(p.read_text().replace('"autoUpdate": true', '"autoUpdate": false'))
    r = box.run("doctor.sh", access=True)
    assert r.returncode == 1 and "updates will not arrive" in box.out


def test_doctor_recognises_clone_mode_without_false_alarms(box):
    box.fake_claude()
    r = box.run("doctor.sh", "--project", str(box.repo), access=True)
    assert r.returncode == 0, box.out
    assert "clone mode" in box.out and "autoUpdate" not in box.out and "[!!]" not in box.out


def test_powershell_twins_exist_and_mention_the_same_steps(box):
    for n in ("setup.ps1", "doctor.ps1"):
        text = (box.repo / "scripts" / n).read_text()
        assert MKT in text and f"{REPO['owner']}/{REPO['name']}" in text and "Reminder" in text or "paid Claude account" in text
    assert "irm https://claude.ai/install.ps1 | iex" in (box.repo / "scripts/setup.ps1").read_text()
