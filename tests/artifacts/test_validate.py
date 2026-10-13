"""The static gate for model-written handler code and for the structured spec."""
import pytest
from backend.artifacts.validate import _fixtures

from backend.artifacts import validate as v

GOOD = '''
import json
import re
from datetime import date, timedelta
import nx_sqlguard, nx_db

LIMIT = 100

def helper(x):
    return [i * 2 for i in range(x)]

def setup(config):
    return {"limit": LIMIT}

def handle(args, ctx):
    rows = helper(int(args.get("n", 3)))
    return {"rows": rows, "today": str(date.today() + timedelta(days=1)), "ok": bool(re.match(r"a", "a"))}
'''


def problems(src, **kw):
    return v.check_source(src, **kw)


def test_a_normal_handler_passes():
    assert problems(GOOD) == []


ESCAPES = {
    "import os": "import os\ndef handle(args, ctx):\n    return os.listdir('/')",
    "from os import system": "from os import system\ndef handle(args, ctx):\n    return 1",
    "import subprocess": "import subprocess\ndef handle(args, ctx):\n    return 1",
    "import socket": "import socket\ndef handle(args, ctx):\n    return 1",
    "import sqlite3 directly": "import sqlite3\ndef handle(args, ctx):\n    return 1",
    "import urllib.request": "import urllib.request\ndef handle(args, ctx):\n    return 1",
    "import importlib": "import importlib\ndef handle(args, ctx):\n    return 1",
    "star import": "from json import *\ndef handle(args, ctx):\n    return 1",
    "relative import": "from . import x\ndef handle(args, ctx):\n    return 1",
    "dunder import": "def handle(args, ctx):\n    return __import__('os').getcwd()",
    "eval": "def handle(args, ctx):\n    return eval(args['x'])",
    "exec": "def handle(args, ctx):\n    exec(args['x'])\n    return 1",
    "compile": "def handle(args, ctx):\n    return compile('1', 'x', 'eval')",
    "open": "def handle(args, ctx):\n    return open('/etc/passwd').read()",
    "print (would corrupt the MCP stream)": "def handle(args, ctx):\n    print('x')\n    return 1",
    "getattr trick": "def handle(args, ctx):\n    return getattr(ctx, 'x')",
    "globals": "def handle(args, ctx):\n    return globals()",
    "dunder attr": "def handle(args, ctx):\n    return ().__class__.__bases__[0].__subclasses__()",
    "dunder globals": "def handle(args, ctx):\n    return handle.__globals__",
    "builtins name": "def handle(args, ctx):\n    return __builtins__",
    "type()": "def handle(args, ctx):\n    return type('X', (), {})",
    "input": "def handle(args, ctx):\n    return input()",
    "breakpoint": "def handle(args, ctx):\n    breakpoint()",
    "exit": "def handle(args, ctx):\n    exit(1)",
    "sys via nx?": "import sys\ndef handle(args, ctx):\n    return sys.argv",
    "ctypes": "import ctypes\ndef handle(args, ctx):\n    return 1",
    "pickle": "import pickle\ndef handle(args, ctx):\n    return 1",
    "pathlib": "from pathlib import Path\ndef handle(args, ctx):\n    return Path('/').iterdir()",
    "shutil": "import shutil\ndef handle(args, ctx):\n    return 1",
    "threading": "import threading\ndef handle(args, ctx):\n    return 1",
    "string-built name": "def handle(args, ctx):\n    f = vars()\n    return f",
    "async def": "async def handle(args, ctx):\n    return 1",
    "global stmt": "X = 1\ndef handle(args, ctx):\n    global X\n    X = 2\n    return 1",
    "lambda dunder": "def handle(args, ctx):\n    return (lambda: 1).__code__",
    "top-level side effect call": "import json\njson.dumps({})\ndef handle(args, ctx):\n    return 1",
    "top-level loop": "for i in range(10):\n    pass\ndef handle(args, ctx):\n    return 1",
    "class def with exec": "class A:\n    x = exec('1')\ndef handle(args, ctx):\n    return 1",
    # ---- reaching banned capabilities through ALLOWED modules (review finding)
    "io.open is the builtin open": "import io\ndef handle(args, ctx):\n    return io.open('/etc/passwd').read()",
    "io.FileIO": "import io\ndef handle(args, ctx):\n    return io.FileIO('/etc/passwd').read()",
    "module hop statistics.sys": "import statistics\ndef handle(args, ctx):\n    return statistics.sys.modules['os'].listdir('/')",
    "module hop json.codecs": "import json\ndef handle(args, ctx):\n    return json.codecs.open('/etc/passwd').read()",
    "module hop csv.os": "import csv\ndef handle(args, ctx):\n    return csv.os.listdir('/')",
    "private attr collections._sys": "import collections\ndef handle(args, ctx):\n    return collections._sys.modules",
    "from json import codecs": "from json import codecs\ndef handle(args, ctx):\n    return codecs.open('/etc/passwd')",
    "from csv import io": "from csv import io\ndef handle(args, ctx):\n    return io.open('/etc/passwd')",
    "from collections import _sys": "from collections import _sys\ndef handle(args, ctx):\n    return _sys",
    "operator.attrgetter dunder by string": "import operator\ndef handle(args, ctx):\n    return operator.attrgetter('__globals__')(handle)",
    "operator.methodcaller": "import operator\ndef handle(args, ctx):\n    return operator.methodcaller('__reduce__')(args)",
    "string.Formatter attribute walk": "import string\ndef handle(args, ctx):\n    return string.Formatter().format('{0.__class__}', args)",
    "dotted import of a submodule": "import json.tool\ndef handle(args, ctx):\n    return 1",
    "dataclasses.inspect": "import dataclasses\ndef handle(args, ctx):\n    return dataclasses.inspect.currentframe()",
    "typing.sys": "import typing\ndef handle(args, ctx):\n    return typing.sys.argv",
    "functools.types": "import functools\ndef handle(args, ctx):\n    return functools.types",
    "importlib via attribute": "import json\ndef handle(args, ctx):\n    return json.importlib",
    "frame walking": "import json\ndef handle(args, ctx):\n    return handle.currentframe",
    "any single-underscore attribute": "def handle(args, ctx):\n    return args._private",
    "decorator calling banned": "def deco(f):\n    return f\n@deco\n@open\ndef handle(args, ctx):\n    return 1",
}


@pytest.mark.parametrize("name", list(ESCAPES))
def test_every_escape_attempt_is_rejected(name):
    assert problems(ESCAPES[name]), f"accepted: {name}"


HARDCODED = {
    "fs root hard-coded": "import nx_fs\ndef handle(args, ctx):\n    return nx_fs.Roots(['/']).read_text('etc/passwd')",
    "fs roots keyword": "import nx_fs\ndef handle(args, ctx):\n    return nx_fs.Roots(roots=['/etc'])",
    "fs default literal": "import nx_fs\ndef handle(args, ctx):\n    return nx_fs.Roots(ctx['config'].get('roots', ['/']))",
    "http metadata host": "import nx_http\ndef handle(args, ctx):\n    return nx_http.HttpGet(['169.254.169.254'], allow_private_hosts=['169.254.169.254']).get('http://169.254.169.254/')",
    "http wildcard": "import nx_http\ndef handle(args, ctx):\n    return nx_http.HttpGet(['*'])",
    "http appended host": "import nx_http\ndef handle(args, ctx):\n    return nx_http.HttpGet(ctx['hosts'] + ['evil.example'])",
    "db path": "import nx_db\ndef handle(args, ctx):\n    return nx_db.connect('sqlite', path='/etc/hosts')",
    "db dsn": "import nx_db\ndef handle(args, ctx):\n    return nx_db.connect('postgres', dsn='postgresql://u:p@internal-db/x')",
    "config load path": "import nx_config\ndef handle(args, ctx):\n    return nx_config.load('/etc/passwd')",
    "audit path": "import nx_audit\ndef handle(args, ctx):\n    return nx_audit.Audit('/Users/x/.ssh/authorized_keys')",
    "from-import form": "from nx_fs import Roots\ndef handle(args, ctx):\n    return Roots(['/'])",
    "joined string path": "import nx_fs\ndef handle(args, ctx):\n    return nx_fs.Roots([f'/etc/{args[\"x\"]}'])",
}


@pytest.mark.parametrize("name", list(HARDCODED))
def test_block_targets_must_come_from_the_configuration_never_from_code(name):
    probs = problems(HARDCODED[name])
    assert probs and any("configuration" in p for p in probs), (name, probs)


def test_block_calls_that_take_their_targets_from_the_config_are_accepted():
    src = ("import nx_fs, nx_http, nx_db, nx_config, nx_audit\n"
           "def setup(config):\n"
           "    db = nx_db.connect('sqlite', path=config['database']['path'], timeout_s=int(config['timeout_seconds']), max_rows=500)\n"
           "    return {'fs': nx_fs.Roots(config['roots']), 'http': nx_http.HttpGet(config['allow_hosts'], timeout=5, max_bytes=100000), 'db': db,\n"
           "            'audit': nx_audit.Audit(config['audit_log']), 'pg': nx_db.connect('postgres', dsn=nx_config.secret(config['database'], 'dsn_env'))}\n")
    assert problems(src, entry="setup", min_args=1, max_args=1) == []


def test_safe_everyday_code_is_still_accepted_after_the_hardening():
    src = ("import json, csv, re, math\nfrom io import StringIO\nfrom collections import Counter, defaultdict\nfrom datetime import datetime, timezone\n"
           "from decimal import Decimal\nfrom statistics import mean\nimport itertools, functools, operator\n"
           "def handle(args, ctx):\n"
           "    rows = list(csv.DictReader(StringIO(args['csv'])))\n"
           "    c = Counter(r['a'] for r in rows)\n"
           "    total = sum(Decimal(r['b']) for r in rows)\n"
           "    items = sorted(c.items(), key=operator.itemgetter(1), reverse=True)\n"
           "    lst = [1, 2, 3]; lst.remove(2)\n"
           "    return {'n': len(rows), 'avg': mean([1, 2]), 'total': str(total), 'top': items[:1], 'msg': f'{len(rows)} rows', 'ts': datetime.now(timezone.utc).isoformat(), 'lst': lst,\n"
           "            'chain': list(itertools.chain([1], [2])), 'p': functools.reduce(operator.add, [1, 2]), 'j': json.dumps({'a': 1}), 'm': math.floor(2.5), 'r': bool(re.search('a', 'a'))}\n")
    assert problems(src) == []


def test_missing_or_wrong_handler_signature_and_syntax_errors():
    assert any("handle" in p for p in problems("def other(args, ctx):\n    return 1"))
    assert any("handle" in p for p in problems("def handle():\n    return 1"))
    assert any("handle" in p for p in problems("def handle(a, b, c, d):\n    return 1"))
    assert any("syntax" in p.lower() for p in problems("def handle(args, ctx:\n    return 1"))
    assert problems("def handle(args):\n    return 1", entry="handle", min_args=1, max_args=1) == []
    assert problems("def handle(args, ctx):\n    return 1", entry="handle", min_args=2, max_args=2) == []
    assert problems("") and problems("   ")


def test_size_and_line_caps():
    assert any("large" in p.lower() for p in problems("def handle(args, ctx):\n    return 1\n" + "# x\n" * 40000))
    assert any("long" in p.lower() for p in problems("def handle(args, ctx):\n" + "    x = 1\n" * 900 + "    return 1"))


def test_allowed_nx_blocks_and_standard_modules():
    for imp in ("nx_mcp", "nx_sqlguard", "nx_db", "nx_audit", "nx_redact", "nx_http", "nx_fs", "nx_config", "nx_cli"):
        assert problems(f"import {imp}\ndef handle(args, ctx):\n    return 1") == []
    for mod in ("json", "re", "math", "datetime", "decimal", "statistics", "csv", "io", "collections", "itertools", "functools", "textwrap", "string", "typing",
                "dataclasses", "enum", "fractions", "calendar", "unicodedata", "difflib", "heapq", "bisect", "operator", "hashlib", "base64", "html", "zoneinfo"):
        assert problems(f"import {mod}\ndef handle(args, ctx):\n    return 1") == [], mod
    assert problems("from urllib.parse import urlparse\ndef handle(args, ctx):\n    return 1") == []
    assert problems("import nx_unknown\ndef handle(args, ctx):\n    return 1")


# ------------------------------------------------------------------ the structured spec
def mcp_spec(**over):
    tool = {"name": "run_query", "description": "Run a read-only query.", "read_only": True,
            "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
            "handler_source": "def handle(args, ctx):\n    return {'rows': []}",
            "examples": [{"args": {"sql": "SELECT 1"}, "expect_contains": ["rows"]}, {"args": {"sql": "DROP TABLE x"}, "expect_error": "not allowed"}]}
    srv = {"name": "aurora-readonly", "description": "Read-only SQL.", "blocks": ["sqlguard", "db"], "packages": [], "setup_source": "def setup(config):\n    return {}",
           "config_schema": [{"key": "dsn_env", "description": "Env var holding the DSN", "secret": True, "example": "AURORA_DSN"}],
           "fixtures": {"sqlite_sql": "create table t (a int)"}, "tools": [tool]}
    spec = {"kind": "MCP server", "mcp_servers": [srv]}
    spec.update(over)
    return spec


def test_a_well_formed_mcp_spec_passes():
    assert v.check_spec(mcp_spec()) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s["mcp_servers"][0]["tools"][0].update(read_only=False), "read-only"),
    (lambda s: s["mcp_servers"][0]["tools"][0].update(name="Run Query"), "tool name"),
    (lambda s: s["mcp_servers"][0].update(name="../evil"), "name"),
    (lambda s: s["mcp_servers"][0]["tools"][0].update(handler_source="import os\ndef handle(args, ctx):\n    return 1"), "import"),
    (lambda s: s["mcp_servers"][0].update(blocks=["sqlguard", "rootkit"]), "block"),
    (lambda s: s["mcp_servers"][0].update(packages=["requests"]), "package"),
    (lambda s: s["mcp_servers"][0]["tools"][0].update(input_schema={"type": "array"}), "input_schema"),
    (lambda s: s["mcp_servers"][0]["tools"][0].update(examples=[]), "example"),
    (lambda s: s["mcp_servers"][0]["config_schema"][0].update(key="password", secret=True), "_env"),
    (lambda s: s["mcp_servers"][0].update(tools=[]), "tool"),
    (lambda s: s["mcp_servers"][0]["tools"].append(dict(s["mcp_servers"][0]["tools"][0])), "duplicate"),
    (lambda s: s["mcp_servers"][0].update(setup_source="import os\ndef setup(config):\n    return {}"), "import"),
    (lambda s: s["mcp_servers"][0]["tools"][0]["examples"][0].update(expect_contains=[], expect_error=None), "expect"),
    (lambda s: s["mcp_servers"][0]["tools"][0].update(description=""), "description"),
    (lambda s: s["mcp_servers"][0].update(fixtures={"files": {"../../etc/passwd": "x"}}), "fixture"),
])
def test_spec_problems_are_reported(mutate, needle):
    s = mcp_spec(); mutate(s)
    probs = v.check_spec(s)
    assert probs and any(needle.lower() in p.lower() for p in probs), (needle, probs)


def test_total_size_cap_and_slugs():
    s = mcp_spec()
    s["mcp_servers"][0]["tools"][0]["handler_source"] = "def handle(args, ctx):\n    return 1\n" + "# " + "x" * 130_000
    assert any("large" in p.lower() for p in v.check_spec(s))


def test_script_and_skill_specs():
    script = {"name": "csv-check", "description": "Check a CSV.", "handler_source": "def handle(args):\n    return {'n': 1}",
              "examples": [{"args": {"csv": "a,b\n1,2"}, "expect_contains": ["n"]}], "blocks": ["cli"]}
    skill = {"name": "triage", "description": "Triage tickets. Use when the user pastes a ticket.", "argument_hint": "the ticket text", "body": "1. Read the ticket.\n2. Classify it.\n",
             "references": [{"name": "rules", "content": "# Rules\nBe kind."}], "allowed_tools": ["Read", "Grep"]}
    assert v.check_spec({"kind": "Tool", "scripts": [script], "skills": [skill]}) == []
    bad = dict(script, name="Bad Name!")
    assert v.check_spec({"kind": "Tool", "scripts": [bad]})
    assert v.check_spec({"kind": "Skill", "skills": [dict(skill, allowed_tools=["Bash(rm -rf /:*)"])]})
    assert v.check_spec({"kind": "Skill", "skills": [dict(skill, references=[{"name": "../x", "content": "y"}])]})
    assert v.check_spec({"kind": "Skill", "skills": [dict(skill, name="x" * 80)]})
    assert v.check_spec({"kind": "Skill", "skills": [dict(skill, body="")]})
    assert v.check_spec({"kind": "Skill", "skills": [dict(skill, allowed_tools=["WebFetch"])]}), "network tools are not allowed in v1"


@pytest.mark.parametrize("name", ["NX_GUARD_PATH", "PYTHONPATH", "PYTHONSTARTUP", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "PATH", "HOME", "NX_CONFIG", "lower"])
def test_fixture_env_cannot_change_how_the_interpreter_or_guard_starts(name):
    assert _fixtures({"env": {name: "x"}}, "t"), name


def test_fixture_env_may_carry_plain_data():
    assert _fixtures({"env": {"CRM_TOKEN": "fake"}}, "t") == []


@pytest.mark.parametrize("src", [
    "def handle(args, ctx):\n    match args:\n        case object(__class__=k):\n            return k\n    return 1",
    "from typing import ForwardRef\ndef handle(args, ctx):\n    return ForwardRef('1+1')",
    "import functools\n@functools.singledispatch\ndef g(x):\n    return 1\ndef handle(args, ctx):\n    return g(1)",
    "import dataclasses\ndef handle(args, ctx):\n    return dataclasses.make_dataclass('A', [('x', 'int')])",
    "def handle(args, ctx):\n    return dir(ctx)",
])
def test_string_evaluating_and_reflection_helpers_are_rejected(src):
    assert problems(src)


def _base_spec(uses):
    return {"scripts": [{"name": "csv-check", "description": "d", "handler_source": "def handle(args):\n    return 1", "examples": [{"args": {}, "expect_contains": ["1"]}]}],
            "mcp_servers": [{"name": "kb", "description": "d", "tools": [{"name": "search", "description": "d", "input_schema": {"type": "object", "properties": {}},
                                                                            "handler_source": "def handle(args, ctx):\n    return 1", "examples": [{"args": {}, "expect_contains": ["1"]}]}]}],
            "skills": [{"name": "s", "description": "d", "body": "b", "uses": uses}]}


@pytest.mark.parametrize("uses", [["csv-check"], ["kb"], ["kb/search"], ["csv-check", "kb/search"]])
def test_skill_uses_that_point_at_real_components_are_accepted(uses):
    from backend.artifacts.validate import check_spec
    assert check_spec(_base_spec(uses)) == []


@pytest.mark.parametrize("uses", [["nope"], ["kb/nope"], ["nope/search"], ["../x"], ["kb/search/extra"]])
def test_skill_uses_that_point_nowhere_are_rejected(uses):
    from backend.artifacts.validate import check_spec
    assert any("uses" in p for p in check_spec(_base_spec(uses)))
