"""JSON-in / JSON-out command line wrapper for tool scripts (vetted block). Exit codes: 0 ok, 1 internal error, 2 bad input."""
import json
import sys


MAX_TEXT = 5_000_000


class UserError(Exception):
    """Expected problem with the input; the message is shown to the caller."""


def _emit(stdout, obj):
    stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def main(handler, argv=None, stdin=None, stdout=None, selftest=None):
    argv, stdin, stdout = sys.argv[1:] if argv is None else argv, stdin or sys.stdin, stdout or sys.stdout
    if "--selftest" in argv:
        results = selftest() if selftest else []
        failed = [name for name, ok in results if not ok]
        _emit(stdout, {"ok": not failed, "tests": len(results), "failed": failed})
        return 0 if not failed else 1
    try:
        text = None
        if "--text-file" in argv:                                       # the wrapper (not the handler) reads the user's file; it arrives as args["text"]
            with open(argv[argv.index("--text-file") + 1], encoding="utf-8", errors="replace") as f:
                text = f.read(MAX_TEXT + 1)
            if len(text) > MAX_TEXT:
                raise UserError(f"The file is larger than {MAX_TEXT // 1_000_000} MB.")
        if "--json" in argv:
            raw = argv[argv.index("--json") + 1]
        elif "--file" in argv:
            with open(argv[argv.index("--file") + 1], encoding="utf-8") as f:
                raw = f.read()
        elif text is not None:
            raw = "{}"
        else:
            raw = stdin.read()
        args = json.loads(raw) if raw.strip() else {}
        if not isinstance(args, dict):
            raise UserError("Input must be a JSON object.")
        if text is not None:
            args["text"] = text
        _emit(stdout, {"ok": True, "result": handler(args)})
        return 0
    except UserError as e:
        _emit(stdout, {"ok": False, "error": str(e)})
        return 2
    except (ValueError, IndexError, OSError):
        _emit(stdout, {"ok": False, "error": "Input must be valid JSON (stdin, --json '{...}' or --file path)."})
        return 2
    except Exception as e:                      # noqa: BLE001
        print(f"[tool] {type(e).__name__}: {e}", file=sys.stderr)
        _emit(stdout, {"ok": False, "error": "Internal error."})
        return 1


def run(handler, selftest=None):
    sys.exit(main(handler, selftest=selftest))
