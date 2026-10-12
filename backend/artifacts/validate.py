"""Static gate for model-written code and for the structured artifact spec. Nothing here executes the code.
check_source: AST allowlist for a handler/setup source file. check_spec: structure, names, sizes, blocks, packages, examples, paths."""
import ast
import re

BLOCKS = ("mcp", "sqlguard", "db", "audit", "redact", "http", "fs", "config", "cli")
NX_MODULES = {f"nx_{b}" for b in BLOCKS}
STD = set("""json re math datetime decimal statistics csv io collections itertools functools textwrap string typing dataclasses enum fractions calendar unicodedata
difflib heapq bisect operator hashlib base64 html zoneinfo __future__""".split())
PACKAGES = {"psycopg[binary]", "pymysql"}
BANNED_NAMES = set("""eval exec compile __import__ open input breakpoint globals locals vars getattr setattr delattr type exit quit print __builtins__ __loader__
__spec__ help memoryview dir ForwardRef singledispatch singledispatchmethod make_dataclass""".split())
# Allowed stdlib modules expose OTHER modules and file openers as attributes (statistics.sys, json.codecs, io.open, ...), and a few helpers reach dunder
# attributes through strings (operator.attrgetter, string.Formatter). Those names are banned wherever they appear, and so is every underscore attribute.
BANNED_ATTRS = {"format", "format_map", "mro", "f_globals", "f_locals", "f_back", "gi_frame", "cr_frame", "tb_frame", "func_globals",
                "open", "FileIO", "open_code", "popen", "system", "attrgetter", "methodcaller", "Formatter", "vformat", "get_field", "modules", "meta_path",
                "path_hooks", "path_importer_cache", "import_module", "find_spec", "reload", "currentframe", "stack", "getframe", "get_type_hints", "ForwardRef", "singledispatch", "singledispatchmethod", "make_dataclass", "register", "evaluate",
                "sys", "os", "subprocess", "builtins", "importlib", "ctypes", "codecs", "pickle", "marshal", "shutil", "socket", "ssl", "inspect", "gc",
                "threading", "signal", "runpy", "code", "tempfile", "glob", "types", "pathlib", "posixpath", "ntpath", "genericpath", "io", "ast", "weakref",
                "multiprocessing", "asyncio", "selectors", "select", "mmap", "fcntl", "resource", "pty", "tty", "termios", "readline", "zipimport", "pkgutil"}
MAX_SOURCE, MAX_LINES, MAX_TOTAL = 60_000, 400, 400_000
SLUG = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
TOOL = re.compile(r"[a-z][a-z0-9_]{0,39}")
REF = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
SECRETISH = re.compile(r"(password|passwd|secret|token|api_?key|dsn|connection_?string|credentials?)", re.I)
SAFE_TOOLS = {"Read", "Grep", "Glob"}


# Block constructors that reach outside the process (disk, network, databases, secrets). Their targets must come from the
# customer's configuration, never from literals the model wrote - otherwise generated code could point them at any file or host.
TARGETED = {("nx_fs", "Roots"), ("nx_http", "HttpGet"), ("nx_db", "connect"), ("nx_config", "load"), ("nx_config", "secret"), ("nx_audit", "Audit")}
DRIVERS = {"sqlite", "postgres", "mysql"}
CFG_ROOTS = {"config", "ctx"}
CFG_WRAPPERS = {"int", "float", "bool", "list", "tuple", "len", "sorted", "set", "dict", "str"}


def _cfg(node, ok):
    """True when the expression is built only from configuration values (names in `ok`), numbers/bools/None and dict keys."""
    if isinstance(node, ast.Constant):
        return not isinstance(node.value, (str, bytes))
    if isinstance(node, ast.Name):
        return node.id in ok
    if isinstance(node, ast.Attribute):
        return _cfg(node.value, ok)
    if isinstance(node, ast.Subscript):
        key = node.slice
        return _cfg(node.value, ok) and ((isinstance(key, ast.Constant) and isinstance(key.value, str)) or _cfg(key, ok))
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("get", "items", "values", "keys") and _cfg(f.value, ok):
            args = node.args[:1] if f.attr == "get" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) else []
            return all(_cfg(a, ok) for a in node.args[len(args):]) and all(_cfg(k.value, ok) for k in node.keywords)
        if isinstance(f, ast.Attribute) and f.attr == "secret" and isinstance(f.value, ast.Name) and f.value.id == "nx_config" and node.args:
            return _cfg(node.args[0], ok) and all(isinstance(a, ast.Constant) or _cfg(a, ok) for a in node.args[1:])
        if isinstance(f, ast.Name) and f.id in CFG_WRAPPERS:
            return all(_cfg(a, ok) for a in node.args) and not node.keywords
        return False
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_cfg(e, ok) for e in node.elts)
    if isinstance(node, ast.Starred):
        return _cfg(node.value, ok)
    if isinstance(node, ast.BinOp):
        return _cfg(node.left, ok) and _cfg(node.right, ok)
    if isinstance(node, ast.BoolOp):
        return all(_cfg(v, ok) for v in node.values)
    if isinstance(node, ast.IfExp):
        return all(_cfg(x, ok) for x in (node.test, node.body, node.orelse))
    if isinstance(node, ast.Compare):
        return _cfg(node.left, ok) and all(_cfg(c, ok) for c in node.comparators)
    if isinstance(node, ast.UnaryOp):
        return _cfg(node.operand, ok)
    return False


def _targeted_calls(tree):
    """Yield (call, function) for calls to TARGETED constructors, whether written nx_x.f(...) or imported by name."""
    mods = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module in NX_MODULES:
            for a in n.names:
                mods[a.asname or a.name] = (n.module, a.name)
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)] or []:
        ok = {a.arg for a in fn.args.args if a.arg in CFG_ROOTS}
        for st in ast.walk(fn):
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) and _cfg(st.value, ok):
                ok.add(st.targets[0].id)
        for c in ast.walk(fn):
            if isinstance(c, ast.Call):
                f = c.func
                key = (f.value.id, f.attr) if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else mods.get(f.id) if isinstance(f, ast.Name) else None
                if key in TARGETED:
                    yield c, key, ok


def _check_targets(tree, add):
    seen = set()
    for call, key, ok in _targeted_calls(tree):
        if id(call) in seen:
            continue
        seen.add(id(call))
        args = list(call.args)
        if key == ("nx_db", "connect") and args and isinstance(args[0], ast.Constant) and args[0].value in DRIVERS:
            args = args[1:]
        if key == ("nx_config", "secret") and len(args) > 1 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
            args = args[:1]
        vals = args + [k.value for k in call.keywords if not (k.arg == "kind" and isinstance(k.value, ast.Constant) and k.value.value in DRIVERS)]
        if not call.args and not call.keywords and key not in {("nx_config", "load")} or any(not _cfg(v, ok) for v in vals):
            add(call, f"{key[0]}.{key[1]}() must be given values taken from the configuration (config[...]), never hard-coded paths, hosts or connection strings")


def _literal(node):
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False


def check_source(src, entry="handle", min_args=1, max_args=2):
    """Returns a list of problems (empty = acceptable)."""
    if not isinstance(src, str) or not src.strip():
        return [f"source is empty (a '{entry}' function is required)"]
    if len(src) > MAX_SOURCE:
        return ["source is too large"]
    if src.count("\n") + 1 > MAX_LINES * 2:
        return [f"source is too long (more than {MAX_LINES * 2} lines)"]
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"syntax error on line {e.lineno}"]
    probs = []

    def add(node, msg):
        probs.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    for node in tree.body:                                         # top level: imports, defs, literal constants, docstring only
        ok = (isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Pass))
              or (isinstance(node, ast.ClassDef))
              or (isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and _literal(node.value))
              or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)))
        if not ok:
            add(node, "top-level code is not allowed (only imports, functions, classes and literal constants)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in NX_MODULES and a.name not in STD and a.name != "urllib.parse":     # exact names only: no submodules like json.tool
                    add(node, f"import of '{a.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            m = node.module or ""
            if node.level:
                add(node, "relative imports are not allowed")
            elif m not in NX_MODULES and m not in STD and m != "urllib.parse":
                add(node, f"import from '{m}' is not allowed")
            if any(a.name == "*" for a in node.names):
                add(node, "star imports are not allowed")
            for a in node.names:
                if a.name in BANNED_ATTRS or a.name.startswith("_") and m != "__future__":
                    add(node, f"importing '{a.name}' is not allowed")
        elif isinstance(node, ast.MatchClass) and any(k.startswith("_") or k in BANNED_ATTRS for k in node.kwd_attrs):
            add(node, "class patterns may not match underscore or restricted attributes")
        elif isinstance(node, ast.AsyncFunctionDef):
            add(node, "async functions are not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            add(node, "global/nonlocal is not allowed")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            add(node, f"'{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                add(node, "attributes starting with an underscore are not allowed")
            elif node.attr in BANNED_ATTRS:
                add(node, f"attribute '{node.attr}' is not allowed")
        elif isinstance(node, ast.FunctionDef) and node.end_lineno and node.end_lineno - node.lineno > MAX_LINES:
            add(node, f"function '{node.name}' is too long (more than {MAX_LINES} lines)")
    _check_targets(tree, add)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == entry), None)
    if fn is None:
        probs.append(f"a top-level function named '{entry}' is required")
    else:
        n = len(fn.args.posonlyargs) + len(fn.args.args)
        if not (min_args <= n <= max_args) or fn.args.vararg or fn.args.kwarg:
            probs.append(f"'{entry}' must take {min_args if min_args == max_args else f'{min_args} to {max_args}'} positional argument(s)")
    return probs


def _is_text(x):
    return isinstance(x, str) and x.strip() != ""


def _size(obj):
    if isinstance(obj, str):
        return len(obj)
    if isinstance(obj, dict):
        return sum(_size(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return sum(_size(v) for v in obj)
    return 0


def _examples(ex, where):
    probs = []
    if not isinstance(ex, list) or not ex:
        return [f"{where}: at least one example is required"]
    for i, e in enumerate(ex):
        if not isinstance(e, dict) or not isinstance(e.get("args"), dict):
            probs.append(f"{where}: example {i + 1} needs an 'args' object")
            continue
        has = (isinstance(e.get("expect_contains"), list) and any(_is_text(x) for x in e["expect_contains"])) or _is_text(e.get("expect_error")) \
            or (isinstance(e.get("expect_not_contains"), list) and e["expect_not_contains"])
        if not has:
            probs.append(f"{where}: example {i + 1} needs an expectation (expect_contains, expect_error or expect_not_contains)")
    return probs


def _fixtures(fx, where):
    probs = []
    if fx in (None, {}):
        return probs
    if not isinstance(fx, dict):
        return [f"{where}: fixtures must be an object"]
    for p in (fx.get("files") or {}):
        if not isinstance(p, str) or p.startswith("/") or ".." in p.split("/") or "\\" in p or "\x00" in p or len(p) > 120:
            probs.append(f"{where}: unsafe fixture file path")
    if fx.get("sqlite_sql") is not None and not isinstance(fx["sqlite_sql"], str):
        probs.append(f"{where}: sqlite_sql must be text")
    if isinstance(fx.get("sqlite_sql"), str) and re.search(r"\b(attach|load_extension|pragma)\b", fx["sqlite_sql"], re.I):
        probs.append(f"{where}: fixture SQL may not use ATTACH, PRAGMA or load_extension")
    env = fx.get("env") or {}
    if not isinstance(env, dict):
        probs.append(f"{where}: env must be an object")
    else:
        for k, v in env.items():
            if not isinstance(k, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,40}", k) or re.match(r"(NX_|PYTHON|LD_|DYLD_|PATH$|HOME$|TMPDIR$|SHELL$|IFS$)", k):
                probs.append(f"{where}: fixture env name '{str(k)[:30]}' is not allowed")
            if not isinstance(v, str) or len(v) > 200:
                probs.append(f"{where}: fixture env values must be short text")
    for k in (fx.get("http") or {}):
        if not isinstance(k, str) or not re.fullmatch(r"GET /[^\s]*", k):
            probs.append(f"{where}: http fixture keys look like 'GET /path'")
    return probs


def check_spec(spec):
    probs = []
    if _size(spec) > MAX_TOTAL:
        probs.append("artifact is too large")
    names = set()

    def uniq(kind, name):
        key = (kind, name)
        if key in names:
            probs.append(f"duplicate {kind} name '{name}'")
        names.add(key)

    for s in spec.get("mcp_servers") or []:
        n = s.get("name", "")
        w = f"mcp server '{n}'"
        if not (isinstance(n, str) and SLUG.fullmatch(n) and len(n) <= 40):
            probs.append(f"{w}: invalid name (lower-case words joined by hyphens, max 40)")
        uniq("server", n)
        if not _is_text(s.get("description")):
            probs.append(f"{w}: description is required")
        for b in s.get("blocks") or []:
            if b not in BLOCKS:
                probs.append(f"{w}: unknown block '{b}'")
        for p in s.get("packages") or []:
            if p not in PACKAGES:
                probs.append(f"{w}: package '{p}' is not on the allowed list")
        probs += [f"{w} setup: {p}" for p in check_source(s.get("setup_source") or "def setup(config):\n    return {}", entry="setup", min_args=1, max_args=1)]
        for c in s.get("config_schema") or []:
            k = c.get("key", "")
            if (c.get("secret") or SECRETISH.search(k)) and not k.endswith("_env"):
                probs.append(f"{w}: config key '{k}' holds a secret, so it must end in _env (it names an environment variable)")
        probs += _fixtures(s.get("fixtures"), w)
        tools = s.get("tools") or []
        if not tools:
            probs.append(f"{w}: at least one tool is required")
        seen = set()
        for t in tools:
            tn = t.get("name", "")
            tw = f"{w} tool '{tn}'"
            if not (isinstance(tn, str) and TOOL.fullmatch(tn)):
                probs.append(f"{tw}: invalid tool name (lower_snake_case, max 40)")
            if tn in seen:
                probs.append(f"{tw}: duplicate tool name")
            seen.add(tn)
            if not _is_text(t.get("description")):
                probs.append(f"{tw}: description is required")
            if t.get("read_only", True) is not True:
                probs.append(f"{tw}: only read-only tools are supported (read_only must be true)")
            sch = t.get("input_schema")
            if not (isinstance(sch, dict) and sch.get("type") == "object" and isinstance(sch.get("properties", {}), dict)):
                probs.append(f"{tw}: input_schema must be an object schema with properties")
            probs += [f"{tw}: {p}" for p in check_source(t.get("handler_source"), entry="handle", min_args=1, max_args=2)]
            probs += _examples(t.get("examples"), tw)
    for s in spec.get("scripts") or []:
        n = s.get("name", "")
        w = f"script '{n}'"
        if not (isinstance(n, str) and SLUG.fullmatch(n) and len(n) <= 40):
            probs.append(f"{w}: invalid name")
        uniq("script", n)
        if not _is_text(s.get("description")):
            probs.append(f"{w}: description is required")
        for b in s.get("blocks") or []:
            if b not in BLOCKS:
                probs.append(f"{w}: unknown block '{b}'")
        probs += [f"{w}: {p}" for p in check_source(s.get("handler_source"), entry="handle", min_args=1, max_args=1)]
        probs += _examples(s.get("examples"), w)
        probs += _fixtures(s.get("fixtures"), w)
    for s in spec.get("skills") or []:
        n = s.get("name", "")
        w = f"skill '{n}'"
        if not (isinstance(n, str) and SLUG.fullmatch(n) and len(n) <= 64):
            probs.append(f"{w}: invalid name (lower-case words joined by hyphens, max 64)")
        uniq("skill", n)
        if not _is_text(s.get("description")) or len(s["description"]) > 1024:
            probs.append(f"{w}: description is required and at most 1024 characters")
        if not _is_text(s.get("body")):
            probs.append(f"{w}: body is required")
        for a in s.get("allowed_tools") or []:
            if a not in SAFE_TOOLS:
                probs.append(f"{w}: allowed tool '{a}' is not permitted (only Read, Grep, Glob; scripts and MCP tools come from 'uses')")
        for r in s.get("references") or []:
            if not (isinstance(r.get("name"), str) and REF.fullmatch(r["name"])) or not _is_text(r.get("content")) or len(r["content"]) > 30_000:
                probs.append(f"{w}: invalid reference (name like 'rules', content up to 30 KB)")
    for s in spec.get("subagents") or []:
        n = s.get("name", "")
        w = f"subagent '{n}'"
        if not (isinstance(n, str) and SLUG.fullmatch(n) and len(n) <= 64):
            probs.append(f"{w}: invalid name")
        uniq("subagent", n)
        if not _is_text(s.get("description")) or not _is_text(s.get("instructions")):
            probs.append(f"{w}: description and instructions are required")
        for a in s.get("tools") or []:
            if a not in SAFE_TOOLS:
                probs.append(f"{w}: tool '{a}' is not permitted (only Read, Grep, Glob)")
    return probs
