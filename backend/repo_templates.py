"""Static text/scripts that ship inside every customer repo. @@TOKENS@@ are replaced by repo_gen (shell/PowerShell use
braces heavily, so str.format is avoided)."""

SETUP_SH = r'''#!/usr/bin/env bash
# Guided setup for the @@COMPANY@@ agents (prepared by Nexara AI). Safe to re-run; it asks before installing anything.
# Usage: ./scripts/setup.sh [--project DIR] [--user] [--yes]
#   --project DIR  connect the agents to the project in DIR (default: current folder)
#   --user         install for your whole user account instead of one project
#   --yes          answer yes to prompts (non-interactive)
set -u
REPO="@@OWNER@@/@@REPO@@"
MKT="@@MKT@@"
PLUGINS="@@PLUGINS@@"
PROJECT="$PWD"; SCOPE="project"; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="${2:-}"; [ -n "$PROJECT" ] || { echo "--project needs a folder"; exit 2; }; shift 2;;
    --user) SCOPE="user"; shift;;
    --yes|-y) YES=1; shift;;
    -h|--help) sed -n '2,6p' "$0"; exit 0;;
    *) echo "Unknown option: $1"; exit 2;;
  esac
done

say() { printf '%s\n' "$*"; }
ok()  { printf '  [ok] %s\n' "$*"; }
bad() { printf '  [!!] %s\n' "$*"; }
ask() { # ask "question" -> 0 if the user says yes (never assumes yes without --yes)
  [ "$YES" = 1 ] && return 0
  printf '%s [y/N] ' "$1"; read -r a </dev/tty 2>/dev/null || a=""
  case "$a" in y|Y|yes|YES) return 0;; *) return 1;; esac
}
add_local_bin() { case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH"; export PATH;; esac; }
add_local_bin

say "1/5 git"
if command -v git >/dev/null 2>&1; then ok "git found"; else bad "git is required. Install it from https://git-scm.com/downloads and run this again."; exit 1; fi

say "2/5 Claude Code"
if command -v claude >/dev/null 2>&1; then
  ok "claude $(claude --version 2>/dev/null | head -1)"
else
  bad "Claude Code is not installed."
  say "     Official installer:  curl -fsSL https://claude.ai/install.sh | bash   (Windows users: see START-HERE.md)"
  if ask "     Install Claude Code now with that command?"; then
    curl -fsSL https://claude.ai/install.sh | bash
    add_local_bin
    if command -v claude >/dev/null 2>&1; then ok "installed"; else bad "Installed, but 'claude' is not on your PATH yet. Open a NEW terminal and run this script again."; exit 1; fi
  else
    say "     Skipped. Install Claude Code, then run this script again."; exit 1
  fi
fi
say "     Claude Code needs a paid Claude account (Pro, Max, Team or Enterprise) or an Anthropic Console account."
say "     If you have not signed in yet, run:  claude   (and follow the browser prompts)."

say "3/5 Access to your private repository $REPO"
can_read() { GIT_TERMINAL_PROMPT=0 git ls-remote "https://github.com/$REPO.git" >/dev/null 2>&1; }
if can_read; then
  ok "your git credentials can read $REPO"
else
  bad "Cannot read https://github.com/$REPO yet."
  say "     a) Accept the GitHub invitation (check your email, or open https://github.com/$REPO/invitations)."
  if command -v gh >/dev/null 2>&1; then
    if ask "     b) Log in with the GitHub CLI now (gh auth login, then gh auth setup-git)?"; then gh auth login && gh auth setup-git; fi
  else
    say "     b) Install the GitHub CLI (https://cli.github.com), then run:  gh auth login && gh auth setup-git"
  fi
  if can_read; then ok "access works now"; else bad "Still no access. Finish a) and b), then run this script again (or run scripts/doctor.sh)."; exit 1; fi
fi

merge_settings() { # merge_settings TARGET SNIPPET: adds our marketplace + plugins, keeps everything else, makes a backup
  cp "$1" "$1.bak" || return 1
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$1" "$2" <<'PY'
import json, sys
t = json.load(open(sys.argv[1])); s = json.load(open(sys.argv[2]))
t.setdefault("extraKnownMarketplaces", {}).update(s["extraKnownMarketplaces"])
t.setdefault("enabledPlugins", {}).update(s["enabledPlugins"])
open(sys.argv[1], "w").write(json.dumps(t, indent=2) + "\n")
PY
  elif command -v node >/dev/null 2>&1; then
    node -e 'const fs=require("fs");const t=JSON.parse(fs.readFileSync(process.argv[1]));const s=JSON.parse(fs.readFileSync(process.argv[2]));
t.extraKnownMarketplaces=Object.assign(t.extraKnownMarketplaces||{},s.extraKnownMarketplaces);
t.enabledPlugins=Object.assign(t.enabledPlugins||{},s.enabledPlugins);fs.writeFileSync(process.argv[1],JSON.stringify(t,null,2)+"\n")' "$1" "$2"
  else
    return 1
  fi
}

say "4/5 Connecting the agents ($SCOPE scope)"
if [ "$SCOPE" = "user" ]; then
  claude plugin marketplace add "$REPO" || { bad "could not add the marketplace $REPO"; exit 1; }
  for p in $PLUGINS; do claude plugin install "$p@$MKT" && ok "installed $p" || bad "could not install $p"; done
  say "     Auto-update: in Claude Code open /plugin -> Marketplaces -> $MKT -> Enable auto-update."
else
  SNIP="$(cd "$(dirname "$0")" && pwd)/../snippets/project-settings.json"
  [ -d "$PROJECT" ] || { bad "project folder not found: $PROJECT"; exit 1; }
  mkdir -p "$PROJECT/.claude"; F="$PROJECT/.claude/settings.json"
  if [ ! -f "$F" ]; then
    cp "$SNIP" "$F" && ok "created $F"
  elif merge_settings "$F" "$SNIP" 2>/dev/null; then
    ok "updated $F (backup: $F.bak)"
  else
    [ -f "$F.bak" ] && cp "$F.bak" "$F"
    bad "Could not merge automatically (is $F valid JSON, and is python3 or node installed?)."
    say "     Add the contents of $SNIP to $F by hand, then continue."
  fi
fi

say "5/5 Done"
say "     Open Claude Code in your project:   cd \"$PROJECT\" && claude"
say "     Accept the 'trust this folder' prompt. Your agents are then available: @@COMMANDS@@"
say "     Updates arrive automatically a few minutes after you start a session (or run /reload-plugins)."
say "     Something not working?  ./scripts/doctor.sh --project \"$PROJECT\""
'''

DOCTOR_SH = r'''#!/usr/bin/env bash
# Checks everything the @@COMPANY@@ agents need and tells you how to fix what is missing. Changes nothing.
# Usage: ./scripts/doctor.sh [--project DIR]
set -u
REPO="@@OWNER@@/@@REPO@@"
MKT="@@MKT@@"
PROJECT="$PWD"
[ "${1:-}" = "--project" ] && PROJECT="${2:-$PWD}"
FAIL=0
good() { printf '  [ok] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*"; FAIL=1; }
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH"; export PATH;; esac

echo "Nexara agents doctor ($REPO)"
if command -v git >/dev/null 2>&1; then good "git installed"; else warn "git missing: install from https://git-scm.com/downloads"; fi

if command -v claude >/dev/null 2>&1; then
  good "Claude Code installed ($(claude --version 2>/dev/null | head -1))"
else
  warn "Claude Code missing: run  curl -fsSL https://claude.ai/install.sh | bash  (Windows: see START-HERE.md)"
fi

if GIT_TERMINAL_PROMPT=0 git ls-remote "https://github.com/$REPO.git" >/dev/null 2>&1; then
  good "git can read the private repo (needed for updates)"
else
  warn "git cannot read https://github.com/$REPO: accept the invitation (https://github.com/$REPO/invitations), then run  gh auth login && gh auth setup-git"
fi

S="$PROJECT/.claude/settings.json"
if [ -f "$PROJECT/.claude-plugin/marketplace.json" ] && grep -q "$MKT" "$PROJECT/.claude-plugin/marketplace.json" 2>/dev/null; then
  good "this folder is the agents repository itself (clone mode: agents load from here, updates come with git pull)"
elif [ -f "$S" ] && grep -q "$MKT" "$S" 2>/dev/null; then
  good "project settings register $MKT ($S)"
  grep -q '"autoUpdate": *true' "$S" 2>/dev/null && good "auto-update is on" || warn "autoUpdate is not true in $S: updates will not arrive by themselves"
else
  warn "no registration for $MKT in $S: run scripts/setup.sh --project \"$PROJECT\""
fi

if command -v claude >/dev/null 2>&1; then
  if claude plugin marketplace list 2>/dev/null | grep -q "$MKT"; then good "Claude Code knows the marketplace $MKT"; else echo "  [..] marketplace $MKT not registered yet (normal until you open Claude Code in the project and accept the trust prompt)"; fi
fi

echo
echo "Reminder: Claude Code needs a paid Claude account (Pro/Max/Team/Enterprise) or an Anthropic Console account."
[ "$FAIL" = 0 ] && echo "Everything checked looks good." || echo "Fix the [!!] items above and run this again. Still stuck? Open an issue: https://github.com/$REPO/issues/new?template=support.md"
exit $FAIL
'''

SETUP_PS1 = r'''# Guided setup for the @@COMPANY@@ agents (prepared by Nexara AI). Safe to re-run; asks before installing anything.
# Usage (PowerShell):  .\scripts\setup.ps1 [-Project DIR] [-User] [-Yes]
# NOTE: written for Windows PowerShell 5.1+ / PowerShell 7; maintained alongside setup.sh.
param([string]$Project = (Get-Location).Path, [switch]$User, [switch]$Yes)
$Repo = "@@OWNER@@/@@REPO@@"; $Mkt = "@@MKT@@"; $Plugins = "@@PLUGINS@@".Split(" ", [StringSplitOptions]::RemoveEmptyEntries)

function Ask($q) { if ($Yes) { return $true }; $a = Read-Host "$q [y/N]"; return ($a -match '^(y|yes)$') }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }

Write-Host "1/5 git"
if (Have git) { Write-Host "  [ok] git found" } else { Write-Host "  [!!] git is required: https://git-scm.com/downloads"; exit 1 }

Write-Host "2/5 Claude Code"
if (Have claude) { Write-Host "  [ok] claude $(claude --version 2>$null)" }
else {
  Write-Host "  [!!] Claude Code is not installed."
  Write-Host "       Official installer:  irm https://claude.ai/install.ps1 | iex"
  if (Ask "       Install Claude Code now with that command?") {
    Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
    if (-not (Have claude)) { Write-Host "  [!!] Installed, but 'claude' is not on PATH yet. Open a NEW terminal and run this again."; exit 1 }
  } else { Write-Host "       Skipped. Install Claude Code, then run this again."; exit 1 }
}
Write-Host "       Claude Code needs a paid Claude account (Pro, Max, Team, Enterprise) or an Anthropic Console account. Run 'claude' once to sign in."

Write-Host "3/5 Access to your private repository $Repo"
function CanRead { $env:GIT_TERMINAL_PROMPT = "0"; git ls-remote "https://github.com/$Repo.git" *> $null; return ($LASTEXITCODE -eq 0) }
if (CanRead) { Write-Host "  [ok] your git credentials can read $Repo" }
else {
  Write-Host "  [!!] Cannot read https://github.com/$Repo yet."
  Write-Host "       a) Accept the GitHub invitation (email, or https://github.com/$Repo/invitations)."
  if (Have gh) { if (Ask "       b) Log in with the GitHub CLI now?") { gh auth login; gh auth setup-git } }
  else { Write-Host "       b) Install the GitHub CLI (https://cli.github.com), then run: gh auth login; gh auth setup-git" }
  if (-not (CanRead)) { Write-Host "  [!!] Still no access. Finish a) and b), then run this again."; exit 1 }
}

Write-Host "4/5 Connecting the agents"
if ($User) {
  claude plugin marketplace add $Repo
  foreach ($p in $Plugins) { claude plugin install "$p@$Mkt" }
  Write-Host "       Auto-update: in Claude Code open /plugin -> Marketplaces -> $Mkt -> Enable auto-update."
} else {
  $snip = Join-Path (Split-Path -Parent $PSScriptRoot) "snippets\project-settings.json"
  $dir = Join-Path $Project ".claude"; New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $file = Join-Path $dir "settings.json"
  if (-not (Test-Path $file)) { Copy-Item $snip $file; Write-Host "  [ok] created $file" }
  else {
    Copy-Item $file "$file.bak" -Force
    try {
      $t = Get-Content $file -Raw | ConvertFrom-Json; $s = Get-Content $snip -Raw | ConvertFrom-Json
      foreach ($k in @("extraKnownMarketplaces", "enabledPlugins")) {
        if (-not $t.$k) { $t | Add-Member -NotePropertyName $k -NotePropertyValue ([pscustomobject]@{}) }
        foreach ($p in $s.$k.PSObject.Properties) { $t.$k | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force }
      }
      $t | ConvertTo-Json -Depth 20 | Set-Content $file; Write-Host "  [ok] updated $file (backup: $file.bak)"
    } catch { Copy-Item "$file.bak" $file -Force; Write-Host "  [!!] Could not merge automatically. Add the contents of $snip to $file by hand." }
  }
}

Write-Host "5/5 Done. Open Claude Code in your project (cd `"$Project`"; claude), accept the trust prompt, then use: @@COMMANDS@@"
Write-Host "     Something not working?  .\scripts\doctor.ps1 -Project `"$Project`""
'''

DOCTOR_PS1 = r'''# Checks everything the @@COMPANY@@ agents need. Changes nothing.   Usage: .\scripts\doctor.ps1 [-Project DIR]
param([string]$Project = (Get-Location).Path)
$Repo = "@@OWNER@@/@@REPO@@"; $Mkt = "@@MKT@@"; $fail = $false
function Good($m) { Write-Host "  [ok] $m" }
function Warn($m) { Write-Host "  [!!] $m"; $script:fail = $true }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }

Write-Host "Nexara agents doctor ($Repo)"
if (Have git) { Good "git installed" } else { Warn "git missing: https://git-scm.com/downloads" }
if (Have claude) { Good "Claude Code installed ($(claude --version 2>$null))" } else { Warn "Claude Code missing: irm https://claude.ai/install.ps1 | iex" }
$env:GIT_TERMINAL_PROMPT = "0"; git ls-remote "https://github.com/$Repo.git" *> $null
if ($LASTEXITCODE -eq 0) { Good "git can read the private repo (needed for updates)" }
else { Warn "git cannot read https://github.com/$Repo : accept the invitation (https://github.com/$Repo/invitations), then run: gh auth login; gh auth setup-git" }
$s = Join-Path $Project ".claude\settings.json"
if ((Test-Path (Join-Path $Project ".claude-plugin\marketplace.json")) -and ((Get-Content (Join-Path $Project ".claude-plugin\marketplace.json") -Raw) -match [regex]::Escape($Mkt))) {
  Good "this folder is the agents repository itself (clone mode: updates come with git pull)"
} elseif ((Test-Path $s) -and ((Get-Content $s -Raw) -match [regex]::Escape($Mkt))) {
  Good "project settings register $Mkt"
  if ((Get-Content $s -Raw) -match '"autoUpdate":\s*true') { Good "auto-update is on" } else { Warn "autoUpdate is not true in $s" }
} else { Warn "no registration for $Mkt in $s : run scripts\setup.ps1 -Project `"$Project`"" }
Write-Host ""
Write-Host "Reminder: Claude Code needs a paid Claude account (Pro/Max/Team/Enterprise) or an Anthropic Console account."
if ($fail) { Write-Host "Fix the [!!] items and run this again. Stuck? https://github.com/$Repo/issues/new?template=support.md"; exit 1 } else { Write-Host "Everything checked looks good." }
'''

START_HERE = '''# Start here: getting your @@COMPANY@@ agents running

This private repository was prepared for you by Nexara AI. It contains Claude Code agents (slash commands, skills and
read-only subagents) built for your business. Follow the steps in order; each takes a few minutes.

## What you need
- A **GitHub account** (free) - you were invited to this repository with it.
- A **paid Claude account**: Claude Pro, Max, Team or Enterprise, or an Anthropic Console (API) account. The free
  claude.ai plan does **not** include Claude Code.
- `git`. (Mac: it asks to install developer tools the first time. Windows: https://git-scm.com/downloads)

## 1. Accept the invitation
GitHub emailed you an invitation to `@@OWNER@@/@@REPO@@`. Open it, or go to
https://github.com/@@OWNER@@/@@REPO@@/invitations and accept. (Invitations expire after 7 days. If it expired, ask us to resend.)

## 2. Install Claude Code (skip if `claude --version` already works)
- **macOS, Linux, WSL:** `curl -fsSL https://claude.ai/install.sh | bash`
- **Windows PowerShell:** `irm https://claude.ai/install.ps1 | iex`

Open a **new** terminal and check: `claude --version`. If it says "not found", see the troubleshooting page linked from
https://code.claude.com/docs/en/setup. Then run `claude` once and sign in with your Claude account in the browser.

## 3. Let git read this private repository
Updates are downloaded from this repository, so your computer needs permission to read it:
```
gh auth login
gh auth setup-git
```
(Install the GitHub CLI from https://cli.github.com if `gh` is missing. SSH keys also work.)

## 4. Connect the agents
Pick **one**:

**A. Use them in your own project (recommended).** From a clone of this repo (or after downloading the `scripts` folder):
```
./scripts/setup.sh --project /path/to/your/project      # macOS / Linux / WSL
.\\scripts\\setup.ps1 -Project C:\\path\\to\\your\\project  # Windows PowerShell
```
It checks everything, asks before installing anything, and adds the agents to your project's `.claude/settings.json` with
auto-update on. Commit that file if you want your teammates to get the agents too (they each need repository access).

**B. Just try them here.** `git clone https://github.com/@@OWNER@@/@@REPO@@.git`, open that folder in Claude Code
(`claude`), accept the "trust this folder" prompt, and the agents load automatically.

## 5. Use your agents
Open Claude Code in the project and try:
@@AGENT_LIST@@

## Updates
We keep improving your agents in this repository. Claude Code checks for updates a few minutes after you start a session
and applies them the next time you launch it (or run `/reload-plugins`). Every change is listed in `CHANGELOG.md`.
Auto-update only works while your git credentials can read this repository. To pause updates: `/plugin` -> Marketplaces ->
`@@MKT@@` -> disable auto-update.

## Something wrong?
Run `./scripts/doctor.sh` (or `.\\scripts\\doctor.ps1`) - it checks each requirement and says how to fix it. Still stuck?
See `SUPPORT.md`.
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
- Edit files under `plugins/` by hand: Nexara manages them and your change would conflict with the next update. Put your own
  additions in `custom/` (we never touch that folder).
'''

CUSTOM_README = '''# custom/

Your own additions live here. Nexara never edits or overwrites anything in this folder, so you can add your own
skills, commands or notes without conflicting with updates to `plugins/`.
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

README_TMPL = '''# @@COMPANY@@: Claude Code agents by Nexara AI

Private repository. It is a Claude Code **plugin marketplace** (`@@MKT@@`): each agent is a plugin with a slash command, a
skill and a read-only subagent.

**New here? Read `START-HERE.md`.**

## Your agents
@@AGENT_LIST@@

## Two ways to use them
1. **In your own project** - run `scripts/setup.sh --project <folder>` (Windows: `scripts/setup.ps1 -Project <folder>`). It connects the agents to your project with auto-update on.
2. **Straight from this clone** - open this folder in Claude Code and accept the trust prompt.

## Layout
- `plugins/<agent>/` - managed by Nexara (updated for you). Don't edit by hand.
- `custom/` - yours. We never touch it.
- `scripts/` - `setup` and `doctor` helpers. `snippets/` - the settings block used by setup.
- `CHANGELOG.md` - every change we make. `SUPPORT.md` - how to get help.
'''

CLAUDE_TMPL = '''# @@COMPANY@@ agent workspace (managed by Nexara AI)

This repository provides these agents through Claude Code plugins:
@@AGENT_BULLETS@@

Rules for working here:
- `plugins/` is managed by Nexara and regenerated on updates: do not edit it by hand. Customisations go in `custom/`.
- Never read or quote secret files (`.env*`, keys, credentials).
- Do not commit secrets or customer data.
'''
