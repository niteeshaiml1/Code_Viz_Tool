"""
main.py — FastAPI backend for Code Understanding Engine
Run: uvicorn main:app --reload --port 8000
"""
import os, sys, subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from analyzer.parser import parse_code
from analyzer.tracer import trace_code

app = FastAPI(title="Code Understanding Engine")

app.add_middleware(CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Models ────────────────────────────────────────────────────────────────────
class CodeIn(BaseModel):
    code: str

# ── Serve frontend ─────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse("static/index.html")

# ── /analyze ──────────────────────────────────────────────────────────────────
@app.post("/analyze")
def analyze(body: CodeIn):
    if not body.code.strip():
        raise HTTPException(400, "Code cannot be empty")
    try:
        result = parse_code(body.code)
    except ValueError as e:
        raise HTTPException(422, str(e))

    return {
        "stats": result.stats,
        "flow_nodes": [
            {"id": n.node_id, "type": n.node_type, "label": n.label,
             "line": n.line, "depth": n.depth}
            for n in result.flow_nodes
        ],
        "functions": [
            {"name": f.name, "args": f.args, "line_start": f.line_start,
             "line_end": f.line_end, "complexity": f.complexity,
             "calls": f.calls, "docstring": f.docstring, "source": f.source}
            for f in result.functions
        ],
        "dependency_map": result.dependency_map,
        "issues": result.issues,
    }

# ── /trace ─────────────────────────────────────────────────────────────────────
@app.post("/trace")
def trace(body: CodeIn):
    if not body.code.strip():
        raise HTTPException(400, "Code cannot be empty")
    steps = trace_code(body.code, max_steps=400)
    user_steps = [s for s in steps if s.line_text.strip() or s.event == "error"]
    return {
        "steps": [
            {"step": s.step, "line": s.line, "line_text": s.line_text,
             "variables": s.variables, "event": s.event, "output": s.output}
            for s in user_steps
        ],
        "total": len(user_steps),
    }

# ── /run ──────────────────────────────────────────────────────────────────────
@app.post("/run")
def run_code(body: CodeIn):
    try:
        proc = subprocess.run(
            [sys.executable, "-c", body.code],
            capture_output=True, text=True, timeout=8
        )
        return {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Timed out after 8 seconds", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}