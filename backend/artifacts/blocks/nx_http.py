"""Allowlisted read-only HTTP GET client (vetted block, stdlib only): exact or *.wildcard hosts, no credentials in URLs, no private
addresses unless listed, manual redirects re-checked on every hop, response size cap, GET only."""
import ipaddress
import json as _json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple


class HttpDenied(Exception):
    """Message is safe to show to the model."""


class Result(NamedTuple):
    status: int
    text: str
    json: object


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class HttpGet:
    def __init__(self, allow_hosts, *, timeout=10, max_bytes=1_000_000, headers=None, allow_insecure_hosts=(), allow_private_hosts=(), max_redirects=3):
        if not allow_hosts:
            raise ValueError("allow_hosts must not be empty")
        self.allow = [h.lower() for h in allow_hosts]
        self.insecure = {h.lower() for h in allow_insecure_hosts}
        self.private_ok = {h.lower() for h in allow_private_hosts}
        self.timeout, self.max_bytes, self.headers, self.max_redirects = timeout, max_bytes, dict(headers or {}), max_redirects
        self.opener = urllib.request.build_opener(_NoRedirect)

    def allowed_host(self, host):
        host = (host or "").lower()
        for a in self.allow:
            if a == host or (a.startswith("*.") and host.endswith(a[1:]) and len(host) > len(a) - 1):
                return True
        return False

    def _addresses(self, host):
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            pass
        try:
            return [ipaddress.ip_address(i[4][0].split("%")[0]) for i in socket.getaddrinfo(host, None)]
        except OSError:
            raise HttpDenied("The host could not be resolved.") from None

    def _check(self, url):
        if not isinstance(url, str) or url.startswith("//"):
            raise HttpDenied("URL not allowed.")
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("https", "http") or u.username or u.password or not u.hostname:
            raise HttpDenied("URL not allowed.")
        host = u.hostname.lower()
        if not self.allowed_host(host):
            raise HttpDenied("That host is not on the allowlist.")
        if u.scheme == "http" and host not in self.insecure:
            raise HttpDenied("Plain http is not allowed for this host.")
        if host not in self.private_ok:                       # an allowlisted NAME must not point at a private/loopback/metadata address
            for ip in self._addresses(host):
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                    raise HttpDenied("Private addresses are not allowed.")
        return u

    def get(self, url, params=None):
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        for _ in range(self.max_redirects + 1):
            u = self._check(url)
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "nexara-tool/1", "Accept": "application/json, text/plain;q=0.8", **self.headers})
            try:
                resp = self.opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                    url = urllib.parse.urljoin(url, e.headers["Location"])
                    continue
                return Result(e.code, "", None)
            except Exception:                         # noqa: BLE001
                raise HttpDenied("The request failed.") from None
            body = resp.read(self.max_bytes + 1)
            if len(body) > self.max_bytes:
                raise HttpDenied("The response is too large.")
            text = body.decode("utf-8", "replace")
            try:
                data = _json.loads(text)
            except ValueError:
                data = None
            return Result(resp.status, text, data)
        raise HttpDenied("Too many redirects.")
