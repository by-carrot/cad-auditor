"""
app.py

FastAPI web application wrapping the existing CAD Auditor CLI pipeline.
Accepts STL uploads, runs five geometry checks, applies two-stage labeling,
calls Anthropic for interpretation, and returns JSON to the browser.

The STL file is deleted immediately after processing. No file storage,
no database, no user accounts.

Run from the project root with:
    uvicorn src.web.app:app --reload
"""

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.load_geometry import load_stl
from src.aggregate import run_all_checks
from src.stage import apply_stage_labels
from src.interpret import interpret_findings_staged

app = FastAPI(title="CAD Auditor", version="1.0.0")


@app.get("/", response_class=HTMLResponse)
def root():
    """Serve the single-page dashboard."""
    return HTMLResponse(Path("static/index.html").read_text(encoding="utf-8"))


@app.post("/analyze")
def analyze(
    file: UploadFile = File(...),
    pull_direction: str = Form(default="Z"),
    prototype_method: str = Form(default="sls"),
):
    """
    Accept an STL file, run all five DFM checks, apply two-stage labeling,
    and return structured findings with LLM interpretation.

    The uploaded file is written to a temp path and deleted in the finally
    block regardless of whether processing succeeds or fails.
    """
    if pull_direction.upper() not in ("X", "Y", "Z"):
        return JSONResponse(
            {"success": False, "error": "pull_direction must be X, Y, or Z"},
            status_code=422,
        )

    if prototype_method.lower() not in ("sls", "fdm", "resin"):
        return JSONResponse(
            {"success": False, "error": "prototype_method must be sls, fdm, or resin"},
            status_code=422,
        )

    suffix = Path(file.filename).suffix.lower() if file.filename else ".stl"
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name

        mesh = load_stl(tmp_path, verbose=False)
        findings = run_all_checks(mesh, pull_direction=pull_direction.upper())
        staged = apply_stage_labels(findings, prototype_method.lower())
        interpretation = interpret_findings_staged(staged, prototype_method.lower())

        return JSONResponse({
            "success": True,
            "file_name": file.filename,
            "findings": staged,
            "interpretation": interpretation,
        })

    except Exception as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=422,
        )

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


app.mount("/static", StaticFiles(directory="static"), name="static")