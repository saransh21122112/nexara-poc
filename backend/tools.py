"""Two controlled tools. Only these are ever executed; args are validated server-side."""
import ast
import json
import operator
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "sample_business_data.json"
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}
MAX_EXPR_LEN = 200
MAX_ABS = 1e15


class ToolError(ValueError):
    pass


def _eval(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ToolError("exponent too large")
        try:
            result = _BIN[type(node.op)](left, right)
        except ZeroDivisionError:
            raise ToolError("division by zero")
        if abs(result) > MAX_ABS:
            raise ToolError("result too large")
        return result
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_eval(node.operand))
    raise ToolError("unsupported expression; only numbers and + - * / // % ** ( ) are allowed")


def calculator(expression: str) -> dict:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > MAX_EXPR_LEN:
        raise ToolError("expression must be a non-empty string up to 200 chars")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError:
        raise ToolError("invalid arithmetic expression")
    return {"expression": expression, "result": _eval(tree.body)}


DATASETS = ("sales", "customers", "orders")


def lookup_business_data(dataset: str, field: str | None = None, value: str | None = None, limit: int = 10) -> dict:
    if dataset not in DATASETS:
        raise ToolError(f"dataset must be one of {list(DATASETS)}")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ToolError("limit must be an integer 1-50")
    rows = json.loads(DATA_FILE.read_text())[dataset]
    if field is not None:
        if field not in rows[0]:
            raise ToolError(f"unknown field '{field}' for {dataset}; valid: {list(rows[0])}")
        if value is None:
            raise ToolError("value is required when field is given")
        rows = [r for r in rows if str(r[field]).lower() == str(value).lower()]
    return {"dataset": dataset, "count": len(rows[:limit]), "records": rows[:limit]}


# name -> (description, JSON schema, handler)
REGISTRY = {
    "calculator": (
        "Evaluate basic arithmetic (+ - * / // % ** and parentheses). Use for any math instead of computing mentally.",
        {"type": "object", "properties": {"expression": {"type": "string", "description": "e.g. '120*49.5 + 80*49.5'"}},
         "required": ["expression"]},
        calculator),
    "lookup_business_data": (
        "Look up records in the sample business dataset. Optionally filter by field=value (exact, case-insensitive). "
        "Fields: sales(id, product, region, units, unit_price, month); customers(id, name, tier, region, email); "
        "orders(id, customer_id, status, eta, item, total). Order ids look like 'ORD-1001'.",
        {"type": "object", "properties": {
            "dataset": {"type": "string", "enum": list(DATASETS)},
            "field": {"type": "string", "description": "optional column to filter on"},
            "value": {"type": "string", "description": "value the field must equal"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
         "required": ["dataset"]},
        lookup_business_data),
}


def run_tool(name: str, args: dict) -> dict:
    """Execute a registered tool. Never raises; returns {'ok', 'output'|'error'}."""
    if name not in REGISTRY:
        return {"ok": False, "error": f"unknown tool '{name}'"}
    schema = REGISTRY[name][1]
    if not isinstance(args, dict) or set(args) - set(schema["properties"]):
        return {"ok": False, "error": "invalid arguments"}
    try:
        return {"ok": True, "output": REGISTRY[name][2](**args)}
    except (ToolError, TypeError) as e:
        return {"ok": False, "error": str(e)}
