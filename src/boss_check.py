"""
boss_check.py

Detects potential boss-like features by clustering thick-wall face samples
that are already flagged by the wall thickness check. A boss is characterized
by a small-footprint cylindrical protrusion whose wall thickness exceeds 60 to
70 percent of the nominal wall, creating a local mass concentration that causes
sink marks on the opposite cosmetic surface.

This check is a post-processor: it does not re-run ray casting but instead
clusters the thick_face_indices already produced by thickness_check.py. The
result is additive to the findings dict and does not affect the five core checks
or overall_severity.

Sources:
- Protolabs: Design Better Screw Bosses on Molded Parts
- Malloy, Plastic Part Design for Injection Molding, Hanser, 2nd ed. 2010
"""

import numpy as np
import trimesh


MAX_BOSS_DIAMETER_MM   = 30.0
MIN_BOSS_CLUSTER_FACES = 2
SINK_RISK_RATIO        = 0.70
WARNING_RATIO          = 0.60


def _compute_face_centroids_for_indices(mesh: trimesh.Trimesh,
                                        face_indices: np.ndarray) -> np.ndarray:
    verts = mesh.vertices[mesh.faces[face_indices]]
    return verts.mean(axis=1)


def _simple_cluster(centroids: np.ndarray,
                    radius: float) -> list[list[int]]:
    """
    Greedy single-linkage clustering. O(n²) but n is at most 500 sample
    points from the wall thickness check so performance is not a concern.
    """
    n       = len(centroids)
    visited = np.zeros(n, dtype=bool)
    clusters: list[list[int]] = []

    for i in range(n):
        if visited[i]:
            continue
        cluster = [i]
        visited[i] = True
        for j in range(n):
            if not visited[j]:
                if np.linalg.norm(centroids[i] - centroids[j]) < radius:
                    cluster.append(j)
                    visited[j] = True
        clusters.append(cluster)

    return clusters


def detect_bosses(
    mesh: trimesh.Trimesh,
    findings: dict,
    nominal_wall_mm: float = 2.5,
) -> dict:
    """
    Identify boss candidates from the thick face indices in findings.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        The loaded part mesh.
    findings : dict
        Full findings dict from aggregate.run_all_checks(). Must include
        wall_thickness with thick_face_indices and thick_face_thicknesses.
    nominal_wall_mm : float
        Nominal wall thickness used to compute rib/boss ratios.

    Returns
    -------
    dict
        A check result dict compatible with findings["checks"] structure.
    """
    wt = findings.get("checks", {}).get("wall_thickness", {})
    thick_indices     = wt.get("thick_face_indices", [])
    thick_thicknesses = wt.get("thick_face_thicknesses", [])

    if not thick_indices or len(thick_indices) < MIN_BOSS_CLUSTER_FACES:
        return {
            "category":          "boss_detection",
            "severity":          "inconclusive",
            "stage_relevance":   "production_only",
            "n_bosses_detected": 0,
            "worst_wall_ratio":  None,
            "boss_candidates":   [],
            "boss_face_indices": [],
            "nominal_wall_mm":   nominal_wall_mm,
            "description": (
                "Insufficient thick-wall samples to identify boss features. "
                "If the part has cylindrical bosses, review wall thickness manually."
            ),
            "methodology_note": "Boss detection requires thick_face_indices from wall thickness check.",
        }

    face_indices  = np.array(thick_indices, dtype=int)
    thicknesses   = np.array(thick_thicknesses, dtype=float)
    centroids     = _compute_face_centroids_for_indices(mesh, face_indices)

    clusters = _simple_cluster(centroids, MAX_BOSS_DIAMETER_MM / 2)

    boss_candidates: list[dict] = []
    boss_face_indices: list[int] = []

    for cluster_idx_list in clusters:
        if len(cluster_idx_list) < MIN_BOSS_CLUSTER_FACES:
            continue

        cluster_centroids   = centroids[cluster_idx_list]
        cluster_thicknesses = thicknesses[cluster_idx_list]

        extent   = cluster_centroids.max(axis=0) - cluster_centroids.min(axis=0)
        diameter = float(np.sqrt(extent[0] ** 2 + extent[1] ** 2))

        if diameter > MAX_BOSS_DIAMETER_MM:
            continue

        avg_thickness = float(cluster_thicknesses.mean())
        wall_ratio    = avg_thickness / nominal_wall_mm if nominal_wall_mm > 0 else 0.0

        if wall_ratio < WARNING_RATIO:
            continue

        boss_candidates.append({
            "estimated_diameter_mm": round(diameter, 2),
            "avg_thickness_mm":      round(avg_thickness, 3),
            "wall_ratio":            round(wall_ratio, 3),
            "centroid":              cluster_centroids.mean(axis=0).tolist(),
        })

        for i in cluster_idx_list:
            boss_face_indices.append(int(face_indices[i]))

    n_bosses    = len(boss_candidates)
    worst_ratio = max((b["wall_ratio"] for b in boss_candidates), default=None)

    if n_bosses == 0:
        severity    = "pass"
        description = (
            "No boss-like features detected above the wall ratio threshold. "
            "Thick sections present but none match the small-footprint cylindrical "
            "signature of a screw boss."
        )
    elif worst_ratio is not None and worst_ratio > SINK_RISK_RATIO:
        severity    = "high"
        description = (
            f"{n_bosses} potential boss feature{'s' if n_bosses > 1 else ''} detected. "
            f"Worst wall ratio {round(worst_ratio * 100)}% of nominal {nominal_wall_mm}mm wall — "
            f"above the 60 to 70% threshold. High sink mark risk on cosmetic surfaces opposite "
            f"each boss. Core from the B-side or add gussets to reduce boss wall thickness."
        )
    else:
        severity    = "medium"
        description = (
            f"{n_bosses} potential boss feature{'s' if n_bosses > 1 else ''} detected. "
            f"Wall ratio {round(worst_ratio * 100) if worst_ratio else 0}% of nominal — "
            f"within the warning range. Monitor for sink marks on cosmetic surfaces and "
            f"consider adding peripheral gussets rather than thickening the boss wall."
        )

    return {
        "category":          "boss_detection",
        "severity":          severity,
        "stage_relevance":   "production_only",
        "n_bosses_detected": n_bosses,
        "worst_wall_ratio":  round(float(worst_ratio), 3) if worst_ratio is not None else None,
        "boss_candidates":   boss_candidates,
        "boss_face_indices": boss_face_indices,
        "nominal_wall_mm":   nominal_wall_mm,
        "description":       description,
        "methodology_note": (
            "Boss detection clusters thick-wall face samples from the wall thickness check. "
            "Small-footprint clusters with wall ratio above 60% of nominal are flagged. "
            "This is a geometric proxy — confirm with visual inspection of cylindrical features."
        ),
    }
