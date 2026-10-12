"""Runs inside the sandboxed interpreter before the target script: blocks outbound network (loopback only), then hands over.
Usage: python -I sandbox_guard.py <script.py> [args...]"""
import ipaddress
import runpy
import socket
import sys

_connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex


def _loopback(addr):
    try:
        host = addr[0] if isinstance(addr, tuple) else addr
        if isinstance(host, bytes):
            host = host.decode()
        if host in ("localhost", ""):
            return True
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except (ValueError, TypeError, IndexError):
        return isinstance(addr, str)                      # AF_UNIX paths are local


def _guarded(real):
    def f(self, addr, *a, **k):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(addr):
            raise PermissionError("network access is blocked in the sandbox (loopback only)")
        return real(self, addr, *a, **k)
    return f


socket.socket.connect = _guarded(_connect)
socket.socket.connect_ex = _guarded(_connect_ex)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: sandbox_guard.py script.py [args]")
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name="__main__")
