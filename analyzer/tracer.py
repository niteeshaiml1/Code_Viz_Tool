"""
tracer.py — clean step-by-step execution tracer
- Captures every user code line with variable snapshots
- Attaches print() output to the line that produced it
- Filters out all internal tracer noise
"""
import sys, subprocess, json
from dataclasses import dataclass, field

@dataclass
class TraceStep:
    step: int
    line: int
    line_text: str
    variables: dict
    event: str
    output: str = ""

_RUNNER = r'''
import sys, json, io, builtins

SOURCE = __SRC__
MAX    = __MAX__

src_lines = SOURCE.splitlines()
steps     = []
_stdout   = sys.stdout          # real stdout — JSON goes here at the end
_buf      = io.StringIO()       # captures print() output

# ── redirect print() through our buffer ──────────────────────────────────────
_real_print = builtins.print
def _patched_print(*args, **kwargs):#bucket(virtual memory)
    kwargs.setdefault("file", _buf)
    _patched_print.__wrapped__(*args, **kwargs)
_patched_print.__wrapped__ = _real_print
builtins.print = _patched_print

# ── tracer ────────────────────────────────────────────────────────────────────
def _tracer(frame, event, arg):
    if len(steps) >= MAX:
        return None
    if frame.f_code.co_filename != "<string>":
        return _tracer                          # skip internal frames
    if event not in ("line", "call", "return"):
        return _tracer

    lineno    = frame.f_lineno
    line_text = src_lines[lineno - 1].strip() if 0 < lineno <= len(src_lines) else ""

    # grab whatever was printed since the last step
    out = _buf.getvalue()
    _buf.truncate(0); _buf.seek(0)

    local_vars = {
        k: repr(v)[:120]
        for k, v in frame.f_locals.items()
        if not k.startswith("__")
    }

    steps.append({
        "step": len(steps) + 1,
        "line": lineno,
        "line_text": line_text,
        "variables": local_vars,
        "event": event,
        "output": out,
    })
    return _tracer

sys.settrace(_tracer)
try:
    exec(compile(SOURCE, "<string>", "exec"), {})
except Exception as e:
    out = _buf.getvalue()
    steps.append({
        "step": len(steps) + 1, "line": 0,
        "line_text": f"ERROR — {type(e).__name__}: {e}",
        "variables": {}, "event": "error", "output": out,
    })
finally:
    sys.settrace(None)
    builtins.print = _patched_print.__wrapped__

# write JSON to real stdout (no user print() mixed in)
_stdout.write(json.dumps(steps))
_stdout.flush()
'''

def trace_code(source: str, timeout: int = 10, max_steps: int = 300) -> list[TraceStep]:
    runner = _RUNNER.replace("__SRC__", repr(source)).replace("__MAX__", str(max_steps))
    try:
        proc = subprocess.run(
            [sys.executable, "-c", runner],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [TraceStep(1, 0, "Timed out — possible infinite loop.", {}, "error")]
    except Exception as e:
        return [TraceStep(1, 0, f"Tracer error: {e}", {}, "error")]

    raw = proc.stdout.strip()
    if not raw:
        err = (proc.stderr or "no output").strip()[:400]
        return [TraceStep(1, 0, f"Runtime error: {err}", {}, "error")]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [TraceStep(1, 0, f"Parse error: {e}\n\nraw={raw[:300]}", {}, "error")]

    # attach output to the step AFTER the line that produced it
    # (output appears in the buffer at the next line event)
    result = []
    pending_output = ""
    for r in data:
        step = TraceStep(
            step=r["step"], line=r["line"], line_text=r["line_text"],
            variables=r["variables"], event=r["event"],
            output=pending_output,
        )
        result.append(step)
        pending_output = r.get("output", "")
    return result
