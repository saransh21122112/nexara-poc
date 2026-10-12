"""Runs generated code in a locked-down subprocess. Layers: isolated interpreter (-I), empty environment, temp working directory, resource limits,
loopback-only network guard, wall-clock timeout with process-group kill, output cap. Switched off unless NEXARA_RUN_GENERATED_CODE=1
(off on the public host: model-written code must never run next to its credentials). It is defence in depth, not a VM: the static gate in
validate.py is the first line, and nothing generated is ever imported into the Nexara process."""
import os
import resource
import signal
import subprocess
import sys
import tempfile
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


def enabled() -> bool:
    return os.environ.get("NEXARA_RUN_GENERATED_CODE") == "1"


def require_enabled():
    if not enabled():
        raise SandboxDisabled("Running generated code is switched off on this host (set NEXARA_RUN_GENERATED_CODE=1 on a trusted machine).")


def _limits(cpu_s, mem_mb, fsize_mb):
    def apply():
        os.setsid()                                            # own process group so the whole tree can be killed
        for name, val in (("RLIMIT_CPU", (cpu_s, cpu_s)), ("RLIMIT_FSIZE", (fsize_mb << 20, fsize_mb << 20)), ("RLIMIT_CORE", (0, 0)),
                          ("RLIMIT_AS", (mem_mb << 20, mem_mb << 20))):
            try:
                resource.setrlimit(getattr(resource, name), val)
            except (ValueError, OSError):                      # not enforceable on this OS (e.g. RLIMIT_AS on macOS)
                pass
    return apply


def run_python(script, cwd, args=(), *, timeout_s=60, cpu_s=None, mem_mb=512, fsize_mb=20, env_extra=None, stdin_text=None, guard=True) -> RunResult:
    """Run `script` with the given args inside the sandbox. `cwd` must be a throwaway directory."""
    cwd = Path(cwd).resolve()
    tmp = cwd / ".tmp"
    tmp.mkdir(exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd), "TMPDIR": str(tmp), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
           "NX_GUARD_PATH": GUARD, **(env_extra or {})}
    cmd = [sys.executable, "-I"] + ([GUARD] if guard else []) + [str(script), *map(str, args)]
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, errors="replace", preexec_fn=_limits(cpu_s or timeout_s + 5, mem_mb, fsize_mb), close_fds=True)
    timed_out = False
    try:
        out, err = p.communicate(stdin_text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            p.kill()
        out, err = p.communicate()
    else:
        try:                                                   # kill anything the script left running in its group
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    truncated = len(out) > MAX_OUTPUT or len(err) > MAX_OUTPUT
    return RunResult(p.returncode == 0 and not timed_out, p.returncode if p.returncode is not None else -9, out[:MAX_OUTPUT], err[:MAX_OUTPUT], timed_out, truncated)


def temp_dir():
    return tempfile.TemporaryDirectory(prefix="nexara-sandbox-")
