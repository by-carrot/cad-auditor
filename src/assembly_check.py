"""
assembly_check.py

Cross-part assembly checks for multi-part products. Requires all parts to
be exported in the same coordinate frame from the CAD tool.

Three checks:

1. Interference detection: samples surface points from each mesh and checks
   if any land inside the other mesh using trimesh.contains(). No boolean
   CSG required, no external dependencies beyond trimesh.

2. Mating face analysis: finds the largest opposing face pairs between two
   meshes using face normal direction and centroid proximity.

3. Tolerance stack: computes worst-case and RSS tolerance accumulation
   across all parts using standard injection molding tolerance formula.
   Called separately from compute_tolerance_stack() since it works from
   bounding box data, not mesh geometry.

Sources:
- Protolabs: Injection Molding Tolerances (protolabs.com)
- SyBridge: Critical Design Guidelines for Urethane Casting (sybridge.com)
- ASME Y14.5 tolerance stack-up methodology
"""

import numpy as np
import trimesh

N_SAMPLES = 1500

# Standard injection molding tolerance per Protolabs:
# ±0.08mm constant + ±0.002mm per mm of dimension
IM_CONSTANT_TOL_MM  = 0.08
IM_LINEAR_TOL_MM_MM = 0.002

# Standard resin casting tolerance per WayKen/SyBridge:
# ±0.15mm constant + ±0.002mm per mm
RC_CONSTANT_TOL_MM  = 0.15
RC_LINEAR_TOL_MM_MM = 0.002


def check_interference(
    mesh_a: trimesh.Trimesh,
    mesh_b: trimesh.Trimesh,
    n_samples: int = N_SAMPLES,
) -> dict:
    """
    Detect physical overlap between two meshes using surface point sampling.

    Samples n_samples points on each mesh surface and checks how many land
    inside the other mesh. A non-zero result means the parts overlap in the
    given coordinate frame.

    Parameters
    ----------
    mesh_a, mesh_b : trimesh.Trimesh
        Both meshes must be in the same coordinate frame.
    n_samples : int
        Number of surface sample points per mesh.

    Returns
    -------
    dict
        Interference result with severity and percentage overlap.
    """
    try:
        points_b, _ = trimesh.sample.sample_surface(mesh_b, n_samples)
        inside_a    = mesh_a.contains(points_b)

        points_a, _ = trimesh.sample.sample_surface(mesh_a, n_samples)
        inside_b    = mesh_b.contains(points_a)

        pct_b_in_a = float(inside_a.mean()) * 100
        pct_a_in_b = float(inside_b.mean()) * 100
        max_pct    = max(pct_b_in_a, pct_a_in_b)

        if not (inside_a.any() or inside_b.any()):
            return {
                "has_interference": False,
                "severity":         "pass",
                "pct_b_inside_a":   0.0,
                "pct_a_inside_b":   0.0,
                "description": (
                    "No interference detected. Parts do not overlap in the given "
                    "coordinate frame. Verify that both STLs were exported with the "
                    "same origin before treating this as a confirmed clearance."
                ),
            }

        severity = "high" if max_pct > 5.0 else "medium"
        return {
            "has_interference": True,
            "severity":         severity,
            "pct_b_inside_a":   round(pct_b_in_a, 2),
            "pct_a_inside_b":   round(pct_a_in_b, 2),
            "description": (
                f"Interference detected. Up to {round(max_pct, 1)}% of sampled surface "
                f"points from one part lie inside the other. Parts physically overlap "
                f"in the given coordinate frame. Modify geometry to add clearance."
            ),
        }

    except Exception as exc:
        return {
            "has_interference": None,
            "severity":         "inconclusive",
            "error":            str(exc),
            "description": (
                "Interference check could not complete. Ensure both parts are "
                "watertight and were exported in the same coordinate frame from Fusion 360."
            ),
        }


def find_mating_faces(
    mesh_a: trimesh.Trimesh,
    mesh_b: trimesh.Trimesh,
    top_n: int = 15,
    alignment_threshold: float = 0.5,
) -> dict:
    """
    Find candidate mating face pairs between two meshes.

    Identifies the largest flat faces on each mesh whose normals point
    toward the other part, then finds the closest opposing pair. Reports
    gap distance and angular deviation from perfect parallelism.

    Parameters
    ----------
    mesh_a, mesh_b : trimesh.Trimesh
        Both meshes must be in the same coordinate frame.
    top_n : int
        Number of largest candidate faces to evaluate per mesh.
    alignment_threshold : float
        Minimum dot product with direction vector to qualify as facing
        toward the other part. 0.5 = within 60 degrees.

    Returns
    -------
    dict
        Mating face result with gap and parallelism measurements.
    """
    try:
        dir_a_to_b = mesh_b.centroid - mesh_a.centroid
        norm = np.linalg.norm(dir_a_to_b)
        if norm < 1e-6:
            return {
                "found": False,
                "description": "Parts share the same centroid — cannot determine facing direction.",
            }
        dir_a_to_b /= norm

        # Faces on A facing toward B
        dots_a    = mesh_a.face_normals @ dir_a_to_b
        facing_a  = np.where(dots_a > alignment_threshold)[0]

        # Faces on B facing toward A
        dots_b    = mesh_b.face_normals @ (-dir_a_to_b)
        facing_b  = np.where(dots_b > alignment_threshold)[0]

        if len(facing_a) == 0 or len(facing_b) == 0:
            return {
                "found": False,
                "description": (
                    "No candidate mating faces found. Parts may not face each other "
                    "along the primary axis, or may require manual face selection."
                ),
            }

        # Select top_n largest faces from each candidate set
        areas_a      = mesh_a.area_faces[facing_a]
        areas_b      = mesh_b.area_faces[facing_b]
        top_a_local  = np.argsort(areas_a)[-top_n:]
        top_b_local  = np.argsort(areas_b)[-top_n:]
        top_a        = facing_a[top_a_local]
        top_b        = facing_b[top_b_local]

        centroids_a  = mesh_a.triangles_center[top_a]
        centroids_b  = mesh_b.triangles_center[top_b]

        # Find closest pair by centroid distance
        best_dist = float('inf')
        best_i, best_j = 0, 0
        for i, ca in enumerate(centroids_a):
            dists = np.linalg.norm(centroids_b - ca, axis=1)
            j     = int(np.argmin(dists))
            if dists[j] < best_dist:
                best_dist = dists[j]
                best_i, best_j = i, j

        face_a_idx = top_a[best_i]
        face_b_idx = top_b[best_j]

        normal_a   = mesh_a.face_normals[face_a_idx]
        normal_b   = mesh_b.face_normals[face_b_idx]
        area_a     = float(mesh_a.area_faces[face_a_idx])
        area_b     = float(mesh_b.area_faces[face_b_idx])

        # Parallelism: angle between normals. Mating faces should be ~180° apart.
        # We measure deviation from antiparallel (opposite normals) as the error.
        cos_angle         = float(np.clip(np.dot(normal_a, normal_b), -1.0, 1.0))
        angle_deg         = float(np.degrees(np.arccos(abs(cos_angle))))
        parallelism_error = angle_deg  # degrees deviation from flat

        gap_mm = round(best_dist, 3)

        if gap_mm < 0.5 and parallelism_error < 2.0:
            verdict = "good"
            desc = (
                f"Best mating face pair found. Gap: {gap_mm}mm, "
                f"parallelism deviation: {round(parallelism_error, 2)}°. "
                f"Faces are well-aligned. Assembly should mate cleanly within standard tolerance."
            )
        elif gap_mm < 2.0 and parallelism_error < 10.0:
            verdict = "review"
            desc = (
                f"Candidate mating face pair found. Gap: {gap_mm}mm, "
                f"parallelism deviation: {round(parallelism_error, 2)}°. "
                f"Review fit: gap may cause looseness or require adhesive. "
                f"Verify coordinate frames are correct."
            )
        else:
            verdict = "poor"
            desc = (
                f"Mating faces are poorly aligned. Gap: {gap_mm}mm, "
                f"parallelism deviation: {round(parallelism_error, 2)}°. "
                f"Either parts are not in the same coordinate frame, or mating "
                f"geometry requires redesign."
            )

        return {
            "found":               True,
            "verdict":             verdict,
            "gap_mm":              gap_mm,
            "parallelism_error_degrees": round(parallelism_error, 2),
            "area_a_mm2":          round(area_a, 2),
            "area_b_mm2":          round(area_b, 2),
            "area_ratio":          round(min(area_a, area_b) / max(area_a, area_b), 3) if max(area_a, area_b) > 0 else 0,
            "description":         desc,
        }

    except Exception as exc:
        return {
            "found":      False,
            "error":      str(exc),
            "description": "Mating face analysis failed. Ensure both meshes are valid.",
        }


def compute_tolerance_stack(
    bounding_boxes: list[dict],
    production_method: str = "injection_molding",
) -> dict:
    """
    Compute worst-case and RSS tolerance stack for a linear assembly.

    Uses standard manufacturing tolerance formulas:
    - Injection molding: ±(0.08mm + 0.002mm/mm × dimension)
    - Resin casting:     ±(0.15mm + 0.002mm/mm × dimension)

    Parameters
    ----------
    bounding_boxes : list of dict
        Each dict has keys 'x', 'y', 'z' with float dimensions in mm,
        plus 'file_name' for labeling.
    production_method : str
        One of "injection_molding" or "resin_casting".

    Returns
    -------
    dict
        Per-axis tolerance stack with worst-case and RSS results.
    """
    const_tol  = RC_CONSTANT_TOL_MM  if production_method == "resin_casting" else IM_CONSTANT_TOL_MM
    linear_tol = RC_LINEAR_TOL_MM_MM if production_method == "resin_casting" else IM_LINEAR_TOL_MM_MM

    result = {}
    for axis in ('x', 'y', 'z'):
        parts = []
        for bb in bounding_boxes:
            dim = float(bb.get(axis, 0))
            tol = const_tol + linear_tol * dim
            parts.append({
                "file_name":     bb.get("file_name", "part"),
                "dimension_mm":  round(dim, 2),
                "tolerance_mm":  round(tol, 3),
            })

        stack_mm   = round(sum(p["dimension_mm"] for p in parts), 2)
        worst_case = round(sum(p["tolerance_mm"] for p in parts), 3)
        rss        = round(float(np.sqrt(sum(p["tolerance_mm"] ** 2 for p in parts))), 3)

        result[axis] = {
            "stack_dimension_mm":     stack_mm,
            "worst_case_tolerance_mm": worst_case,
            "rss_tolerance_mm":        rss,
            "parts":                   parts,
        }

    return result