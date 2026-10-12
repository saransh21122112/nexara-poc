"""The sandbox that runs generated code: isolated, time-boxed, no environment leaks, no outbound network."""
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.artifacts import sandbox as sb


def script(tmp_path, body):
    p = tmp_path / "t.py"
    p.write_text(body)
    return p


def test_it_is_off_unless_switched_on(monkeypatch):
    monkeypatch.delenv("NEXARA_RUN_GENERATED_CODE", raising=False)
    assert sb.enabled() is False
    with pytest.raises(sb.SandboxDisabled):
        sb.require_enabled()
    monkeypatch.setenv("NEXARA_RUN_GENERATED_CODE", "1")
    assert sb.enabled() is True
    sb.require_enabled()


def test_runs_a_script_and_captures_output(tmp_path):
    r = sb.run_python(script(tmp_path, "print('hello')"), cwd=tmp_path)
    assert r.ok and r.returncode == 0 and r.stdout.strip() == "hello" and not r.timed_out


def test_environment_is_empty_and_secrets_do_not_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXARA_SECRET_PROBE", "hunter2")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:pw@h/db")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")
    r = sb.run_python(script(tmp_path, "import os, json\nprint(json.dumps(sorted(os.environ)))"), cwd=tmp_path)
    assert "hunter2" not in r.stdout and "NEXARA_SECRET_PROBE" not in r.stdout and "DATABASE_URL" not in r.stdout and "OAUTH" not in r.stdout
    r = sb.run_python(script(tmp_path, "import os\nprint(os.environ.get('MINE'))"), cwd=tmp_path, env_extra={"MINE": "yes"})
    assert r.stdout.strip() == "yes"


def test_isolated_interpreter_cannot_import_the_app_and_runs_in_the_temp_dir(tmp_path):
    r = sb.run_python(script(tmp_path, "import os\ntry:\n    import backend\n    print('LEAK')\nexcept ImportError:\n    print('isolated', os.getcwd() == %r)" % str(tmp_path.resolve())), cwd=tmp_path)
    assert "isolated" in r.stdout and "LEAK" not in r.stdout


def test_infinite_loops_are_killed_by_the_timeout(tmp_path):
    t0 = time.time()
    r = sb.run_python(script(tmp_path, "while True:\n    pass"), cwd=tmp_path, timeout_s=2)
    assert r.timed_out and not r.ok and time.time() - t0 < 10


def test_descendant_processes_are_killed_too(tmp_path):
    marker = f"nx-sandbox-{os.getpid()}-{int(time.time())}"
    body = f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', '{marker}'])\nwhile True:\n    time.sleep(0.1)"
    r = sb.run_python(script(tmp_path, body), cwd=tmp_path, timeout_s=2)
    time.sleep(0.5)
    left = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
    assert r.timed_out and left == []


def test_outbound_network_is_blocked_but_loopback_works(tmp_path):
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
    threading.Thread(target=lambda: srv.accept(), daemon=True).start()
    body = (f"import socket\n"
            f"s = socket.create_connection(('127.0.0.1', {port}), timeout=2); print('loopback ok'); s.close()\n"
            f"try:\n    socket.create_connection(('93.184.216.34', 80), timeout=2)\n    print('INTERNET')\nexcept OSError as e:\n    print('blocked', type(e).__name__)\n"
            f"try:\n    socket.create_connection(('10.0.0.5', 80), timeout=2)\n    print('PRIVATE')\nexcept OSError as e:\n    print('blocked-private')\n")
    r = sb.run_python(script(tmp_path, body), cwd=tmp_path)
    srv.close()
    assert "loopback ok" in r.stdout and "blocked" in r.stdout and "INTERNET" not in r.stdout and "PRIVATE" not in r.stdout, r.stdout + r.stderr


def test_output_is_capped(tmp_path):
    r = sb.run_python(script(tmp_path, "import sys\nsys.stdout.write('x' * 5_000_000)"), cwd=tmp_path)
    assert len(r.stdout) <= sb.MAX_OUTPUT + 100 and r.truncated


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS does not enforce RLIMIT_AS; the memory cap is enforced on Linux (CI and Render)")
def test_memory_hogs_are_stopped(tmp_path):
    r = sb.run_python(script(tmp_path, "x = bytearray(4 * 1024 ** 3)\nprint('allocated')"), cwd=tmp_path, mem_mb=256, timeout_s=20)
    assert "allocated" not in r.stdout and not r.ok


def test_a_failing_script_reports_failure_and_stderr(tmp_path):
    r = sb.run_python(script(tmp_path, "import sys\nprint('oops', file=sys.stderr)\nsys.exit(3)"), cwd=tmp_path)
    assert not r.ok and r.returncode == 3 and "oops" in r.stderr


# ---- review findings: OS-level isolation, memory watchdog, bounded output, UDP
needs_os = pytest.mark.skipif(not sb.os_isolation_available(), reason="no OS-level isolation available here (sandbox-exec on macOS, unshare on Linux)")


@needs_os
def test_the_os_layer_blocks_the_network_even_if_the_python_guard_is_bypassed(tmp_path):
    r = sb.run_python(script(tmp_path, "import _socket\ns = _socket.socket()\ntry:\n    s.connect(('93.184.216.34', 80))\n    print('RAW NET')\nexcept OSError:\n    print('blocked')"), cwd=tmp_path)
    assert "blocked" in r.stdout and "RAW NET" not in r.stdout and r.isolation != "python-guard-only"


@pytest.mark.skipif(sys.platform != "darwin", reason="file-read isolation is implemented with macOS sandbox-exec; on Linux it is not enforced (documented)")
def test_files_outside_the_working_directory_cannot_be_read(tmp_path):
    secret = Path.home() / ".nexara-sandbox-probe"
    secret.write_text("top secret")
    try:
        r = sb.run_python(script(tmp_path, f"try:\n    print(open({str(secret)!r}).read())\nexcept OSError:\n    print('denied')"), cwd=tmp_path)
    finally:
        secret.unlink()
    assert "denied" in r.stdout and "top secret" not in r.stdout


def test_udp_exfiltration_is_blocked_by_the_python_guard(tmp_path):
    body = "import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\ntry:\n    s.sendto(b'x', ('93.184.216.34', 53))\n    print('SENT')\nexcept OSError:\n    print('blocked')"
    r = sb.run_python(script(tmp_path, body), cwd=tmp_path)
    assert "blocked" in r.stdout and "SENT" not in r.stdout


def test_memory_watchdog_kills_a_hog_on_every_platform(tmp_path):
    r = sb.run_python(script(tmp_path, "import time\nx = bytearray(1500 * 1024 ** 2)\nfor i in range(0, len(x), 4096):\n    x[i] = 1\ntime.sleep(30)\nprint('survived')"),
                      cwd=tmp_path, mem_mb=256, timeout_s=20)
    assert not r.ok and "survived" not in r.stdout and r.reason == "memory"


def test_an_output_flood_cannot_fill_memory(tmp_path):
    r = sb.run_python(script(tmp_path, "import sys\nchunk = 'x' * 1_000_000\nfor _ in range(400):\n    sys.stdout.write(chunk)"), cwd=tmp_path, fsize_mb=5, timeout_s=20)
    assert r.truncated and len(r.stdout) <= sb.MAX_OUTPUT and not r.ok


# ---- review findings: the OS profile must not be "allow everything"
@pytest.mark.skipif(sys.platform != "darwin", reason="the file-write/exec profile is the macOS sandbox-exec one")
def test_macos_profile_denies_writes_outside_the_work_dir_and_running_other_programs(tmp_path):
    probe = Path.home() / ".nexara-sandbox-write-probe"
    body = (f"import subprocess, sys\n"
            f"try:\n    open({str(probe)!r}, 'w').write('x'); print('WROTE-HOME')\nexcept OSError:\n    print('write-denied')\n"
            f"try:\n    open('/tmp/nexara-sandbox-write-probe', 'w').write('x'); print('WROTE-TMP')\nexcept OSError:\n    print('tmp-denied')\n"
            f"try:\n    subprocess.run(['/bin/ls', '/'], capture_output=True, check=True); print('RAN-LS')\nexcept (OSError, subprocess.CalledProcessError):\n    print('exec-denied')\n"
            f"open('inside.txt', 'w').write('ok'); print('wrote-inside')\n"
            f"try:\n    open('/etc/hosts').read(); print('READ-ETC')\nexcept OSError:\n    print('etc-denied')\n")
    try:
        r = sb.run_python(script(tmp_path, body), cwd=tmp_path)
    finally:
        probe.unlink(missing_ok=True)
        Path("/tmp/nexara-sandbox-write-probe").unlink(missing_ok=True)
    out = r.stdout
    assert "write-denied" in out and "tmp-denied" in out and "exec-denied" in out and "wrote-inside" in out and "etc-denied" in out, out + r.stderr
    assert "WROTE" not in out and "RAN-LS" not in out and "READ-ETC" not in out and (tmp_path / "inside.txt").exists()


def test_the_interpreter_can_still_run_its_own_subprocesses_inside_the_sandbox(tmp_path):
    body = "import subprocess, sys\nr = subprocess.run([sys.executable, '-c', 'print(42)'], capture_output=True, text=True)\nprint(r.stdout.strip())"
    assert sb.run_python(script(tmp_path, body), cwd=tmp_path).stdout.strip() == "42"
