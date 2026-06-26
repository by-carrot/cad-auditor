"""
thickness_check.py

Estimates wall thickness at sampled surface points using ray casting.
For each sample point on the outer surface, a ray is cast inward along
the inverse face normal. The distance to the first intersection on the
opposite wall is recorded as the local wall thickness.

This module is deterministic. No LLM is involved. The structured output
dict is consumed by aggregate.py and passed to interpret.py for natural
language explanation only after all geometry is fully computed.

Limitations
-----------
- Assumes STL units are millimeters. STL carries no unit metadata.
- Sampling introduces statistical approximation. Dense meshes may have
  localized thin regions that fall between sample points.
- Non-watertight meshes produce unreliable results: rays that exit
  through holes return no hit and are silently excluded.
"""

import numpy as np
import trimesh
from typing import Optional


DEFAULT_MIN_THICKNESS_MM = 1.5
DEFAULT_MAX_THICKNESS_MM = 4.0
DEFAULT_SAMPLE_COUNT = 500
ORIGIN_OFFSET_MM = 0.001
MIN_VALID_THICKNESS_MM = 0.05


def sample_face_indices(mesh: trimesh.Trimesh, sample_count: int) -> np.ndarray:
    """
    Return an array of face indices sampled uniformly from the mesh,
    weighted by face area so that large faces are not undersampled.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh from load_geometry.load_stl().
    sample_count : int
        Number of faces to sample.

    Returns
    -------
    np.ndarray
        Integer array of sampled face indices, shape (n_sample,).
    """
    n_faces = len(mesh.faces)
    actual_count = min(sample_count, n_faces)

    face_areas = mesh.area_faces
    total_area = face_areas.sum()

    if total_area < 1e-10:
        return np.arange(actual_count)

    probabilities = face_areas / total_area
    return np.random.choice(n_faces, size=actual_count, replace=False, p=probabilities)


def compute_face_centroids(mesh: trimesh.Trimesh, face_indices: np.ndarray) -> np.ndarray:
    """
    Compute the centroid of each specified face as the mean of its
    three vertex positions.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh.
    face_indices : np.ndarray
        Integer array of face indices to compute centroids for.

    Returns
    -------
    np.ndarray
        Array of shape (n, 3) with centroid xyz coordinates.
    """
    vertex_positions = mesh.vertices[mesh.faces[face_indices]]
    return vertex_positions.mean(axis=1)


def cast_thickness_rays(
    mesh: trimesh.Trimesh,
    origins: np.ndarray,
    directions: np.ndarray,
    return_ray_indices: bool = False,
) -> np.ndarray:
    """
    Cast rays inward from surface sample points and return thickness
    measurements for rays that successfully hit the opposite wall.

    Rays that miss (exit through holes in a non-watertight mesh) are
    excluded from the returned array rather than treated as zero.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh against which rays are cast.
    origins : np.ndarray
        Ray origin points of shape (n, 3), offset slightly inward from
        the surface to avoid self-intersection.
    directions : np.ndarray
        Inward unit direction vectors of shape (n, 3).
    return_ray_indices : bool
        If True, return a tuple (distances, ray_indices) instead of just distances.

    Returns
    -------
    np.ndarray
        1D array of valid thickness measurements in millimeters.
        Length may be less than n if some rays missed.
    """
    locations, index_ray, _ = mesh.ray.intersects_location(
        ray_origins=origins,
        ray_directions=directions,
        multiple_hits=False,
    )

    if len(locations) == 0:
        empty = np.array([], dtype=float)
        if return_ray_indices:
            return empty, np.array([], dtype=int)
        return empty

    hit_origins = origins[index_ray]
    raw_distances = np.linalg.norm(locations - hit_origins, axis=1)
    valid_mask = raw_distances >= MIN_VALID_THICKNESS_MM

    if return_ray_indices:
        return raw_distances[valid_mask], index_ray[valid_mask]
    return raw_distances[valid_mask]


def check_thickness(
    mesh: trimesh.Trimesh,
    min_thickness_mm: float = DEFAULT_MIN_THICKNESS_MM,
    max_thickness_mm: float = DEFAULT_MAX_THICKNESS_MM,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    random_seed: Optional[int] = 42,
) -> dict:
    """
    Sample the mesh surface, cast inward rays to measure wall thickness,
    and return structured findings flagging regions that are too thin or
    too thick for standard injection molding.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh from load_geometry.load_stl().
    min_thickness_mm : float
        Minimum acceptable wall thickness in millimeters. Default 1.5mm.
    max_thickness_mm : float
        Maximum acceptable wall thickness in millimeters. Default 4.0mm.
    sample_count : int
        Number of surface points to sample. Default 500.
    random_seed : int or None
        Seed for reproducibility. Set None to disable. Default 42.

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
            "category": "wall_thickness",
            "severity": "inconclusive",
            "n_samples_attempted": len(face_indices),
            "n_samples_measured": 0,
            "min_measured_mm": None,
            "max_measured_mm": None,
            "mean_measured_mm": None,
            "pct_too_thin": None,
            "pct_too_thick": None,
            "threshold_min_mm": min_thickness_mm,
            "threshold_max_mm": max_thickness_mm,
            "thin_face_indices":     [],
            "thin_face_thicknesses": [],
            "thick_face_indices":    [],
            "thick_face_thicknesses": [],
            "description": (
                "No valid thickness measurements were obtained. "
                "The mesh may not be watertight. "
                "Repair the mesh in your CAD tool and rerun."
            ),
        }

    too_thin = thicknesses < min_thickness_mm
    too_thick = thicknesses > max_thickness_mm
    pct_thin = float(too_thin.sum()) / n_measured
    pct_thick = float(too_thick.sum()) / n_measured

    thin_face_indices    = valid_face_idx[too_thin].tolist()
    thin_face_thicknesses = [round(float(t), 3) for t in thicknesses[too_thin]]
    thick_face_indices   = valid_face_idx[too_thick].tolist()
    thick_face_thicknesses = [round(float(t), 3) for t in thicknesses[too_thick]]

    if pct_thin > 0.10:
        severity = "high"
        description = (
            f"{pct_thin:.1%} of sampled regions fall below {min_thickness_mm}mm. "
            f"Thin walls risk incomplete fill and structural weakness. "
            f"Minimum measured thickness: {float(thicknesses.min()):.2f}mm."
        )
    elif pct_thick > 0.20:
        severity = "medium"
        description = (
            f"{pct_thick:.1%} of sampled regions exceed {max_thickness_mm}mm. "
            f"Thick sections cool unevenly and risk sink marks or voids. "
            f"Maximum measured thickness: {float(thicknesses.max()):.2f}mm."
        )
    elif pct_thin > 0.0 or pct_thick > 0.0:
        severity = "low"
        description = (
            f"Isolated thickness violations detected. "
            f"{pct_thin:.1%} too thin, {pct_thick:.1%} too thick. "
            f"Review specific regions before sending to tooling."
        )
    else:
        severity = "pass"
        description = (
            f"All {n_measured} sampled regions fall within the acceptable "
            f"thickness range of {min_thickness_mm}mm to {max_thickness_mm}mm."
        )

    return {
        "category": "wall_thickness",
        "severity": severity,
        "n_samples_attempted": len(face_indices),
        "n_samples_measured": n_measured,
        "min_measured_mm": round(float(thicknesses.min()), 3),
        "max_measured_mm": round(float(thicknesses.max()), 3),
        "mean_measured_mm": round(float(thicknesses.mean()), 3),
        "pct_too_thin": round(pct_thin, 4),
        "pct_too_thick": round(pct_thick, 4),
        "threshold_min_mm": min_thickness_mm,
        "threshold_max_mm": max_thickness_mm,
        "thin_face_indices":     thin_face_indices,
        "thin_face_thicknesses": thin_face_thicknesses,
        "thick_face_indices":    thick_face_indices,
        "thick_face_thicknesses": thick_face_thicknesses,
        "description": description,
    }