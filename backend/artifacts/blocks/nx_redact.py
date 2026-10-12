"""Masking and blocking of sensitive columns (vetted block)."""


def mask_email(v):
    s = str(v)
    if "@" not in s or s.startswith("@"):
        return "***"
    local, domain = s.rsplit("@", 1)
    return f"{local[0]}***@{domain}"


def mask_phone(v):
    digits = "".join(c for c in str(v) if c.isdigit())
    return "***" + digits[-4:] if len(digits) >= 6 else "***"


def mask_text(v, keep=2):
    s = str(v)
    return s[:keep] + "***" if len(s) > keep else "***"


KINDS = {"email": mask_email, "phone": mask_phone, "text": mask_text, "full": lambda v: "***"}


class Redactor:
    """masked: {column: kind}; blocked: columns that are removed from every row."""

    def __init__(self, masked=None, blocked=None):
        self.masked = {k.lower(): v for k, v in (masked or {}).items()}
        self.blocked = {c.lower() for c in (blocked or ())}
        for kind in self.masked.values():
            if kind not in KINDS:
                raise ValueError(f"unknown mask kind: {kind}")

    def apply(self, rows):
        out = []
        for row in rows:
            new = {}
            for k, v in row.items():
                lk = k.lower()
                if lk in self.blocked:
                    continue
                new[k] = KINDS[self.masked[lk]](v) if lk in self.masked and v is not None else v
            out.append(new)
        return out
