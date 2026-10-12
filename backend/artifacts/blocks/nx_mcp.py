"""Minimal, dependency-free MCP server over stdio (newline-delimited JSON-RPC 2.0). Vetted Nexara block: copied verbatim into delivered servers.

Supports: initialize (protocol version negotiation), notifications/initialized, ping, tools/list, tools/call.
Rules it enforces so individual tool handlers cannot get them wrong:
  * stdout carries protocol messages ONLY; all logging goes to stderr.
  * tool arguments are validated against the declared JSON Schema (a practical subset) before the handler runs.
  * a handler raises ToolError(message) for expected problems (shown to the model); any other exception becomes a generic
    "internal error" result and the details go to stderr only (no stack traces or paths leak to the model).
  * request lines and tool output are size-capped.
"""
import json
import sys

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_LINE_BYTES = 1_000_000
MAX_OUTPUT_CHARS = 100_000


class ToolError(Exception):
    """Expected, user-facing failure: the message is returned to the model as an error result."""


class Tool:
    def __init__(self, name, description, input_schema, handler, read_only=True):
        self.name, self.description, self.input_schema, self.handler, self.read_only = name, description, input_schema, handler, read_only


def _type_ok(t, v):
    if t == "string":
        return isinstance(v, str)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    if t == "null":
        return v is None
    return True


def validate(schema, value, path="arguments"):
    """Returns a list of problems (empty = valid). Subset of JSON Schema: type, enum, required, properties, additionalProperties,
    items, minimum, maximum, minLength, maxLength, minItems, maxItems."""
    errs = []
    t = schema.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        if not any(_type_ok(x, value) for x in ts):
            return [f"{path}: expected {'/'.join(ts)}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += validate(schema["items"], v, f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}.{k}: required")
        for k, v in value.items():
            if k in props:
                errs += validate(props[k], v, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: not allowed")
    return errs


def _text(result):
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str, indent=2)


class Server:
    def __init__(self, name, version, tools, instructions=None):
        self.name, self.version, self.instructions = name, version, instructions
        self.tools = {t.name: t for t in tools}
        if len(self.tools) != len(tools):
            raise ValueError("duplicate tool names")
        self.initialized = False

    # ---- protocol
    def _ok(self, rid, result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _err(self, rid, code, message):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    def handle(self, msg):
        """One decoded JSON-RPC message in, response dict (or None for notifications) out."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return self._err(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
        method, rid, params = msg["method"], msg.get("id"), msg.get("params") or {}
        is_note = "id" not in msg
        if method == "initialize":
            want = params.get("protocolVersion") if isinstance(params, dict) else None
            ver = want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            res = {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": self.name, "version": self.version}}
            if self.instructions:
                res["instructions"] = self.instructions
            return self._ok(rid, res)
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if is_note:
            return None                                   # unknown notifications are ignored, never answered
        if method == "ping":
            return self._ok(rid, {})
        if method == "tools/list":
            return self._ok(rid, {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.input_schema,
                                             "annotations": {"readOnlyHint": bool(t.read_only)}} for t in self.tools.values()]})
        if method == "tools/call":
            return self._call(rid, params)
        return self._err(rid, -32601, f"Method not found: {method}")

    def _call(self, rid, params):
        name = params.get("name") if isinstance(params, dict) else None
        tool = self.tools.get(name)
        if tool is None:
            return self._err(rid, -32602, f"Unknown tool: {name}")
        args = params.get("arguments") if params.get("arguments") is not None else {}
        problems = validate(tool.input_schema, args)
        if problems:
            return self._ok(rid, {"content": [{"type": "text", "text": "Invalid arguments: " + "; ".join(problems[:5])}], "isError": True})
        try:
            out = tool.handler(args)
            err = False
        except ToolError as e:
            out, err = f"{e}", True
        except Exception as e:                           # noqa: BLE001  never leak internals to the model
            print(f"[{self.name}] tool {name} failed: {type(e).__name__}: {e}", file=sys.stderr)
            out, err = "Internal error while running this tool.", True
        text = _text(out)
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + "\n[output truncated]"
        res = {"content": [{"type": "text", "text": text}], "isError": err}
        if not err and isinstance(out, dict):
            res["structuredContent"] = out
        return self._ok(rid, res)

    # ---- transport
    def serve(self, stdin=None, stdout=None):
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            if len(line.encode("utf-8", "ignore")) > MAX_LINE_BYTES:
                self._write(stdout, self._err(None, -32600, "Request too large"))
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self._write(stdout, self._err(None, -32700, "Parse error"))
                continue
            if isinstance(msg, list):                    # batches: answer requests only
                out = [r for r in (self.handle(m) for m in msg) if r is not None]
                if out:
                    self._write(stdout, out)
                continue
            resp = self.handle(msg)
            if resp is not None:
                self._write(stdout, resp)

    @staticmethod
    def _write(stdout, obj):
        stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
        stdout.flush()
