"""
aggregate.py

Orchestrates all five geometry checks and assembles their outputs into
a single structured findings dict consumed by interpret.py.

This module is the only place in the pipeline that imports from all
geometry check modules simultaneously. main.py calls run_all_checks()
and receives a complete findings package. interpret.py receives that
package and adds LLM-generated interpretation. Neither module needs to
know the internal structure of any individual check.
"""

import trimesh
from typing import Union

from src.load_geometry import load_stl, mesh_summary
from src.draft_check import check_draft
from src.thickness_check import check_thickness
from src.undercut_check import check_undercuts
from src.feature_check import check_rib_thickness_proxy, check_sharp_corners


def run_all_checks(
    mesh: trimesh.Trimesh,
    pull_direction: Union[str, list] = "Z",
    min_draft_degrees: float = 1.0,
    min_thickness_mm: float = 1.5,
    max_thickness_mm: float = 4.0,
    nominal_wall_mm: float = 2.5,
    sample_count: int = 500,
    random_seed: int = 42,
) -> dict:
    """
    Run all five DFM geometry checks and return a unified findings dict.

    Parameters are forwarded to the relevant check functions. Defaults
    reflect standard injection molding practice for commodity plastics
    such as ABS and polypropylene. Users building parts from engineering
    plastics (nylon, polycarbonate) or elastomers should override the
    thickness thresholds accordingly.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh from load_geometry.load_stl().
    pull_direction : str or list
        Mold opening direction. String shorthand (X, Y, Z) or a
        three-element list representing an arbitrary unit vector.
    min_draft_degrees : float
        Minimum acceptable draft angle. Default 1.0 degree.
    min_thickness_mm : float
        Minimum acceptable wall thickness. Default 1.5mm.
    max_thickness_mm : float
        Maximum acceptable wall thickness. Default 4.0mm.
    nominal_wall_mm : float
        Expected nominal wall thickness for rib ratio analysis.
        Default 2.5mm. Override with your actual design intent.
    sample_count : int
        Ray casting sample count for thickness and rib checks.
    random_seed : int
        Reproducibility seed for sampling operations.

    Returns
    -------
    dict
        Unified findings package with mesh summary and all five check
        results, ready for interpret.py and report.py.
    """
    summary = mesh_summary(mesh)

    draft = check_draft(
        mesh,
        pull_direction=pull_direction,
        min_draft_degrees=min_draft_degrees,
    )

    thickness = check_thickness(
        mesh,
        min_thickness_mm=min_thickness_mm,
        max_thickness_mm=max_thickness_mm,
        sample_count=sample_count,
        random_seed=random_seed,
    )

    undercuts = check_undercuts(
        mesh,
        pull_direction=pull_direction,
    )

    rib = check_rib_thickness_proxy(
        mesh,
        pull_direction=pull_direction,
        nominal_wall_mm=nominal_wall_mm,
        sample_count=sample_count,
        random_seed=random_seed,
    )

    corners = check_sharp_corners(mesh)

    severity_order = {"high": 0, "medium": 1, "low": 2, "pass": 3, "inconclusive": 4}
    checks = [draft, thickness, undercuts, rib, corners]
    overall_severity = min(
        checks,
        key=lambda c: severity_order.get(c["severity"], 99),
    )["severity"]

    return {
        "mesh_summary": summary,
        "overall_severity": overall_severity,
        "checks": {
            "draft_angle": draft,
            "wall_thickness": thickness,
            "undercuts": undercuts,
            "rib_thickness_proxy": rib,
            "sharp_corners": corners,
        },
    }