"""Append-only audit log (vetted block): one JSON line per event, never result data or secrets, never raises."""
import json
import os
import sys
import time

DROP = {"rows", "row", "result", "results", "data", "records", "body", "content", "password", "passwd", "secret", "token", "authorization", "api_key", "apikey", "dsn", "cookie"}


class Audit:
    def __init__(self, path):
        self.path = path

    def log(self, **fields):
        try:
            clean = {}
            for k, v in fields.items():
                if k.lower() in DROP:
                    clean[k] = "<omitted>"
                elif isinstance(v, str):
                    clean[k] = v if len(v) <= 500 else v[:500] + "..."
                elif isinstance(v, (int, float, bool)) or v is None:
                    clean[k] = v
                else:
                    clean[k] = str(v)[:500]
            line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **clean}, ensure_ascii=False)
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:                      # noqa: BLE001  auditing must never break the tool
            print(f"[audit] could not write audit log: {type(e).__name__}", file=sys.stderr)
