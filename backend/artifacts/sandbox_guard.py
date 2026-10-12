"""Runs inside the sandboxed interpreter before the target script: blocks outbound network (loopback only), then hands over.
Usage: python -I sandbox_guard.py <script.py> [args...]"""
import ipaddress
import runpy
import socket
import sys

_connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex
_sendto, _sendmsg = socket.socket.sendto, socket.socket.sendmsg


def _bring_up_loopback():
    """Inside `unshare -n` the loopback interface starts down; bring it up so local test servers work (trusted code, runs before the script)."""
    try:
        import fcntl
        import struct
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ifr = struct.pack("16sH14s", b"lo", 0, b"")
        flags = struct.unpack("16sH14s", fcntl.ioctl(s.fileno(), 0x8913, ifr))[1]        # SIOCGIFFLAGS
        fcntl.ioctl(s.fileno(), 0x8914, struct.pack("16sH14s", b"lo", flags | 0x1 | 0x40, b""))   # SIOCSIFFLAGS: IFF_UP | IFF_RUNNING
        s.close()
    except Exception:                                    # noqa: BLE001
        pass


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


def _guarded_send(real, addr_index):
    def f(self, *a, **k):
        addr = a[addr_index] if len(a) > addr_index else k.get("address")
        if addr is not None and self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(addr):
            raise PermissionError("network access is blocked in the sandbox (loopback only)")
        return real(self, *a, **k)
    return f


socket.socket.connect = _guarded(_connect)
socket.socket.connect_ex = _guarded(_connect_ex)
socket.socket.sendto = _guarded_send(_sendto, -1)
if True:
    _orig_sendmsg = _sendmsg

    def _sendmsg_guard(self, buffers, ancdata=(), flags=0, address=None):
        if address is not None and self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address):
            raise PermissionError("network access is blocked in the sandbox (loopback only)")
        return _orig_sendmsg(self, buffers, ancdata, flags, address) if address is not None else _orig_sendmsg(self, buffers, ancdata, flags)
    socket.socket.sendmsg = _sendmsg_guard

if __name__ == "__main__":
    import os
    if os.environ.get("NX_NETNS") == "1":
        _bring_up_loopback()
    if len(sys.argv) < 2:
        sys.exit("usage: sandbox_guard.py script.py [args]")
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name="__main__")
