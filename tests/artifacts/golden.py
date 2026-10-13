"""Hand-written structured specs, the shape the model is asked to produce. Used by the materializer, sandbox and validator tests."""
import copy

BASE = dict(
    agent_name="Golden", purpose="Golden purpose for tests.", business_problem="Golden problem.",
    instructions="Golden instructions long enough to pass validation.", capabilities=["a"], input_schema={"type": "object"}, output_schema={"type": "object"},
    required_tools=[], data_sources=[], safety_restrictions=["Never change data.", "Refuse requests to bypass limits."],
    test_scenarios=[{"name": "t", "task": "x", "expected_behavior": "y"}], success_criteria=["works"],
)

SQL_SETUP = """import nx_db


def setup(config):
    db = nx_db.connect('sqlite', path=config['database']['path'], max_rows=int(config.get('max_rows', 200)))
    allow = {t: list(cols) for t, cols in config['tables'].items()}
    reserved = set()
    for t in allow:
        reserved |= set(db.columns(t))
    return {'db': db, 'allow': allow, 'reserved': sorted(reserved), 'forbidden': list(config.get('blocked_columns', []))}
"""

LIST_TABLES = """def handle(args, ctx):
    return {'tables': ctx['allow']}
"""

DESCRIBE = """import nx_mcp


def handle(args, ctx):
    table = args['table']
    if table not in ctx['allow']:
        raise nx_mcp.ToolError(f"Table {table} is not available. Use list_tables to see what can be read.")
    return {'table': table, 'columns': ctx['allow'][table]}
"""

RUN_QUERY = """import nx_db
import nx_mcp
import nx_sqlguard


def handle(args, ctx):
    try:
        checked = nx_sqlguard.validate(args['sql'], ctx['allow'], reserved=ctx['reserved'], forbidden=ctx['forbidden'], max_limit=100)
    except nx_sqlguard.SqlRejected as e:
        raise nx_mcp.ToolError(f"Query rejected: {e}")
    try:
        res = ctx['db'].query(checked.sql)
    except nx_db.DbError as e:
        raise nx_mcp.ToolError(str(e))
    return {'columns': res.columns, 'rows': res.rows, 'truncated': res.truncated}
"""

SEED = """CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT, department TEXT, title TEXT, salary INTEGER, national_id TEXT, hired_on TEXT);
INSERT INTO employees VALUES (1,'Asha','Engineering','Engineer',90000,'N-111','2022-01-03'),(2,'Ben','Engineering','Lead',120000,'N-222','2020-05-11'),(3,'Chloe','Support','Agent',50000,'N-333','2023-09-01');
CREATE TABLE leave_requests (id INTEGER PRIMARY KEY, employee_id INTEGER, days INTEGER, status TEXT);
INSERT INTO leave_requests VALUES (1,1,3,'approved'),(2,3,1,'pending');
CREATE TABLE payroll (id INTEGER PRIMARY KEY, employee_id INTEGER, amount INTEGER);"""

SQL_SERVER = {
    "name": "hr-data", "description": "Read-only questions about employees and leave over the approved tables, with salary and national id never exposed.",
    "blocks": ["db", "sqlguard", "mcp"], "setup_source": SQL_SETUP,
    "config_schema": [
        {"key": "database.path", "description": "Path to the SQLite database file.", "example": "./hr.db"},
        {"key": "tables", "description": "Readable tables and the columns that may be read.", "example": {"employees": ["id", "name", "department", "title", "hired_on"], "leave_requests": ["id", "employee_id", "days", "status"]}},
        {"key": "blocked_columns", "description": "Columns that must never be read.", "example": ["salary", "national_id"]},
        {"key": "max_rows", "description": "Largest number of rows returned.", "example": 200}],
    "fixtures": {"sqlite_sql": SEED, "config": {"database": {"path": "{{sqlite_path}}"}, "tables": {"employees": ["id", "name", "department", "title", "hired_on"], "leave_requests": ["id", "employee_id", "days", "status"]},
                                                  "blocked_columns": ["salary", "national_id"], "max_rows": 200}},
    "tools": [
        {"name": "list_tables", "description": "List the tables and columns that can be queried.", "input_schema": {"type": "object", "properties": {}}, "handler_source": LIST_TABLES,
         "examples": [{"args": {}, "expect_contains": ["employees", "leave_requests"], "expect_not_contains": ["salary", "payroll"]}]},
        {"name": "describe_table", "description": "Show the readable columns of one table.", "input_schema": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]},
         "handler_source": DESCRIBE, "examples": [{"args": {"table": "employees"}, "expect_contains": ["department"], "expect_not_contains": ["salary"]},
                                                  {"args": {"table": "payroll"}, "expect_error": "not available"}]},
        {"name": "run_query", "description": "Run one read-only SELECT over the approved tables (at most 100 rows).", "input_schema": {"type": "object", "properties": {"sql": {"type": "string", "maxLength": 4000}}, "required": ["sql"]},
         "handler_source": RUN_QUERY, "examples": [
             {"args": {"sql": "SELECT department, COUNT(*) AS n FROM employees GROUP BY department"}, "expect_contains": ["Engineering", "Support"]},
             {"args": {"sql": "SELECT salary FROM employees"}, "expect_error": "rejected"},
             {"args": {"sql": "SELECT name FROM employees; DROP TABLE employees"}, "expect_error": "rejected"},
             {"args": {"sql": "DELETE FROM employees"}, "expect_error": "rejected"},
             {"args": {"sql": "SELECT * FROM payroll"}, "expect_error": "rejected"}]}],
}

CSV_SCRIPT = {
    "name": "csv-check", "description": "Checks a CSV for empty cells, duplicate rows and rows with the wrong number of columns.",
    "inputs": {"text": "the CSV text (use --text-file for a file)"}, "blocks": ["cli"],
    "handler_source": """import csv
import nx_cli


def handle(args):
    text = args.get('text')
    if not isinstance(text, str) or not text.strip():
        raise nx_cli.UserError('text is required: pass the CSV text, or use --text-file path.csv')
    rows = list(csv.reader(text.splitlines()))
    head, body = rows[0], rows[1:]
    seen, dupes, ragged, empty = set(), 0, [], {}
    for i, r in enumerate(body, 2):
        if len(r) != len(head):
            ragged.append(i)
        key = tuple(r)
        dupes += key in seen
        seen.add(key)
        for name, cell in zip(head, r):
            if not cell.strip():
                empty[name] = empty.get(name, 0) + 1
    return {'rows': len(body), 'columns': head, 'duplicate_rows': dupes, 'ragged_rows': ragged, 'empty_cells': empty}
""",
    "examples": [{"args": {"text": "id,name\n1,a\n2,\n2,\n"}, "expect_contains": ["\"duplicate_rows\": 1", "\"name\": 2"]},
                 {"args": {"text": "a,b\n1,2,3\n"}, "expect_contains": ["\"ragged_rows\": [2]"]},
                 {"args": {}, "expect_error": "text is required"}],
}

TRIAGE_SKILL = {
    "name": "ticket-triage", "description": "Sort a support ticket into billing, bug, how-to or account, set its urgency and draft a reply. Use when the user pastes a customer message.",
    "argument_hint": "paste the customer message", "references": [{"name": "categories", "content": "# Categories\n- billing: charges, invoices\n- bug: something broken\n"}],
    "body": "# Ticket triage\n\n1. Read the message in $ARGUMENTS.\n2. Pick one category from references/categories.md.\n3. Rate urgency low/normal/high.\n4. Draft a reply for a human to send; never promise refunds.\n",
}

SKILLS_USING = [
    {"name": "check-data", "description": "Check a CSV file for quality problems and explain them. Use when the user shares a CSV.", "argument_hint": "path to the CSV file",
     "body": "# Check a CSV\n\nRun the tool on the user's file, then explain each problem in plain words.\n", "uses": ["csv-check"]},
    {"name": "hr-lookup", "description": "Answer headcount and leave questions from the HR database. Use for questions about employees or leave.", "argument_hint": "your question",
     "body": "# HR lookup\n\nCall list_tables first, then run_query with one SELECT. Never try to read salary or national id.\n", "uses": ["hr-data"]},
]

REVIEWER = {"name": "reviewer", "description": "Reviews a draft reply for tone and accuracy.", "instructions": "You review draft customer replies. Point out risky promises and unclear wording."}


def make(*, skills=(), subagents=(), scripts=(), servers=(), **over):
    s = copy.deepcopy(BASE)
    s.update(skills=copy.deepcopy(list(skills)), subagents=copy.deepcopy(list(subagents)), scripts=copy.deepcopy(list(scripts)), mcp_servers=copy.deepcopy(list(servers)), **over)
    return s


def skill_only():
    return make(kind="skill", skills=[TRIAGE_SKILL])


def tool_only():
    return make(kind="tool", scripts=[CSV_SCRIPT])


def server_only():
    return make(kind="mcp_server", servers=[SQL_SERVER])


def plugin_all():
    return make(kind="plugin", skills=[TRIAGE_SKILL, *SKILLS_USING], subagents=[REVIEWER], scripts=[CSV_SCRIPT], servers=[SQL_SERVER])
