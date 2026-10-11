"""Controlled tools for the factory test runs. Only these are ever executed; args are validated server-side.
There is deliberately no sample-data tool: agents work on the customer's own data, and test inputs are written inline."""
import ast
import operator

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


# name -> (description, JSON schema, handler)
REGISTRY = {
    "calculator": (
        "Evaluate basic arithmetic (+ - * / // % ** and parentheses). Use for any math instead of computing mentally.",
        {"type": "object", "properties": {"expression": {"type": "string", "description": "e.g. '120*49.5 + 80*49.5'"}},
         "required": ["expression"]},
        calculator),
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
    except (ValueError, TypeError) as e:  # ToolError is a ValueError
        return {"ok": False, "error": str(e)}
