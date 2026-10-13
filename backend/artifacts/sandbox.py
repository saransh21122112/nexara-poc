"""Runs generated code in a locked-down subprocess. Layers, outermost first:
  1. OS isolation where available: macOS `sandbox-exec` (no outbound network except loopback, no reading user files outside the work dir and the
     interpreter) or Linux `unshare -rn` (no network). Falls back to the Python-level guard alone and says so in the result.
  2. Isolated interpreter (-I), EMPTY environment, throwaway working directory, `sandbox_guard.py` (loopback-only sockets, TCP and UDP).
  3. Resource limits (CPU, file size, core, address space, process count on Linux), a memory watchdog that polls the whole process group (RLIMIT_AS is not
     enforced on macOS), wall-clock timeout, process-group kill, and file-backed output so a flood can never fill this process's memory.
Switched off unless NEXARA_RUN_GENERATED_CODE=1 (off on the public host: model-written code must never run next to its credentials). It is defence in
depth, not a VM: the static gate in validate.py is the first line and nothing generated is ever imported into the Nexara process."""
import functools
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import NamedTuple

MAX_OUTPUT = 100_000
GUARD = str(Path(__file__).resolve().parent / "sandbox_guard.py")


class SandboxDisabled(Exception):
    pass


class RunResult(NamedTuple):
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    reason: str = ""            # "", "timeout", "memory"
    isolation: str = ""         # "os-sandbox" (macOS: deny-default files/exec/network), "netns" (Linux: network only, NO filesystem isolation) or "python-guard-only"


def enabled() -> bool:
    return os.environ.get("NEXARA_RUN_GENERATED_CODE") == "1"


def require_enabled():
    if not enabled():
        raise SandboxDisabled("Running generated code is switched off on this host (set NEXARA_RUN_GENERATED_CODE=1 on a trusted machine).")


@functools.lru_cache(maxsize=1)
def _netns_works() -> bool:
    if not sys.platform.startswith("linux") or not shutil.which("unshare"):
        return False
    try:
        return subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def os_isolation_available() -> bool:
    return (sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec")) or _netns_works()


def _mac_profile(cwd: Path) -> str:
    """Deny-by-default: reads of system/interpreter files and the work dir, writes only inside the work dir, exec only of the
    interpreter itself, network only to loopback."""
    q = lambda p: p.replace("\\", "\\\\").replace('"', '\\"')
    py = {str(Path(os.path.realpath(x))) for x in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.executable)} | {sys.prefix, sys.base_prefix, sys.exec_prefix}
    py |= {str(Path(p).parent) for p in (sys.executable, os.path.realpath(sys.executable))}
    reads = sorted(py | {str(cwd), str(Path(os.path.realpath(cwd))), str(Path(GUARD).parent)})
    sub = lambda ps: " ".join(f'(subpath "{q(p)}")' for p in ps)
    exe = {sys.executable, os.path.realpath(sys.executable)}
    return ("(version 1)\n(deny default)\n"
            "(allow process-fork)\n"
            f"(allow process-exec {' '.join(f'(literal \"{q(e)}\")' for e in sorted(exe))} {sub(py)})\n"
            "(allow file-read-metadata)\n(allow signal (target self))\n(allow sysctl-read)\n(allow mach-lookup)\n(allow ipc-posix-shm-read-data)\n"
            '(allow file-read* (subpath "/usr/lib") (subpath "/System") (subpath "/Library/Preferences/Logging") (subpath "/private/var/db/timezone") (subpath "/usr/share")\n'
            '  (subpath "/opt/homebrew/lib") (subpath "/opt/homebrew/opt") (subpath "/opt/homebrew/Cellar") (subpath "/usr/local/lib") (subpath "/usr/local/opt") (subpath "/usr/local/Cellar") (subpath "/Library/Frameworks")\n'
            '  (subpath "/dev") (literal "/") (literal "/private") (literal "/private/var") (literal "/var") (literal "/Users") (literal "/Library"))\n'
            f"(allow file-read* {sub(reads)})\n"
            f"(allow file-write* {sub(sorted({str(cwd), str(Path(os.path.realpath(cwd)))}))} (literal \"/dev/null\"))\n"
            '(allow network-outbound (remote ip "localhost:*") (remote unix-socket))\n(allow network-inbound (local ip "localhost:*"))\n(allow network-bind (local ip "localhost:*"))\n')


def _prefix(cwd: Path):
    if sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec"):
        prof = cwd / ".tmp" / "sandbox.sb"
        prof.write_text(_mac_profile(cwd))
        return ["/usr/bin/sandbox-exec", "-f", str(prof)], "os-sandbox"
    if _netns_works():
        return ["unshare", "-rn", "--"], "netns"
    return [], "python-guard-only"


def _limits(cpu_s, mem_mb, fsize_mb):
    def apply():
        os.setsid()                                            # own process group so the whole tree can be killed
        items = [("RLIMIT_CPU", (cpu_s, cpu_s)), ("RLIMIT_FSIZE", (fsize_mb << 20, fsize_mb << 20)), ("RLIMIT_CORE", (0, 0)),
                 ("RLIMIT_AS", (mem_mb << 20, mem_mb << 20))]
        if sys.platform.startswith("linux"):
            items.append(("RLIMIT_NPROC", (1024, 1024)))
        for name, val in items:
            try:
                resource.setrlimit(getattr(resource, name), val)
            except (ValueError, OSError):                      # not enforceable on this OS (e.g. RLIMIT_AS on macOS)
                pass
    return apply


def _group_rss_mb(pgid: int) -> float:
    try:
        out = subprocess.run(["ps", "-A", "-o", "pgid=,rss="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    total = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(pgid) and parts[1].isdigit():
            total += int(parts[1])
    return total / 1024.0


def _kill(pgid, p):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            p.kill()
        except OSError:
            pass


def _read(path: Path):
    size = path.stat().st_size if path.exists() else 0
    with open(path, "rb") as f:
        data = f.read(MAX_OUTPUT)
    return data.decode("utf-8", "replace"), size > MAX_OUTPUT


def run_python(script, cwd, args=(), *, timeout_s=60, cpu_s=None, mem_mb=512, fsize_mb=20, env_extra=None, stdin_text=None, guard=True) -> RunResult:
    """Run `script` with the given args inside the sandbox. `cwd` must be a throwaway directory."""
    cwd = Path(cwd).resolve()
    tmp = cwd / ".tmp"
    tmp.mkdir(exist_ok=True)
    prefix, isolation = _prefix(cwd)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd), "TMPDIR": str(tmp), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
           "NX_GUARD_PATH": GUARD, **({"NX_NETNS": "1"} if isolation == "netns" else {}), **(env_extra or {})}
    cmd = prefix + [sys.executable, "-I"] + ([GUARD] if guard else []) + [str(script), *map(str, args)]
    out_f, err_f = tmp / "stdout.txt", tmp / "stderr.txt"
    reason = {"v": ""}
    with open(out_f, "wb") as fo, open(err_f, "wb") as fe:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL, stdout=fo, stderr=fe,
                             preexec_fn=_limits(cpu_s or timeout_s + 5, mem_mb, fsize_mb), close_fds=True)
        stop = threading.Event()

        def watch():
            while not stop.wait(0.2):
                if _group_rss_mb(p.pid) > mem_mb:
                    reason["v"] = "memory"
                    _kill(p.pid, p)
                    return
        threading.Thread(target=watch, daemon=True).start()
        timed_out = False
        try:
            p.communicate(stdin_text.encode() if stdin_text is not None else None, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            reason["v"] = reason["v"] or "timeout"
            _kill(p.pid, p)
            p.communicate()
        finally:
            stop.set()
        _kill(p.pid, p)                                        # anything still running in the group (already-reaped leader: ProcessLookupError is fine)
    out, t1 = _read(out_f)
    err, t2 = _read(err_f)
    rc = p.returncode if p.returncode is not None else -9
    return RunResult(rc == 0 and not timed_out and not reason["v"], rc, out, err, timed_out, t1 or t2, reason["v"], isolation)


def temp_dir():
    return tempfile.TemporaryDirectory(prefix="nexara-sandbox-")
