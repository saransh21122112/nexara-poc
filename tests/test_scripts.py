"""Runs the generated customer scripts (setup.sh / doctor.sh / update.sh) in a sandbox with fake claude/gh/git/curl on PATH.
Windows .ps1 twins are NOT executed here (no PowerShell on this host)."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from backend import repo_gen
from tests.test_repo_gen import MKT, PLUGIN, REPO, SPEC, SPEC2, build

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
SLUG = f"{REPO['owner']}/{REPO['name']}"


def script(path: Path, body: str):
    path.write_text("#!/bin/bash\n" + body)   # the fake git uses bash-isms; on Linux /bin/sh is dash
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
    # fake git: `ls-remote` ok only when $STATE/access exists; `clone URL DEST` copies the repo; pull/fetch recorded
    real_git = shutil.which("git") or "/usr/bin/git"
    script(bin_ / "git", f'''echo "git $@" >> "{state}/git.log"
case "$1" in
  ls-remote) [ -e "{state}/access" ]; exit $?;;
  clone) dest="${{@: -1}}"; [ -e "{state}/access" ] || exit 128; mkdir -p "$dest"; cp -R "{repo}/." "$dest/"; mkdir -p "$dest/.git"; exit 0;;
  -C) shift 2; case "$1" in pull) [ -e "{state}/pull_fails" ] && exit 1; echo "Already up to date." ; exit 0;; fetch) exit 0;; rev-list) echo "${{BEHIND:-0}}"; exit 0;; esac;;
esac
exit 0
''')

    class Box:
        pass
    b = Box()
    b.repo, b.home, b.bin, b.state, b.proj, b.tmp = repo, home, bin_, state, proj, tmp_path

    def fake_claude(plugins=""):
        script(bin_ / "claude", f'echo "$@" >> "{state}/claude.log"\n[ "$1" = "--version" ] && echo "2.1.0 (Claude Code)"\n[ "$1 $2" = "plugin list" ] && echo "{plugins}"\nexit 0\n')

    def fake_gh(works=True):
        script(bin_ / "gh", f'echo "$@" >> "{state}/gh.log"\n[ "$1 $2" = "auth login" ] && touch "{state}/access"\nexit 0\n' if works else 'exit 1\n')

    def run(name, *args, access=False, cwd=None, env_extra=None, script_dir=None):
        if access:
            (state / "access").touch()
        path = ":".join([str(bin_), "/usr/bin", "/bin"])
        env = {"HOME": str(home), "PATH": path, "TERM": "dumb", **(env_extra or {})}
        r = subprocess.run(["bash", str((script_dir or repo) / "scripts" / name), *args], capture_output=True, text=True, env=env,
                           cwd=str(cwd or proj), timeout=60, start_new_session=True, stdin=subprocess.DEVNULL)   # no tty -> prompts default to "no"
        b.out = r.stdout + r.stderr
        return r
    b.run, b.fake_claude, b.fake_gh = run, fake_claude, fake_gh
    b.target = home / ".claude" / "skills" / PLUGIN
    return b


def test_scripts_are_executable_and_parse(box):
    for n in ("setup.sh", "doctor.sh", "update.sh"):
        assert os.access(box.repo / "scripts" / n, os.X_OK)
        assert subprocess.run(["bash", "-n", str(box.repo / "scripts" / n)]).returncode == 0


# ------------------------------------------------------------------ setup.sh
def test_setup_rejects_unknown_options(box):
    assert box.run("setup.sh", "--bogus").returncode == 2


def test_setup_without_claude_declines_install_by_default(box):
    r = box.run("setup.sh")                       # no --yes, no tty: must NOT install anything
    assert r.returncode == 1 and "Claude Code is not installed" in box.out and "Skipped" in box.out
    assert "claude.ai/install.sh" in box.out and not (box.home / ".local/bin/claude").exists()


FAKE_INSTALLER = '''#!/bin/sh
mkdir -p "$HOME/.local/bin"; printf '#!/bin/sh\\necho 2.1.0 (Claude Code)\\n' > "$HOME/.local/bin/claude"; chmod +x "$HOME/.local/bin/claude"
'''


def fake_curl(box, body):
    """curl that writes `body` to the file given with -o (like the real download)."""
    script(box.bin / "curl", 'while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\ncat > "$out" <<\'EOF_\'\n' + body + 'EOF_\n')


def test_setup_installs_claude_only_with_consent(box):
    fake_curl(box, FAKE_INSTALLER)
    box.fake_gh()
    r = box.run("setup.sh", "--yes", access=True)
    assert r.returncode == 0, box.out
    assert (box.home / ".local/bin/claude").exists() and "installed" in box.out
    assert "sha256" in box.out and not list(Path(box.tmp).glob("claude-install.*"))   # fingerprint shown, temp file removed


def test_setup_refuses_a_download_that_is_not_a_script(box):
    fake_curl(box, "<html>captive portal</html>")
    r = box.run("setup.sh", "--yes", access=True)
    assert r.returncode == 1 and "not an installer script" in box.out and not (box.home / ".local/bin/claude").exists()


def test_setup_never_pipes_a_download_into_a_shell(box):
    for n in ("setup.sh", "setup.ps1"):
        t = (box.repo / "scripts" / n).read_text()
        assert "| iex" not in t and "| Invoke-Expression" not in t and "curl -fsSL https://claude.ai/install.sh |" not in t


def test_setup_stops_with_instructions_when_the_repo_is_not_readable(box):
    box.fake_claude()
    r = box.run("setup.sh")                       # no access, no gh
    assert r.returncode == 1 and "Accept the GitHub invitation" in box.out and "gh auth login" in box.out and "invitations" in box.out
    assert not box.target.exists()                # changed nothing


def test_setup_logs_in_with_gh_when_consenting_then_clones(box):
    box.fake_claude()
    box.fake_gh()
    r = box.run("setup.sh", "--yes")
    assert r.returncode == 0, box.out
    log = (box.state / "gh.log").read_text()
    assert "auth login" in log and "auth setup-git" in log and "access works now" in box.out
    assert (box.target / ".claude-plugin" / "plugin.json").exists()


def test_setup_default_installs_the_plugin_as_a_folder_in_the_skills_dir(box):
    box.fake_claude()
    r = box.run("setup.sh", "--yes", access=True)
    assert r.returncode == 0, box.out
    assert (box.target / ".claude-plugin" / "plugin.json").exists() and (box.target / "agents" / "acme-code-reviewer.md").exists()
    assert f"git clone --quiet https://github.com/{SLUG}.git {box.target}" in (box.state / "git.log").read_text()
    assert f"/{PLUGIN}:acme-code-reviewer" in box.out and "NEW Claude Code session" in box.out
    assert not (box.proj / ".claude").exists()    # no project settings are written any more


def test_setup_declines_to_clone_without_consent_and_prints_the_manual_command(box):
    box.fake_claude()
    r = box.run("setup.sh", access=True)          # no --yes and no tty -> "no"
    assert r.returncode == 1 and not box.target.exists() and f"git clone https://github.com/{SLUG}.git" in box.out


def test_setup_is_idempotent_and_recognises_an_existing_install(box):
    box.fake_claude()
    box.run("setup.sh", "--yes", access=True)
    r = box.run("setup.sh", "--yes", access=True)
    assert r.returncode == 0 and "already installed" in box.out
    assert (box.state / "git.log").read_text().count("clone") == 1       # did not clone twice


def test_setup_from_inside_the_installed_folder_just_confirms(box):
    box.fake_claude()
    box.run("setup.sh", "--yes", access=True)
    r = box.run("setup.sh", "--yes", access=True, script_dir=box.target)
    assert r.returncode == 0 and "already is the installed plugin" in box.out


def test_setup_marketplace_mode_uses_the_one_step_claude_command(box):
    box.fake_claude()
    r = box.run("setup.sh", "--marketplace", access=True)
    assert r.returncode == 0, box.out
    assert f"plugin install {PLUGIN} --marketplace {SLUG}" in (box.state / "claude.log").read_text()
    assert not box.target.exists() and "Enable auto-update" in box.out


# ------------------------------------------------------------------ doctor.sh
def test_doctor_reports_each_problem_with_a_fix(box):
    r = box.run("doctor.sh")                       # nothing installed, no access
    assert r.returncode == 1
    for needle in ("Claude Code missing", "git cannot read", "invitations", "not installed", f"gh repo clone {SLUG}", "paid Claude account"):
        assert needle in box.out, needle


def test_doctor_all_good_after_a_setup_run(box):
    box.fake_claude()
    box.run("setup.sh", "--yes", access=True)
    r = box.run("doctor.sh", access=True)
    assert r.returncode == 0 and "[!!]" not in box.out and "Everything checked looks good" in box.out
    assert "installed as a folder" in box.out and "up to date" in box.out


def test_doctor_reports_available_updates(box):
    box.fake_claude()
    box.run("setup.sh", "--yes", access=True)
    r = box.run("doctor.sh", access=True, env_extra={"BEHIND": "3"})
    assert r.returncode == 1 and "3 update(s) available" in box.out and "update.sh" in box.out


def test_doctor_flags_a_double_install(box):
    box.fake_claude(plugins=f"{PLUGIN}@{MKT}")
    box.run("setup.sh", "--yes", access=True)                      # folder install
    r = box.run("doctor.sh", access=True)                          # ...and claude says the marketplace one exists too
    assert r.returncode == 1 and "BOTH as a folder and through the marketplace" in box.out


def test_doctor_recognises_marketplace_only_install(box):
    box.fake_claude(plugins=f"{PLUGIN}@{MKT}")
    r = box.run("doctor.sh", access=True)
    assert r.returncode == 0 and "installed through the marketplace" in box.out


def test_doctor_inside_an_uninstalled_clone_says_how_to_install(box):
    box.fake_claude()
    r = box.run("doctor.sh", access=True, cwd=box.repo)
    assert "you are inside the plugin folder but it is not installed" in box.out and "scripts/setup.sh" in box.out


def test_doctor_notes_a_zip_install_has_no_git_history(box):
    box.fake_claude()
    shutil.copytree(box.repo, box.target)                           # a zip install: files, no .git
    r = box.run("doctor.sh", access=True)
    assert "no git history" in box.out and "new zip" in box.out and r.returncode == 0


def test_doctor_finds_a_project_level_install(box):
    box.fake_claude()
    shutil.copytree(box.repo, box.proj / ".claude" / "skills" / PLUGIN)
    r = box.run("doctor.sh", "--project", str(box.proj), access=True)
    assert "installed as a folder" in box.out and str(box.proj) in box.out


# ------------------------------------------------------------------ update.sh
def test_update_pulls_the_git_copy_and_points_at_the_changelog(box):
    box.fake_claude()
    box.run("setup.sh", "--yes", access=True)
    r = box.run("update.sh", access=True, script_dir=box.target)
    assert r.returncode == 0 and "Restart Claude Code or run /reload-plugins" in box.out and "CHANGELOG.md" in box.out
    assert f"-C {box.target} pull --ff-only" in (box.state / "git.log").read_text()


def test_update_failure_explains_what_to_do(box):
    box.fake_claude()
    box.run("setup.sh", "--yes", access=True)
    (box.state / "pull_fails").touch()
    r = box.run("update.sh", access=True, script_dir=box.target)
    assert r.returncode == 1 and "Could not update" in box.out and "doctor.sh" in box.out and "custom/" in box.out


def test_update_marketplace_install_uses_the_claude_commands(box):
    box.fake_claude(plugins=f"{PLUGIN}@{MKT}")
    r = box.run("update.sh", access=True)                           # repo dir has no .git and no skills copy -> marketplace route
    assert r.returncode == 0, box.out
    log = (box.state / "claude.log").read_text()
    assert f"plugin marketplace update {MKT}" in log and f"plugin update {PLUGIN}@{MKT}" in log


def test_update_with_nothing_installed_says_how_to_install(box):
    box.fake_claude()
    r = box.run("update.sh", access=True)
    assert r.returncode == 1 and f"gh repo clone {SLUG}" in box.out and "zip" in box.out


def test_powershell_twins_exist_and_carry_the_same_steps(box):
    for n in ("setup.ps1", "doctor.ps1", "update.ps1"):
        text = (box.repo / "scripts" / n).read_text()
        assert MKT in text and SLUG in text and PLUGIN in text, n
    assert "https://claude.ai/install.ps1" in (box.repo / "scripts/setup.ps1").read_text()
    ps = (box.repo / "scripts/setup.ps1").read_text()
    assert "claude plugin install $Plugin --marketplace $Repo" in ps and f'$Repo = "{SLUG}"' in ps and f'$Plugin = "{PLUGIN}"' in ps
