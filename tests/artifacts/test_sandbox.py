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
    body = f"import subprocess, time\nsubprocess.Popen(['sleep', '60', '{marker}'])\nwhile True:\n    time.sleep(0.1)"
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
