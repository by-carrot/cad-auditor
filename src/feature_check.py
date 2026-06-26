"""
feature_check.py

Checks feature geometry for two DFM failure categories:

1. Rib thickness proxy: identifies localized regions where measured
   thickness is disproportionately high relative to adjacent regions,
   which is the geometric signature of overly thick ribs or bosses.
   Full parametric rib detection requires CAD feature data not present
   in STL. This proxy is documented as an approximation.

2. Sharp corners: identifies mesh edges where the dihedral angle between
   adjacent faces falls below a threshold, indicating internal corners
   with insufficient radius for clean mold filling and ejection.
"""

import numpy as np
import trimesh
from typing import Union, Optional

from src.draft_check import resolve_pull_direction
from src.thickness_check import (
    sample_face_indices,
    compute_face_centroids,
    cast_thickness_rays,
    DEFAULT_SAMPLE_COUNT,
    ORIGIN_OFFSET_MM,
)


DEFAULT_NOMINAL_WALL_MM = 2.5
DEFAULT_MAX_RIB_RATIO = 0.60
DEFAULT_MIN_CORNER_ANGLE_DEG = 45.0
DEFAULT_SHARP_CORNER_SAMPLE = 300


def check_rib_thickness_proxy(
    mesh: trimesh.Trimesh,
    pull_direction: Union[str, np.ndarray] = "Z",
    nominal_wall_mm: float = DEFAULT_NOMINAL_WALL_MM,
    max_rib_ratio: float = DEFAULT_MAX_RIB_RATIO,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    random_seed: Optional[int] = 42,
) -> dict:
    """
    Estimate rib thickness violations using a thickness distribution proxy.

    Because STL meshes carry no parametric feature data, true rib detection
    is not possible without a CAD kernel. This function instead identifies
    regions where local wall thickness is disproportionately large relative
    to the nominal wall, which is the geometric signature of thick ribs
    or bosses that risk causing sink marks on the opposite surface.

    A region is flagged when its measured thickness exceeds the nominal
    wall thickness multiplied by the inverse rib ratio threshold. For a
    nominal wall of 2.5mm and max_rib_ratio of 0.60, ribs thicker than
    2.5 * (1 / 0.60) = 4.17mm would be flagged as disproportionate.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh from load_geometry.load_stl().
    pull_direction : str or np.ndarray
        Used to orient sampling toward side-wall features.
    nominal_wall_mm : float
        Expected nominal wall thickness for this part in millimeters.
        Defaults to 2.5mm, typical for consumer product ABS parts.
        Users should override this with their actual design intent.
    max_rib_ratio : float
        Maximum acceptable rib-to-wall thickness ratio. Default 0.60.
    sample_count : int
        Number of surface points to sample. Default 500.
    random_seed : int or None
        Seed for reproducibility. Default 42.

    Returns
    -------
    dict
        Structured findings consumed by aggregate.py.
    """
    if random_seed is not None:
        np.random.seed(random_seed)

    face_indices = sample_face_indices(mesh, sample_count)
    centroids = compute_face_centroids(mesh, face_indices)
    inward_directions = -mesh.face_normals[face_indices]
    origins_offset = centroids + inward_directions * ORIGIN_OFFSET_MM

    thicknesses, valid_ray_idx = cast_thickness_rays(
        mesh, origins_offset, inward_directions, return_ray_indices=True
    )
    valid_face_idx = (
        face_indices[valid_ray_idx]
        if len(valid_ray_idx) > 0
        else np.array([], dtype=int)
    )
    n_measured = len(thicknesses)

    if n_measured == 0:
        return {
            "category": "rib_thickness_proxy",
            "severity": "inconclusive",
            "nominal_wall_mm": nominal_wall_mm,
            "max_rib_ratio": max_rib_ratio,
            "n_samples_measured": 0,
            "pct_exceeding_ratio": None,
            "rib_flagged_face_indices":  [],
            "rib_flagged_thicknesses":   [],
            "description": (
                "No thickness measurements obtained. "
                "Mesh may not be watertight. Rib analysis skipped."
            ),
            "methodology_note": (
                "Proxy analysis only. True rib detection requires "
                "parametric CAD feature data not present in STL format."
            ),
        }

    rib_thickness_threshold = nominal_wall_mm / max_rib_ratio
    exceeding = thicknesses > rib_thickness_threshold
    rib_flagged_face_indices  = valid_face_idx[exceeding].tolist()
    rib_flagged_thicknesses   = [round(float(t), 3) for t in thicknesses[exceeding]]
    pct_exceeding = float(exceeding.sum()) / n_measured

    if pct_exceeding > 0.15:
        severity = "high"
        description = (
            f"{pct_exceeding:.1%} of sampled regions exceed the rib thickness "
            f"threshold of {rib_thickness_threshold:.2f}mm "
            f"(nominal {nominal_wall_mm}mm wall, {max_rib_ratio:.0%} ratio). "
            f"Thick ribs or bosses are likely present and risk sink marks "
            f"on opposite surfaces."
        )
    elif pct_exceeding > 0.0:
        severity = "low"
        description = (
            f"Isolated regions ({pct_exceeding:.1%} of samples) exceed the "
            f"rib thickness threshold. Review these areas for rib or boss "
            f"geometry that may cause sink marks."
        )
    else:
        severity = "pass"
        description = (
            f"No regions detected exceeding the rib thickness threshold of "
            f"{rib_thickness_threshold:.2f}mm based on a nominal wall of "
            f"{nominal_wall_mm}mm."
        )

    return {
        "category": "rib_thickness_proxy",
        "severity": severity,
        "nominal_wall_mm": nominal_wall_mm,
        "max_rib_ratio": max_rib_ratio,
        "rib_thickness_threshold_mm": round(rib_thickness_threshold, 3),
        "n_samples_measured": n_measured,
        "pct_exceeding_ratio": round(pct_exceeding, 4),
        "rib_flagged_face_indices":  rib_flagged_face_indices,
        "rib_flagged_thicknesses":   rib_flagged_thicknesses,
        "description": description,
        "methodology_note": (
            "Proxy analysis based on thickness distribution. "
            "True rib detection requires parametric CAD feature data "
            "not present in STL format. Treat findings as indicative."
        ),
    }


def compute_edge_dihedral_angles(mesh: trimesh.Trimesh) -> np.ndarray:
    """
    Compute the interior dihedral angle in degrees for every edge in the mesh.

    The dihedral angle is the angle between the two faces sharing an edge,
    measured on the interior of the solid. A flat surface has a dihedral
    angle of 180 degrees. A sharp right-angle concave corner has 90 degrees.
    A fully acute concave corner has less than 90 degrees.

    Only edges shared by exactly two faces (manifold edges) are included.
    Boundary edges on non-watertight meshes are excluded.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh from load_geometry.load_stl().

    Returns
    -------
    np.ndarray
        1D array of dihedral angles in degrees for manifold edges.
        May be shorter than the total edge count if the mesh has boundaries.
    """
    face_adjacency = mesh.face_adjacency
    if len(face_adjacency) == 0:
        return np.array([], dtype=float)

    normals_a = mesh.face_normals[face_adjacency[:, 0]]
    normals_b = mesh.face_normals[face_adjacency[:, 1]]

    dot_products = np.einsum("ij,ij->i", normals_a, normals_b)
    dot_products = np.clip(dot_products, -1.0, 1.0)

    angles_between_normals = np.degrees(np.arccos(dot_products))
    dihedral_angles = 180.0 - angles_between_normals

    return dihedral_angles


def check_sharp_corners(
    mesh: trimesh.Trimesh,
    min_corner_angle_deg: float = DEFAULT_MIN_CORNER_ANGLE_DEG,
) -> dict:
    """
    Identify edges where the interior dihedral angle falls below the minimum
    acceptable corner angle, indicating sharp concave corners that risk
    flow hesitation, weld lines, and tooling stress concentration.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh from load_geometry.load_stl().
    min_corner_angle_deg : float
        Minimum acceptable interior dihedral angle in degrees. Edges below
        this threshold are flagged. Default 45 degrees. A 90-degree internal
        corner (right angle) has a dihedral angle of 90 degrees and is
        borderline acceptable. Anything below 45 degrees is a clear concern.

    Returns
    -------
    dict
        Structured findings consumed by aggregate.py.
    """
    dihedral_angles = compute_edge_dihedral_angles(mesh)
    n_edges = len(dihedral_angles)

    if n_edges == 0:
        return {
            "category": "sharp_corners",
            "severity": "inconclusive",
            "n_edges_analyzed": 0,
            "n_edges_flagged": 0,
            "flagged_edge_vertices": [],
            "min_measured_angle_deg": None,
            "threshold_deg": min_corner_angle_deg,
            "description": (
                "No manifold edges found. Mesh may not be watertight. "
                "Sharp corner analysis skipped."
            ),
        }

    sharp_mask = dihedral_angles < min_corner_angle_deg
    n_flagged = int(sharp_mask.sum())
    flagged_angles = dihedral_angles[sharp_mask]

    flagged_edge_vertices = []
    if n_flagged > 0:
        edge_pairs = mesh.face_adjacency_edges[sharp_mask]
        verts = mesh.vertices[edge_pairs]
        flagged_edge_vertices = [
            [v[0].tolist(), v[1].tolist()] for v in verts
        ]

    if n_flagged == 0:
        severity = "pass"
        description = (
            f"No edges detected below the {min_corner_angle_deg} degree "
            f"dihedral angle threshold across {n_edges} analyzed edges. "
            f"Corner geometry appears acceptable for injection molding."
        )
    elif n_flagged / n_edges > 0.05:
        severity = "high"
        description = (
            f"{n_flagged} edges ({n_flagged / n_edges:.1%} of total) fall "
            f"below the {min_corner_angle_deg} degree dihedral angle threshold. "
            f"Sharp corners cause flow hesitation, weld lines, and accelerated "
            f"tooling wear. Add fillets of at least 0.5mm to flagged regions."
        )
    else:
        severity = "medium"
        description = (
            f"{n_flagged} edges fall below the {min_corner_angle_deg} degree "
            f"threshold. Review these corners and consider adding fillets "
            f"before sending to tooling."
        )

    return {
        "category": "sharp_corners",
        "severity": severity,
        "n_edges_analyzed": n_edges,
        "n_edges_flagged": n_flagged,
        "flagged_edge_vertices": flagged_edge_vertices,
        "min_measured_angle_deg": (
            round(float(flagged_angles.min()), 2) if n_flagged > 0 else None
        ),
        "mean_flagged_angle_deg": (
            round(float(flagged_angles.mean()), 2) if n_flagged > 0 else None
        ),
        "threshold_deg": min_corner_angle_deg,
        "description": description,
    }