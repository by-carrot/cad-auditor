"""
undercut_check.py

Detects faces that oppose the mold pull direction, indicating potential
undercuts that cannot be released from a simple two-part mold.

Methodology
-----------
A face is flagged as a potential undercut when its outward normal has a
component opposing the pull direction beyond a configurable threshold.
Specifically, faces where the dot product of the face normal with the
pull direction is below a negative threshold are flagged.

This is a first-order approximation. It is conservative by design:
some flagged faces may represent intentional undercuts accommodated by
side-action tooling. The output clearly communicates this limitation.

Full shadow-volume undercut detection is out of scope for this project.
That distinction is documented in the architecture decision log.
"""

import numpy as np
import trimesh
from typing import Union

from src.draft_check import resolve_pull_direction


OPPOSING_THRESHOLD = -0.259
"""
Faces with dot(normal, pull) below this value are flagged.

Derivation: cos(105 degrees) = -0.259. A face whose normal is more than
105 degrees from the pull direction (15 degrees past perpendicular into
opposing territory) is treated as a potential undercut. This threshold
excludes faces near the parting plane that are merely angled slightly
against pull due to tessellation or shallow compound geometry.
"""


def compute_pull_alignment(
    mesh: trimesh.Trimesh,
    pull_direction: Union[str, np.ndarray],
) -> np.ndarray:
    """
    Compute the dot product of every face normal with the pull direction.

    A value of 1.0 means the face points directly along pull (horizontal,
    safe). A value of 0.0 means the face is perpendicular to pull (vertical
    wall). A value below 0.0 means the face opposes pull (potential undercut).

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh from load_geometry.load_stl().
    pull_direction : str or np.ndarray
        Mold opening direction.

    Returns
    -------
    np.ndarray
        Array of shape (n_faces,) with dot products in range [-1.0, 1.0].
    """
    pull_vec = resolve_pull_direction(pull_direction)
    return np.dot(mesh.face_normals, pull_vec)


def check_undercuts(
    mesh: trimesh.Trimesh,
    pull_direction: Union[str, np.ndarray] = "Z",
    opposing_threshold: float = OPPOSING_THRESHOLD,
) -> dict:
    """
    Identify faces that oppose the pull direction beyond the threshold,
    indicating potential undercuts requiring side-action tooling or redesign.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        Loaded mesh from load_geometry.load_stl().
    pull_direction : str or np.ndarray
        Mold opening direction. Defaults to Z.
    opposing_threshold : float
        Dot product threshold below which a face is flagged. Default is
        cos(105 degrees) = -0.259, representing 15 degrees past perpendicular
        into opposing territory. More negative values flag fewer faces.

    Returns
    -------
    dict
        Structured findings consumed by aggregate.py.
    """
    alignment = compute_pull_alignment(mesh, pull_direction)
    opposing_mask = alignment < opposing_threshold
    flagged_indices = np.where(opposing_mask)[0].tolist()

    n_flagged = len(flagged_indices)
    n_total = len(mesh.faces)
    flagged_alignment = alignment[opposing_mask]

    if n_flagged == 0:
        severity = "pass"
        description = (
            f"No faces detected opposing the {pull_direction} pull direction "
            f"beyond the undercut threshold. The part appears releasable "
            f"from a simple two-part mold along this axis."
        )
    elif n_flagged / n_total > 0.05:
        severity = "high"
        description = (
            f"{n_flagged} faces ({n_flagged / n_total:.1%} of total) oppose "
            f"the {pull_direction} pull direction significantly. "
            f"These surfaces cannot release from a straight-pull two-part mold "
            f"and likely require side-action tooling or part redesign. "
            f"Note: some flagged faces may represent intentional undercuts "
            f"already accommodated in tooling design."
        )
    else:
        severity = "medium"
        description = (
            f"{n_flagged} faces oppose the {pull_direction} pull direction "
            f"beyond the detection threshold. Review these surfaces with your "
            f"mold designer to confirm whether side-action tooling is planned "
            f"or redesign is needed."
        )

    pull_label = (
        pull_direction
        if isinstance(pull_direction, str)
        else pull_direction.tolist()
    )

    return {
        "category": "undercuts",
        "severity": severity,
        "face_count_flagged": n_flagged,
        "face_count_total": n_total,
        "flagged_face_indices": flagged_indices,
        "most_opposing_alignment": (
            round(float(flagged_alignment.min()), 4) if n_flagged > 0 else None
        ),
        "opposing_threshold": opposing_threshold,
        "pull_direction": pull_label,
        "description": description,
        "methodology_note": (
            "First-order approximation based on face normal alignment. "
            "Full shadow-volume analysis is out of scope. "
            "Some flagged faces may be intentional undercuts handled by tooling."
        ),
    }