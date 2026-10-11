"""Static text/scripts that ship inside every customer repo. @@TOKENS@@ are replaced by repo_gen in ONE pass (shell and PowerShell use
braces heavily, so str.format is avoided). Tokens: COMPANY OWNER REPO MKT PLUGIN AGENT_LIST AGENT_BULLETS COMMANDS SUPPORT_EMAIL."""

README_TMPL = '''# @@COMPANY@@: Claude Code agents by Nexara AI

Private repository. The whole repository **is** one Claude Code plugin (`@@PLUGIN@@`): clone it into your skills folder and it
loads in every session. No settings to edit, nothing to install.

```bash
gh repo clone @@OWNER@@/@@REPO@@ ~/.claude/skills/@@PLUGIN@@        # macOS / Linux / WSL
gh repo clone @@OWNER@@/@@REPO@@ $HOME\\.claude\\skills\\@@PLUGIN@@   # Windows PowerShell
```
Open Claude Code (a new session) and run one of your agents. No Claude Code yet, or no `gh`? See **START-HERE.md**.

## Your agents
@@AGENT_LIST@@

Or just describe the job in plain words: Claude will pick the right agent.

## Updating
```bash
git -C ~/.claude/skills/@@PLUGIN@@ pull        # or: ~/.claude/skills/@@PLUGIN@@/scripts/update.sh
```
Then restart Claude Code or run `/reload-plugins`. Every change we make is listed in `CHANGELOG.md`.
Prefer automatic updates? Install through the marketplace instead: `claude plugin install @@PLUGIN@@ --marketplace @@OWNER@@/@@REPO@@` (details in START-HERE.md, option B). Use **one** install method,
not both, because the same plugin name can only load once.

## Safety
- Agents are **read-only** (Read, Grep, Glob): they read your project, they never edit files or run commands.
- They are told never to open secret files (`.env*`, keys, credentials). That is an instruction, not a lock. For a hard
  block add deny rules to your project's `.claude/settings.json`:
```json
{"permissions": {"deny": ["Read(./.env)", "Read(./.env.*)", "Read(**/*.pem)", "Read(**/*.key)", "Read(**/id_rsa*)", "Read(**/credentials*)"]}}
```
- Some agents were designed with a calculator and a sample-data tool for testing. In Claude Code they do arithmetic by hand and
  show their working, and read the data files you point them to: spot-check important figures.

## Layout
- `agents/`, `skills/` - the agents. Managed by Nexara and regenerated on updates: don't edit by hand.
- `custom/` - yours. We never touch it.
- `scripts/` - `doctor`, `update` and `setup` helpers. `snippets/` - settings block to share the plugin with a whole project.
- `CHANGELOG.md`, `SUPPORT.md` - what changed, and how to get help.
'''

START_HERE = '''# Start here: getting your @@COMPANY@@ agents running

This private repository was prepared for you by Nexara AI. It holds Claude Code agents built for your business.
The install is **one command** once you have the basics. Pick your situation:

## You already use Claude Code and have GitHub access
Accept the GitHub invitation (email, or https://github.com/@@OWNER@@/@@REPO@@/invitations), then:
```bash
gh repo clone @@OWNER@@/@@REPO@@ ~/.claude/skills/@@PLUGIN@@          # macOS / Linux / WSL
gh repo clone @@OWNER@@/@@REPO@@ $HOME\\.claude\\skills\\@@PLUGIN@@     # Windows PowerShell
```
(`git clone https://github.com/@@OWNER@@/@@REPO@@.git <same folder>` works too.) Start a new Claude Code session: your agents are
there. Try:
@@AGENT_LIST@@

## You are new to Claude Code
1. **A paid Claude account** is required: Claude Pro, Max, Team or Enterprise, or an Anthropic Console (API) account. The free
   claude.ai plan does not include Claude Code.
2. **Install Claude Code** (check first with `claude --version`):
   - macOS, Linux, WSL: `curl -fsSL https://claude.ai/install.sh | bash`
   - Windows PowerShell: `irm https://claude.ai/install.ps1 | iex`
   - Prefer a package manager? macOS: `brew install --cask claude-code`. Windows: `winget install Anthropic.ClaudeCode`. (These do not self-update; the one-liners above do.)

   Open a **new** terminal, run `claude` once and sign in in the browser.
3. **GitHub:** accept the invitation (link above), then let your computer read the private repo:
   `gh auth login` then `gh auth setup-git` (install the GitHub CLI from https://cli.github.com; SSH keys also work).
4. Run the one-line clone from the section above, and open Claude Code in any project.

Prefer a guided run? After cloning, `scripts/setup.sh` (Windows: `scripts/setup.ps1`) checks each step and asks before installing anything.

## Option B: marketplace install with automatic updates
One command (in your terminal, or as `/plugin install ...` inside Claude Code):
```bash
claude plugin install @@PLUGIN@@ --marketplace @@OWNER@@/@@REPO@@
```
Then open `/plugin` -> Marketplaces -> `@@MKT@@` -> **Enable auto-update** (once). To share with everyone on a project, paste
`snippets/project-settings.json` into the project's `.claude/settings.json`. Use either this or the clone above, not both.

## No GitHub account?
Ask us for the plugin zip. Unzip it so the folder is `~/.claude/skills/@@PLUGIN@@` (or run `claude --plugin-dir @@PLUGIN@@.zip`).
It will not update by itself: we send you a new zip when there is one.

## Updates
Clone install: `git -C ~/.claude/skills/@@PLUGIN@@ pull` (or `scripts/update.sh`), then restart Claude Code or `/reload-plugins`.
Marketplace install: automatic after you enable auto-update (a few minutes after a session starts; applied on the next launch).
Everything we change is listed in `CHANGELOG.md`. Auto-update and `git pull` only work while your git credentials can read this repo.

## Something wrong?
Run `scripts/doctor.sh` (Windows: `scripts/doctor.ps1`). It checks each requirement and says how to fix it. Still stuck? See `SUPPORT.md`.
'''

SUPPORT = '''# Support

- **Something is not working:** run `./scripts/doctor.sh` (Windows: `.\\scripts\\doctor.ps1`) and include its output.
- **Report a problem or ask for a change:** open an issue in this repository:
  https://github.com/@@OWNER@@/@@REPO@@/issues/new?template=support.md
- **Want another agent or an integration (MCP) added?** Open an issue and describe the job you want done; new agents are
  added to this same repository and arrive through the normal update.
@@SUPPORT_EMAIL@@
## Please do not
- Commit passwords, API keys or customer data to this repository.
- Edit files under `agents/` or `skills/` by hand: Nexara manages them and your change would conflict with the next update. Put
  your own additions in `custom/` (we never touch that folder).
'''

CUSTOM_README = '''# custom/

Your own additions live here. Nexara never edits or overwrites anything in this folder, so you can keep your own notes or
reference material without conflicting with updates to the agents.
'''

GITIGNORE = '''.env
.env.*
*.pem
*.key
.claude/settings.local.json
.DS_Store
node_modules/
'''

ISSUE_TEMPLATE = '''---
name: Support request
about: Something is broken, or you want a change or a new agent
title: ""
labels: support
---

**What were you trying to do?**

**What happened instead?** (paste any error text; do not paste secrets)

**Output of `./scripts/doctor.sh`** (optional but very helpful)
'''

# ----------------------------------------------------------------------------- shell scripts
SETUP_SH = r'''#!/usr/bin/env bash
# Guided setup for the @@COMPANY@@ agents (prepared by Nexara AI). Safe to re-run; asks before installing anything.
# Usage: ./scripts/setup.sh [--marketplace] [--yes]
#   (default)       install the plugin as a folder in ~/.claude/skills/@@PLUGIN@@ (loads in every session)
#   --marketplace   install through the Claude Code marketplace instead (supports auto-update)
#   --yes           answer yes to prompts (non-interactive)
set -u
REPO="@@OWNER@@/@@REPO@@"
MKT="@@MKT@@"
PLUGIN="@@PLUGIN@@"
MODE="folder"; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --marketplace) MODE="marketplace"; shift;;
    --yes|-y) YES=1; shift;;
    -h|--help) sed -n '2,7p' "$0"; exit 0;;
    *) echo "Unknown option: $1"; exit 2;;
  esac
done
say() { printf '%s\n' "$*"; }
ok()  { printf '  [ok] %s\n' "$*"; }
bad() { printf '  [!!] %s\n' "$*"; }
ask() { # never assumes yes unless --yes
  [ "$YES" = 1 ] && return 0
  printf '%s [y/N] ' "$1"; read -r a </dev/tty 2>/dev/null || a=""
  case "$a" in y|Y|yes|YES) return 0;; *) return 1;; esac
}
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH"; export PATH;; esac

# Anthropic's official installer. Saved to a file first (not piped into a shell), checked to be a script, and fingerprinted, so
# you can see exactly what runs; it still trusts claude.ai, as the official instructions do.
install_claude() {
  local tmp sum
  tmp="$(mktemp "${TMPDIR:-/tmp}/claude-install.XXXXXX")" || { bad "could not create a temporary file"; return 1; }
  if ! curl -fsSL "https://claude.ai/install.sh" -o "$tmp"; then bad "download from claude.ai failed (check your connection)"; rm -f "$tmp"; return 1; fi
  if [ ! -s "$tmp" ] || ! head -c 2 "$tmp" | grep -q '^#!'; then bad "what was downloaded is not an installer script: refusing to run it"; rm -f "$tmp"; return 1; fi
  if command -v shasum >/dev/null 2>&1; then sum="$(shasum -a 256 "$tmp" | cut -d' ' -f1)"; elif command -v sha256sum >/dev/null 2>&1; then sum="$(sha256sum "$tmp" | cut -d' ' -f1)"; else sum="(no checksum tool found)"; fi
  say "     Downloaded the installer ($(wc -c < "$tmp" | tr -d ' ') bytes, sha256 $sum). Running it now."
  bash "$tmp"; local rc=$?
  rm -f "$tmp"
  [ "$rc" = 0 ] || { bad "the installer exited with an error ($rc)"; return 1; }
}

say "1/4 git"
if command -v git >/dev/null 2>&1; then ok "git found"; else bad "git is required: https://git-scm.com/downloads"; exit 1; fi

say "2/4 Claude Code"
if command -v claude >/dev/null 2>&1; then
  ok "claude $(claude --version 2>/dev/null | head -1)"
else
  bad "Claude Code is not installed."
  say "     Official installer: https://claude.ai/install.sh (this script downloads it, shows its checksum, then runs it). Homebrew users can run instead: brew install --cask claude-code"
  if ask "     Install Claude Code now with that command?"; then
    install_claude || exit 1
    case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH"; export PATH;; esac
    if command -v claude >/dev/null 2>&1; then ok "installed"; else bad "Installed, but 'claude' is not on PATH yet. Open a NEW terminal and run this again."; exit 1; fi
  else
    say "     Skipped. Install Claude Code, then run this again."; exit 1
  fi
fi
say "     Claude Code needs a paid Claude account (Pro, Max, Team or Enterprise) or an Anthropic Console account. First run: type  claude  and sign in."

say "3/4 Access to your private repository $REPO"
can_read() { GIT_TERMINAL_PROMPT=0 git ls-remote "https://github.com/$REPO.git" >/dev/null 2>&1; }
if can_read; then
  ok "your git credentials can read $REPO"
else
  bad "Cannot read https://github.com/$REPO yet."
  say "     a) Accept the GitHub invitation (email, or https://github.com/$REPO/invitations)."
  if command -v gh >/dev/null 2>&1; then
    if ask "     b) Log in with the GitHub CLI now (gh auth login, then gh auth setup-git)?"; then gh auth login && gh auth setup-git; fi
  else
    say "     b) Install the GitHub CLI (https://cli.github.com), then run:  gh auth login && gh auth setup-git"
  fi
  if can_read; then ok "access works now"; else bad "Still no access. Finish a) and b), then run this again (or scripts/doctor.sh)."; exit 1; fi
fi

say "4/4 Installing ($MODE)"
HERE="$(cd "$(dirname "$0")/.." && pwd)"; TARGET="$HOME/.claude/skills/$PLUGIN"
if [ "$MODE" = "marketplace" ]; then
  claude plugin install "$PLUGIN" --marketplace "$REPO" && ok "installed $PLUGIN@$MKT" || { bad "could not install $PLUGIN from $REPO"; exit 1; }
  say "     Auto-update: in Claude Code open /plugin -> Marketplaces -> $MKT -> Enable auto-update (once)."
elif [ "$HERE" = "$TARGET" ]; then
  ok "this folder already is the installed plugin ($TARGET)"
elif [ -e "$TARGET/.claude-plugin/plugin.json" ]; then
  ok "already installed at $TARGET. To update:  scripts/update.sh"
else
  say "     This puts the plugin in $TARGET so it loads in every Claude Code session."
  if ask "     Clone it there now?"; then
    mkdir -p "$HOME/.claude/skills" && git clone --quiet "https://github.com/$REPO.git" "$TARGET" && ok "installed at $TARGET" || { bad "clone failed"; exit 1; }
  else
    say "     Skipped. Do it yourself:  git clone https://github.com/$REPO.git \"$TARGET\""; exit 1
  fi
fi
say ""
say "Done. Start a NEW Claude Code session (or run /reload-plugins) and try:  @@COMMANDS@@"
say "If something is not working:  scripts/doctor.sh"
'''

DOCTOR_SH = r'''#!/usr/bin/env bash
# Checks everything the @@COMPANY@@ agents need and tells you how to fix what is missing. Changes nothing.
# Usage: ./scripts/doctor.sh [--project DIR]    (DIR = a project that may hold its own .claude/skills copy)
set -u
REPO="@@OWNER@@/@@REPO@@"
MKT="@@MKT@@"
PLUGIN="@@PLUGIN@@"
PROJECT="$PWD"
[ "${1:-}" = "--project" ] && PROJECT="${2:-$PWD}"
FAIL=0
good() { printf '  [ok] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*"; FAIL=1; }
note() { printf '  [..] %s\n' "$*"; }
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH"; export PATH;; esac

echo "Nexara agents doctor ($REPO)"
if command -v git >/dev/null 2>&1; then good "git installed"; else warn "git missing: install from https://git-scm.com/downloads"; fi

if command -v claude >/dev/null 2>&1; then
  good "Claude Code installed ($(claude --version 2>/dev/null | head -1))"
else
  warn "Claude Code missing: run  curl -fsSL https://claude.ai/install.sh | bash  (Windows: see START-HERE.md)"
fi

if GIT_TERMINAL_PROMPT=0 git ls-remote "https://github.com/$REPO.git" >/dev/null 2>&1; then
  good "git can read the private repo (needed to install and update)"
else
  warn "git cannot read https://github.com/$REPO: accept the invitation (https://github.com/$REPO/invitations), then run  gh auth login && gh auth setup-git"
fi

FOLDER=0; MARKET=0
USER_DIR="$HOME/.claude/skills/$PLUGIN"; PROJ_DIR="$PROJECT/.claude/skills/$PLUGIN"
for d in "$USER_DIR" "$PROJ_DIR"; do
  if [ -f "$d/.claude-plugin/plugin.json" ]; then FOLDER=1; good "installed as a folder: $d"; fi
done
if command -v claude >/dev/null 2>&1 && claude plugin list 2>/dev/null | grep -q "$PLUGIN@$MKT"; then MARKET=1; good "installed through the marketplace ($PLUGIN@$MKT)"; fi
if [ "$FOLDER" = 1 ] && [ "$MARKET" = 1 ]; then warn "installed BOTH as a folder and through the marketplace: the same plugin name loads only once. Remove one (delete the folder, or: claude plugin uninstall $PLUGIN@$MKT)."; fi
if [ "$FOLDER" = 0 ] && [ "$MARKET" = 0 ]; then
  if [ -f "$PWD/.claude-plugin/plugin.json" ] && grep -q "\"$PLUGIN\"" "$PWD/.claude-plugin/plugin.json" 2>/dev/null; then
    note "you are inside the plugin folder but it is not installed: run  scripts/setup.sh  (or  claude --plugin-dir \"$PWD\"  for one session)"
  else
    warn "not installed: run  gh repo clone $REPO ~/.claude/skills/$PLUGIN"
  fi
fi

if [ "$FOLDER" = 1 ]; then
  D="$USER_DIR"; [ -f "$D/.claude-plugin/plugin.json" ] || D="$PROJ_DIR"
  if [ -d "$D/.git" ]; then
    if GIT_TERMINAL_PROMPT=0 git -C "$D" fetch --quiet 2>/dev/null; then
      BEHIND=$(git -C "$D" rev-list --count HEAD..@{u} 2>/dev/null || echo 0)
      [ "${BEHIND:-0}" = 0 ] && good "up to date" || warn "$BEHIND update(s) available: run  $D/scripts/update.sh"
    else
      note "could not check for updates (no access right now)"
    fi
  else
    note "this copy has no git history (zip install?): ask us for a new zip when there is an update"
  fi
fi

echo
echo "Reminder: Claude Code needs a paid Claude account (Pro/Max/Team/Enterprise) or an Anthropic Console account."
[ "$FAIL" = 0 ] && echo "Everything checked looks good." || echo "Fix the [!!] items above and run this again. Still stuck? https://github.com/$REPO/issues/new?template=support.md"
exit $FAIL
'''

UPDATE_SH = r'''#!/usr/bin/env bash
# Updates the @@COMPANY@@ agents to the latest version we published. Usage: ./scripts/update.sh
set -u
REPO="@@OWNER@@/@@REPO@@"
MKT="@@MKT@@"
PLUGIN="@@PLUGIN@@"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH"; export PATH;; esac
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$HERE"; [ -d "$DIR/.git" ] || DIR="$HOME/.claude/skills/$PLUGIN"
if [ -d "$DIR/.git" ]; then
  echo "Updating $DIR ..."
  if GIT_TERMINAL_PROMPT=0 git -C "$DIR" pull --ff-only; then
    echo "Done. Restart Claude Code or run /reload-plugins. What changed: $DIR/CHANGELOG.md"
  else
    echo "Could not update. Do you have access ($REPO)? Run scripts/doctor.sh. If you edited files under agents/ or skills/ by hand, move your changes to custom/ and run: git -C \"$DIR\" checkout -- agents skills"
    exit 1
  fi
elif command -v claude >/dev/null 2>&1 && claude plugin list 2>/dev/null | grep -q "$PLUGIN@$MKT"; then
  claude plugin marketplace update "$MKT" && claude plugin update "$PLUGIN@$MKT" && echo "Done. Restart Claude Code."
else
  echo "No git copy found at $DIR. Install with:  gh repo clone $REPO ~/.claude/skills/$PLUGIN   (zip installs: ask us for a new zip)"
  exit 1
fi
'''

# ----------------------------------------------------------------------------- PowerShell twins (not executed in our tests: no PowerShell host)
SETUP_PS1 = r'''# Guided setup for the @@COMPANY@@ agents (prepared by Nexara AI). Safe to re-run; asks before installing anything.
# Usage (PowerShell):  .\scripts\setup.ps1 [-Marketplace] [-Yes]
param([switch]$Marketplace, [switch]$Yes)
$Repo = "@@OWNER@@/@@REPO@@"; $Mkt = "@@MKT@@"; $Plugin = "@@PLUGIN@@"
function Ask($q) { if ($Yes) { return $true }; $a = Read-Host "$q [y/N]"; return ($a -match '^(y|yes)$') }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }

Write-Host "1/4 git"
if (Have git) { Write-Host "  [ok] git found" } else { Write-Host "  [!!] git is required: https://git-scm.com/downloads"; exit 1 }

Write-Host "2/4 Claude Code"
if (Have claude) { Write-Host "  [ok] claude $(claude --version 2>$null)" }
else {
  Write-Host "  [!!] Claude Code is not installed."
  Write-Host "       Official installer: https://claude.ai/install.ps1 (this script downloads it, shows its checksum, then runs it). winget users can run instead: winget install Anthropic.ClaudeCode"
  if (Ask "       Install Claude Code now with that command?") {
    $f = Join-Path $env:TEMP "claude-install.ps1"
    try { Invoke-WebRequest https://claude.ai/install.ps1 -OutFile $f -UseBasicParsing } catch { Write-Host "  [!!] download from claude.ai failed (check your connection)"; exit 1 }
    if ((Get-Item $f).Length -eq 0 -or -not ((Get-Content $f -TotalCount 1) -match "^\s*(#|\$|param|Set-|function|\[)")) { Remove-Item $f -Force; Write-Host "  [!!] what was downloaded does not look like an installer script: refusing to run it"; exit 1 }
    Write-Host "       Downloaded the installer ($((Get-Item $f).Length) bytes, sha256 $((Get-FileHash $f -Algorithm SHA256).Hash.ToLower())). Running it now."
    Invoke-Expression (Get-Content $f -Raw); Remove-Item $f -Force
    if (-not (Have claude)) { Write-Host "  [!!] Installed, but 'claude' is not on PATH yet. Open a NEW terminal and run this again."; exit 1 }
  } else { Write-Host "       Skipped. Install Claude Code, then run this again."; exit 1 }
}
Write-Host "       Claude Code needs a paid Claude account (Pro, Max, Team, Enterprise) or an Anthropic Console account. First run: type 'claude' and sign in."

Write-Host "3/4 Access to your private repository $Repo"
function CanRead { $env:GIT_TERMINAL_PROMPT = "0"; git ls-remote "https://github.com/$Repo.git" *> $null; return ($LASTEXITCODE -eq 0) }
if (CanRead) { Write-Host "  [ok] your git credentials can read $Repo" }
else {
  Write-Host "  [!!] Cannot read https://github.com/$Repo yet."
  Write-Host "       a) Accept the GitHub invitation (email, or https://github.com/$Repo/invitations)."
  if (Have gh) { if (Ask "       b) Log in with the GitHub CLI now?") { gh auth login; gh auth setup-git } }
  else { Write-Host "       b) Install the GitHub CLI (https://cli.github.com), then run: gh auth login; gh auth setup-git" }
  if (-not (CanRead)) { Write-Host "  [!!] Still no access. Finish a) and b), then run this again."; exit 1 }
}

Write-Host "4/4 Installing"
$target = Join-Path $HOME ".claude\skills\$Plugin"; $here = Split-Path -Parent $PSScriptRoot
if ($Marketplace) {
  claude plugin install $Plugin --marketplace $Repo
  Write-Host "       Auto-update: in Claude Code open /plugin -> Marketplaces -> $Mkt -> Enable auto-update (once)."
} elseif ((Resolve-Path $here).Path -eq $target) { Write-Host "  [ok] this folder already is the installed plugin ($target)" }
elseif (Test-Path (Join-Path $target ".claude-plugin\plugin.json")) { Write-Host "  [ok] already installed at $target. To update: .\scripts\update.ps1" }
else {
  Write-Host "       This puts the plugin in $target so it loads in every Claude Code session."
  if (Ask "       Clone it there now?") { New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null; git clone --quiet "https://github.com/$Repo.git" $target; if ($LASTEXITCODE -eq 0) { Write-Host "  [ok] installed at $target" } else { Write-Host "  [!!] clone failed"; exit 1 } }
  else { Write-Host "       Skipped. Do it yourself: git clone https://github.com/$Repo.git `"$target`""; exit 1 }
}
Write-Host ""
Write-Host "Done. Start a NEW Claude Code session (or run /reload-plugins) and try: @@COMMANDS@@"
Write-Host "If something is not working: .\scripts\doctor.ps1"
'''

DOCTOR_PS1 = r'''# Checks everything the @@COMPANY@@ agents need. Changes nothing.   Usage: .\scripts\doctor.ps1 [-Project DIR]
param([string]$Project = (Get-Location).Path)
$Repo = "@@OWNER@@/@@REPO@@"; $Mkt = "@@MKT@@"; $Plugin = "@@PLUGIN@@"; $fail = $false
function Good($m) { Write-Host "  [ok] $m" }
function Warn($m) { Write-Host "  [!!] $m"; $script:fail = $true }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }

Write-Host "Nexara agents doctor ($Repo)"
if (Have git) { Good "git installed" } else { Warn "git missing: https://git-scm.com/downloads" }
if (Have claude) { Good "Claude Code installed ($(claude --version 2>$null))" } else { Warn "Claude Code missing: irm https://claude.ai/install.ps1 | iex" }
$env:GIT_TERMINAL_PROMPT = "0"; git ls-remote "https://github.com/$Repo.git" *> $null
if ($LASTEXITCODE -eq 0) { Good "git can read the private repo (needed to install and update)" }
else { Warn "git cannot read https://github.com/$Repo : accept the invitation (https://github.com/$Repo/invitations), then run: gh auth login; gh auth setup-git" }
$folder = $false; $market = $false
foreach ($d in @((Join-Path $HOME ".claude\skills\$Plugin"), (Join-Path $Project ".claude\skills\$Plugin"))) {
  if (Test-Path (Join-Path $d ".claude-plugin\plugin.json")) { $folder = $true; Good "installed as a folder: $d" }
}
if ((Have claude) -and ((claude plugin list 2>$null) -match [regex]::Escape("$Plugin@$Mkt"))) { $market = $true; Good "installed through the marketplace ($Plugin@$Mkt)" }
if ($folder -and $market) { Warn "installed BOTH as a folder and through the marketplace: the same plugin name loads only once. Remove one." }
if (-not $folder -and -not $market) { Warn "not installed: run  gh repo clone $Repo $HOME\.claude\skills\$Plugin" }
Write-Host ""
Write-Host "Reminder: Claude Code needs a paid Claude account (Pro/Max/Team/Enterprise) or an Anthropic Console account."
if ($fail) { Write-Host "Fix the [!!] items and run this again. Stuck? https://github.com/$Repo/issues/new?template=support.md"; exit 1 } else { Write-Host "Everything checked looks good." }
'''

UPDATE_PS1 = r'''# Updates the @@COMPANY@@ agents to the latest version we published.   Usage: .\scripts\update.ps1
$Repo = "@@OWNER@@/@@REPO@@"; $Mkt = "@@MKT@@"; $Plugin = "@@PLUGIN@@"
$here = Split-Path -Parent $PSScriptRoot; $dir = $here
if (-not (Test-Path (Join-Path $dir ".git"))) { $dir = Join-Path $HOME ".claude\skills\$Plugin" }
$env:GIT_TERMINAL_PROMPT = "0"
if (Test-Path (Join-Path $dir ".git")) {
  Write-Host "Updating $dir ..."; git -C $dir pull --ff-only
  if ($LASTEXITCODE -eq 0) { Write-Host "Done. Restart Claude Code or run /reload-plugins. What changed: $dir\CHANGELOG.md" }
  else { Write-Host "Could not update. Do you have access ($Repo)? Run scripts\doctor.ps1."; exit 1 }
} elseif ((Get-Command claude -ErrorAction SilentlyContinue) -and ((claude plugin list 2>$null) -match [regex]::Escape("$Plugin@$Mkt"))) {
  claude plugin marketplace update $Mkt; claude plugin update "$Plugin@$Mkt"; Write-Host "Done. Restart Claude Code."
} else { Write-Host "No git copy found at $dir. Install with: gh repo clone $Repo $dir   (zip installs: ask us for a new zip)"; exit 1 }
'''
