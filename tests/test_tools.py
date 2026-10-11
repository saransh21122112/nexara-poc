from backend.tools import run_tool


def test_calculator_ok():
    assert run_tool("calculator", {"expression": "120*49.5 + 80*49.5"})["output"]["result"] == 9900.0


def test_calculator_rejects_code_and_huge_pow():
    for expr in ("__import__('os').system('ls')", "2**1000", "abs(-1)", "1/0"):
        assert not run_tool("calculator", {"expression": expr})["ok"]


def test_registry_validation():
    assert not run_tool("shell", {"cmd": "ls"})["ok"]
    assert not run_tool("calculator", {"expression": "1", "extra": 1})["ok"]
