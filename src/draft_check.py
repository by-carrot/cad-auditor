"""
draft_check.py

Computes the draft angle for every face in the mesh relative to the
specified pull direction and flags faces below the minimum threshold.

Draft angle is the angle between a face and the pull direction.
A perfectly vertical face has zero draft and cannot be released from
a two-part mold without damage. A face with one degree of draft
can release cleanly under standard ejection conditions.

The model never touches these calculations. All geometry is computed
deterministically here and passed as a structured dict to aggregate.py.
"""

import numpy as np
import trimesh
from typing import Union


PULL_DIRECTION_MAP = {
    "X": np.array([1.0, 0.0, 0.0]),
    "Y": np.array([0.0, 1.0, 0.0]),
    "Z": np.array([0.0, 0.0, 1.0]),
}

DEFAULT_MIN_DRAFT_DEGREES = 1.0


def resolve_pull_direction(pull_direction: Union[str, np.ndarray]) -> np.ndarray:
    """
    Convert a pull direction argument into a normalized unit vector.

    Accepts either a string shorthand ("X", "Y", "Z") or an arbitrary
    3D numpy array. String inputs map to the canonical axis vectors.
    Array inputs are normalized before use.

    Parameters
    ----------
    pull_direction : str or np.ndarray
        The mold opening direction as a string or 3D vector.

    Returns
    -------
    np.ndarray
        Unit vector of shape (3,) representing the pull direction.

    Raises
    ------
    ValueError
        If a string other than X, Y, or Z is given, or if the provided
        vector has zero magnitude and cannot be normalized.
    """
    if isinstance(pull_direction, str):
        key = pull_direction.upper()
        if key not in PULL_DIRECTION_MAP:
            raise ValueError(
                f"Pull direction must be X, Y, or Z. Received: {pull_direction}"
            )
        return PULL_DIRECTION_MAP[key].copy()

    vec = np.array(pull_direction, dtype=float)
    magnitude = np.linalg.norm(vec)
    if magnitude < 1e-10:
        raise ValueError(
            "Pull direction vector has zero magnitude and cannot be normalized."
        )
    return vec / magnitude


def compute_draft_angles(
    mesh: trimesh.Trimesh,
    pull_direction: Union[str, np.ndarray],
) -> np.ndarray:
    """
    Compute the draft angle in degrees for every face in the mesh.

    Draft angle is the angle between the face and the pull direction.
    A face whose normal is perpendicular to pull (a vertical wall) has
    zero draft. A face whose normal is parallel to pull (a horizontal
    surface) has ninety degrees of draft and is never a problem.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh returned by load_geometry.load_stl().
    pull_direction : str or np.ndarray
        Mold opening direction.

    Returns
    -------
    np.ndarray
        Array of shape (n_faces,) with draft angle in degrees per face.
        Values range from 0.0 (no draft, worst case) to 90.0 (horizontal).
    """
    pull_vec = resolve_pull_direction(pull_direction)

    # Dot product of every face normal with the pull direction.
    # Result shape: (n_faces,). Each value is cos(angle_between_normal_and_pull).
    dot_products = np.dot(mesh.face_normals, pull_vec)

    # Absolute value treats both mold halves symmetrically.
    # A downward-pointing face on the bottom of the part has the same
    # draft as the corresponding upward face on the top.
    dot_products = np.abs(dot_products)

    # Clip to [0, 1] to guard against floating point values marginally
    # outside the valid arcsin domain, which would produce NaN silently.
    dot_products = np.clip(dot_products, 0.0, 1.0)

    # arcsin maps dot product to draft angle.
    # arcsin(0) = 0 degrees  → vertical face, no draft, flagged
    # arcsin(1) = 90 degrees → horizontal face, full draft, safe
    return np.degrees(np.arcsin(dot_products))


def check_draft(
    mesh: trimesh.Trimesh,
    pull_direction: Union[str, np.ndarray] = "Z",
    min_draft_degrees: float = DEFAULT_MIN_DRAFT_DEGREES,
) -> dict:
    """
    Identify faces with insufficient draft and return structured findings.

    Only faces that are predominantly vertical (draft angle below 85 degrees)
    are evaluated. Near-horizontal faces are excluded because they are
    structurally safe regardless of minor tessellation artifacts.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh returned by load_geometry.load_stl().
    pull_direction : str or np.ndarray
        Mold opening direction. Defaults to "Z".
    min_draft_degrees : float
        Minimum acceptable draft angle. Faces below this are flagged.
        Default is 1.0 degree, appropriate for smooth non-textured surfaces.

    Returns
    -------
    dict
        Structured findings consumed by aggregate.py. Contains category,
        severity, face counts, flagged face indices, measured angles,
        threshold, and a plain English description of the finding.
    """
    draft_angles = compute_draft_angles(mesh, pull_direction)

    # Restrict evaluation to faces that are meaningfully vertical.
    # Faces above 85 degrees are near-horizontal and safe by definition.
    # Without this filter, tessellation artifacts on flat surfaces could
    # produce spurious flags if min_draft_degrees is set unusually high.
    is_side_wall = draft_angles < 85.0
    below_threshold = (draft_angles < min_draft_degrees) & is_side_wall

    flagged_indices = np.where(below_threshold)[0].tolist()
    flagged_angles = draft_angles[below_threshold]
    n_flagged = len(flagged_indices)
    n_total = len(mesh.faces)

    if n_flagged == 0:
        severity = "pass"
        description = (
            f"All side-wall faces meet the minimum draft angle of "
            f"{min_draft_degrees} degrees relative to the "
            f"{pull_direction} pull direction."
        )
    elif n_flagged / n_total > 0.15:
        severity = "high"
        description = (
            f"{n_flagged} faces ({n_flagged / n_total:.1%} of total) fall below "
            f"{min_draft_degrees} degrees of draft. Significant ejection risk. "
            f"Part likely requires redesign before tooling."
        )
    else:
        severity = "medium"
        description = (
            f"{n_flagged} faces fall below {min_draft_degrees} degrees of draft. "
            f"Review these surfaces before sending to tooling."
        )

    pull_label = (
        pull_direction
        if isinstance(pull_direction, str)
        else pull_direction.tolist()
    )

    return {
        "category": "draft_angle",
        "severity": severity,
        "face_count_flagged": n_flagged,
        "face_count_total": n_total,
        "flagged_face_indices": flagged_indices,
        "flagged_face_angles": [round(float(a), 3) for a in flagged_angles.tolist()] if n_flagged > 0 else [],
        "min_measured_degrees": (
            round(float(flagged_angles.min()), 3) if n_flagged > 0 else None
        ),
        "mean_measured_degrees": (
            round(float(flagged_angles.mean()), 3) if n_flagged > 0 else None
        ),
        "threshold_degrees": min_draft_degrees,
        "pull_direction": pull_label,
        "description": description,
    }