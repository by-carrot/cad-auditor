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
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.load_geometry import load_stl
from src.aggregate import run_all_checks
from src.stage import apply_stage_labels, compute_overall_effective_severity
from src.interpret import interpret_findings_staged
from src.knowledge.loader import get_material_thresholds
from src.boss_check import detect_bosses
from src.assembly_check import check_interference, find_mating_faces, compute_tolerance_stack

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
    production_method: str = Form(default="injection_molding"),
    material: str = Form(default="abs"),
    custom_min_wall:  Optional[str] = Form(default=None),
    custom_max_wall:  Optional[str] = Form(default=None),
    custom_min_draft: Optional[str] = Form(default=None),
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

    if prototype_method.lower() not in ("sls", "fdm", "resin", "resin_casting"):
        return JSONResponse(
            {"success": False, "error": "prototype_method must be sls, fdm, resin, or resin_casting"},
            status_code=422,
        )

    if production_method.lower() not in ("injection_molding", "resin_casting"):
        return JSONResponse(
            {"success": False, "error": "production_method must be injection_molding or resin_casting"},
            status_code=422,
        )

    VALID_MATERIALS = {"abs", "polypropylene", "polycarbonate", "nylon_pa6", "tpe"}
    if material.lower() not in VALID_MATERIALS:
        return JSONResponse(
            {"success": False, "error": f"material must be one of: {', '.join(sorted(VALID_MATERIALS))}"},
            status_code=422,
        )

    suffix = Path(file.filename).suffix.lower() if file.filename else ".stl"
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name

        mesh = load_stl(tmp_path, verbose=False)

        mat_thresholds = get_material_thresholds(material.lower())

        def _parse(val):
            try:
                return float(val) if val and val.strip() else None
            except ValueError:
                return None

        cmw = _parse(custom_min_wall)
        cxw = _parse(custom_max_wall)
        cmd = _parse(custom_min_draft)
        if cmw is not None: mat_thresholds["min_wall_mm"]       = cmw
        if cxw is not None: mat_thresholds["max_wall_mm"]       = cxw
        if cmd is not None: mat_thresholds["min_draft_degrees"] = cmd

        findings = run_all_checks(
            mesh,
            pull_direction=pull_direction.upper(),
            min_draft_degrees=mat_thresholds["min_draft_degrees"],
            min_thickness_mm=mat_thresholds["min_wall_mm"],
            max_thickness_mm=mat_thresholds["max_wall_mm"],
            nominal_wall_mm=mat_thresholds["nominal_wall_mm"],
        )
        staged = apply_stage_labels(
            findings,
            prototype_method.lower(),
            production_method.lower(),
            material_min_wall_mm=mat_thresholds["min_wall_mm"],
        )
        boss_result = detect_bosses(
            mesh,
            staged,
            nominal_wall_mm=mat_thresholds["nominal_wall_mm"],
        )
        staged["checks"]["boss_detection"] = boss_result
        staged["overall_effective_severity"] = compute_overall_effective_severity(staged["checks"])
        interpretation = interpret_findings_staged(
            staged,
            prototype_method.lower(),
            production_method.lower(),
            material=material.lower(),
        )

        return JSONResponse({
            "success":       True,
            "file_name":     file.filename,
            "findings":      staged,
            "interpretation": interpretation,
            "material":      material.lower(),
            "material_name": mat_thresholds["material_name"],
        })

    except Exception as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=422,
        )

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/check-assembly")
def check_assembly(
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
    production_method: str = Form(default="injection_molding"),
):
    """
    Run cross-part assembly checks on two STL files simultaneously.
    Both files must be exported in the same coordinate frame from Fusion 360.
    Both files are deleted immediately after processing.
    """
    tmp_a = tmp_b = None
    try:
        suffix_a = Path(file_a.filename).suffix.lower() if file_a.filename else ".stl"
        suffix_b = Path(file_b.filename).suffix.lower() if file_b.filename else ".stl"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix_a) as fa:
            fa.write(file_a.file.read())
            tmp_a = fa.name

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix_b) as fb:
            fb.write(file_b.file.read())
            tmp_b = fb.name

        mesh_a = load_stl(tmp_a, verbose=False)
        mesh_b = load_stl(tmp_b, verbose=False)

        interference = check_interference(mesh_a, mesh_b)
        mating       = find_mating_faces(mesh_a, mesh_b)

        def get_extents(mesh, fname):
            e = mesh.extents
            return {
                "x": round(float(e[0]), 2),
                "y": round(float(e[1]), 2),
                "z": round(float(e[2]), 2),
                "file_name": fname,
            }

        bounding_boxes = [
            get_extents(mesh_a, file_a.filename),
            get_extents(mesh_b, file_b.filename),
        ]
        tolerance = compute_tolerance_stack(bounding_boxes, production_method)

        return JSONResponse({
            "success":         True,
            "part_a":          file_a.filename,
            "part_b":          file_b.filename,
            "interference":    interference,
            "mating_faces":    mating,
            "tolerance_stack": tolerance,
        })

    except Exception as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=422,
        )
    finally:
        for p in (tmp_a, tmp_b):
            if p and os.path.exists(p):
                os.unlink(p)


app.mount("/static", StaticFiles(directory="static"), name="static")
