"""Read-only SQL guard (vetted Nexara block, stdlib only). Lets a single analytic SELECT through and rejects everything else.

validate(sql, allow, ...) -> Checked(sql, tables, columns); raises SqlRejected with a short, safe message.
`allow` maps table -> list of readable columns (blocked / sensitive columns are simply left out of the list).
Design: tokenize strictly (no comments, no backslashes, no non-ASCII outside string literals), then check tokens against allowlists
(statement shape, functions, tables, columns), clamp LIMIT, and REBUILD the SQL from tokens so nothing the tokenizer did not see can survive.
This is defence in depth: the database user must still have SELECT-only grants and a read-only transaction.
"""
import re
from typing import NamedTuple


class SqlRejected(Exception):
    pass


class Checked(NamedTuple):
    sql: str
    tables: set
    columns: set


BANNED = set("""insert update delete drop alter create truncate grant revoke call exec execute merge replace into outfile dumpfile load copy set use show pragma
attach detach vacuum lock explain analyze begin commit rollback savepoint declare handler prepare deallocate do import export reindex cluster comment checkpoint
listen notify unlisten refresh rename fetch open close values window recursive returning""".split())
KEYWORDS = set("""select distinct all from where group by order having limit offset as and or not in is null like ilike between exists case when then else end join inner
left right full outer cross natural on using union intersect except asc desc true false current_date current_timestamp interval date timestamp time over partition
rows range unbounded preceding following current row with escape""".split())
CLAUSE_END = {"where", "group", "order", "having", "limit", "offset", "union", "intersect", "except", "on", "using"}
FUNCS = set("""count sum avg min max round floor ceil ceiling abs coalesce nullif ifnull lower upper length char_length substr substring trim ltrim rtrim concat date datetime
strftime date_trunc extract cast year month day hour minute second greatest least row_number rank dense_rank lag lead sign power sqrt mod left right position instr
to_char date_format timestamp""".split())
TYPE_WORDS = set("integer int bigint smallint text varchar char decimal numeric float real double boolean date timestamp".split())
OPS = {"=", "<", ">", "<=", ">=", "<>", "!=", "+", "-", "*", "/", "%", "||"}
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUM = re.compile(r"\d+(\.\d+)?")


def _tokenize(sql, max_chars, max_tokens):
    if not isinstance(sql, str) or not sql.strip():
        raise SqlRejected("Empty query.")
    if len(sql) > max_chars:
        raise SqlRejected("Query is too long.")
    toks, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in " \t\r\n":
            i += 1
        elif c == "'":
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise SqlRejected("Unterminated string literal.")
                if sql[j] == "\\":
                    raise SqlRejected("Backslashes are not allowed.")
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        buf.append("'"); j += 2; continue
                    break
                if ord(sql[j]) < 32 and sql[j] not in "\t\r\n":
                    raise SqlRejected("Control characters are not allowed.")
                buf.append(sql[j]); j += 1
            toks.append(("str", "".join(buf))); i = j + 1
        elif c in '"`':
            j = sql.find(c, i + 1)
            if j < 0:
                raise SqlRejected("Unterminated quoted identifier.")
            name = sql[i + 1:j]
            if not name or not name.isascii() or any(ord(x) < 32 for x in name):
                raise SqlRejected("Invalid quoted identifier.")
            toks.append(("qid", name)); i = j + 1
        elif c == "-" and sql[i:i + 2] == "--" or c == "#" or sql[i:i + 2] == "/*":
            raise SqlRejected("Comments are not allowed.")
        elif c.isdigit():
            m = NUM.match(sql, i); toks.append(("num", m.group())); i = m.end()
        elif c.isascii() and (c.isalpha() or c == "_"):
            m = WORD.match(sql, i); toks.append(("word", m.group().lower())); i = m.end()
        elif sql[i:i + 2] in ("<=", ">=", "<>", "!=", "||"):
            toks.append(("op", sql[i:i + 2])); i += 2
        elif c in "=<>+-*/%":
            toks.append(("op", c)); i += 1
        elif c in "(),.;":
            toks.append(("p", c)); i += 1
        else:
            raise SqlRejected("Unsupported character in query.")
        if len(toks) > max_tokens:
            raise SqlRejected("Query is too complex.")
    return toks


def _val(t):
    return t[1] if t else None


def validate(sql, allow, *, max_limit=1000, star_tables=(), max_chars=8000, max_tokens=600, max_depth=8):
    allow = {k.lower(): {c.lower() for c in v} for k, v in allow.items()}
    star_tables = {t.lower() for t in star_tables}
    toks = _tokenize(sql, max_chars, max_tokens)
    if toks and toks[-1] == ("p", ";"):
        toks.pop()
    if any(t == ("p", ";") for t in toks):
        raise SqlRejected("Only a single statement is allowed.")
    if not toks or toks[0] not in (("word", "select"), ("word", "with")):
        raise SqlRejected("Only SELECT queries are allowed.")
    depth = 0
    for t in toks:
        if t == ("p", "("):
            depth += 1
            if depth > max_depth:
                raise SqlRejected("Query is too deeply nested.")
        elif t == ("p", ")"):
            depth -= 1
            if depth < 0:
                raise SqlRejected("Unbalanced parentheses.")
    if depth:
        raise SqlRejected("Unbalanced parentheses.")
    for i, t in enumerate(toks):
        if t[0] == "word" and t[1] in BANNED:
            raise SqlRejected(f"'{t[1].upper()}' is not allowed (read-only).")
        if t == ("word", "for") and _val(toks[i + 1] if i + 1 < len(toks) else None) in ("update", "share", "no", "key"):
            raise SqlRejected("Locking clauses are not allowed.")
        if t == ("word", "lock"):
            raise SqlRejected("Locking clauses are not allowed.")

    # ---- tables, aliases, CTE names
    tables, aliases, ctes, out_aliases, skip = set(), {}, set(), set(), set()      # skip = token positions that are table names / aliases
    stack = []                                           # one frame per open paren: {"from": bool, "expect": bool}
    cur = {"from": False, "expect": False}
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t == ("p", "("):
            if cur["expect"]:                              # subquery in table position
                if not (nxt and nxt in (("word", "select"), ("word", "with"))):
                    raise SqlRejected("Only subqueries are allowed in FROM.")
                cur["expect"] = False
                cur["derived"] = True
            stack.append(cur); cur = {"from": False, "expect": False}
        elif t == ("p", ")"):
            cur = stack.pop()
            if cur.pop("derived", False):                  # alias after a derived table
                j = i + 1
                if j < len(toks) and toks[j] == ("word", "as"):
                    j += 1
                if j < len(toks) and toks[j][0] in ("word", "qid") and (toks[j][0] == "qid" or toks[j][1] not in KEYWORDS | CLAUSE_END):
                    aliases[toks[j][1].lower()] = None; skip.add(j); i = j
        elif t[0] == "word" and t[1] in ("from", "join"):
            cur["expect"] = True
            cur["from"] = True if t[1] == "from" else cur["from"]
        elif t[0] == "word" and t[1] in CLAUSE_END and t[1] not in ("on", "using"):
            cur["from"] = False; cur["expect"] = False
        elif t == ("p", ",") and cur["from"] and not cur["expect"]:
            cur["expect"] = True
        elif cur["expect"] and t[0] in ("word", "qid"):
            if t[0] == "word" and t[1] in KEYWORDS:
                raise SqlRejected("Unexpected keyword in FROM.")
            name, j = t[1].lower(), i + 1
            while j + 1 < len(toks) and toks[j] == ("p", ".") and toks[j + 1][0] in ("word", "qid"):
                name += "." + toks[j + 1][1].lower(); j += 2
            if name not in allow and name not in ctes:
                raise SqlRejected(f"Table '{name[:40]}' is not available.")
            if name in allow:
                tables.add(name)
            aliases[name] = name
            skip.update(range(i, j))
            if j < len(toks) and toks[j] == ("word", "as"):
                j += 1
            if j < len(toks) and toks[j][0] in ("word", "qid") and (toks[j][0] == "qid" or toks[j][1] not in KEYWORDS | CLAUSE_END | BANNED):
                aliases[toks[j][1].lower()] = name; skip.add(j); j += 1
            cur["expect"] = False; i = j - 1
        elif t == ("word", "as") and nxt == ("p", "(") and i >= 1 and toks[i - 1][0] == "word" and toks[i - 1][1] not in KEYWORDS:
            ctes.add(toks[i - 1][1]); aliases[toks[i - 1][1]] = None; skip.add(i - 1)
        elif t == ("word", "as") and nxt and nxt[0] in ("word", "qid"):
            out_aliases.add(nxt[1].lower())
        i += 1
    if not tables and not ctes:
        raise SqlRejected("Query must read from an approved table.")

    # ---- functions and columns
    cols_ok = set().union(*(allow[t] for t in tables)) if tables else set()
    used_cols = set()
    for i, t in enumerate(toks):
        if i in skip:
            continue
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t[0] == "op" and t[1] == "*":
            star = prev in (("word", "select"), ("word", "distinct"), ("p", ","), ("p", "."))
            if star and not (tables and tables <= star_tables):
                raise SqlRejected("SELECT * is not allowed: list the columns you need.")
            continue
        if t[0] not in ("word", "qid"):
            continue
        if t[0] == "word" and t[1] in KEYWORDS and t[1] not in FUNCS:
            continue
        if t[0] == "word" and nxt == ("p", "("):
            if t[1] not in FUNCS:
                raise SqlRejected(f"Function '{t[1][:30]}' is not allowed.")
            continue
        if t[0] == "word" and t[1] == "as" or prev == ("word", "as"):
            continue
        name = t[1].lower()
        if nxt == ("p", ".") and (name in aliases or name in allow):
            continue                                       # qualifier (table or alias)
        if prev == ("p", "."):
            q = toks[i - 2][1].lower() if i >= 2 else ""
            real = aliases.get(q, q)
            if real in allow and name not in allow[real]:
                raise SqlRejected(f"Column '{name[:40]}' is not available.")
            if real not in allow and q not in aliases:
                raise SqlRejected("Unknown table qualifier.")
            used_cols.add(name)
            continue
        if name in out_aliases or name in aliases and aliases[name] is None or name in ctes or (name in TYPE_WORDS and prev == ("word", "as")):
            continue
        if name in aliases:                                  # a table name used as a bare identifier
            raise SqlRejected(f"'{name[:40]}' is a table, not a column.")
        if name not in cols_ok:
            raise SqlRejected(f"Column '{name[:40]}' is not available.")
        used_cols.add(name)

    # ---- LIMIT: exactly one at top level, clamped; add one if missing
    top_limits = [i for i, t in enumerate(toks) if t == ("word", "limit") and _depth_at(toks, i) == 0]
    if len(top_limits) > 1:
        raise SqlRejected("Only one LIMIT is allowed.")
    if top_limits:
        i = top_limits[0]
        a = toks[i + 1] if i + 1 < len(toks) else None
        if not a or a[0] != "num" or "." in a[1]:
            raise SqlRejected("LIMIT must be a whole number.")
        lim, off, end = int(a[1]), None, i + 2
        if end < len(toks) and toks[end] == ("p", ","):
            b = toks[end + 1] if end + 1 < len(toks) else None
            if not b or b[0] != "num" or "." in b[1]:
                raise SqlRejected("LIMIT must be a whole number.")
            off, lim, end = int(a[1]), int(b[1]), end + 2
        elif end < len(toks) and toks[end] == ("word", "offset"):
            b = toks[end + 1] if end + 1 < len(toks) else None
            if not b or b[0] != "num" or "." in b[1]:
                raise SqlRejected("OFFSET must be a whole number.")
            off, end = int(b[1]), end + 2
        if end != len(toks):
            raise SqlRejected("Unexpected text after LIMIT.")
        toks = toks[:i] + [("word", "limit"), ("num", str(min(lim, max_limit)))] + ([("word", "offset"), ("num", str(off))] if off is not None else [])
    else:
        toks = toks + [("word", "limit"), ("num", str(max_limit))]
    return Checked(_render(toks), tables, used_cols)


def _depth_at(toks, idx):
    d = 0
    for t in toks[:idx]:
        d += 1 if t == ("p", "(") else -1 if t == ("p", ")") else 0
    return d


def _render(toks):
    out = []
    for k, t in enumerate(toks):
        if t[0] == "str":
            s = "'" + t[1].replace("'", "''") + "'"
        elif t[0] == "qid":
            if not WORD.fullmatch(t[1]):
                raise SqlRejected("Unsupported identifier.")
            s = t[1]
        elif t[0] == "word":
            s = t[1].upper() if t[1] in KEYWORDS or t[1] in BANNED else t[1]
        else:
            s = t[1]
        out.append(s)
    text = ""
    for k, s in enumerate(out):
        if k == 0:
            text = s
        elif s in (",", ")", ".") or out[k - 1] in ("(", "."):
            text += s
        elif s == "(" and toks[k - 1][0] == "word" and toks[k - 1][1] in FUNCS and toks[k - 1][1] not in KEYWORDS - FUNCS:
            text += s
        else:
            text += " " + s
    return text
