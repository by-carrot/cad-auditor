"""
test_geometry_checks.py

Tests for the geometry loading module. Every test is self-contained:
no external STL files required. Meshes are constructed programmatically
using trimesh primitives so tests run without any file system dependencies.
"""

import pytest
import trimesh
import numpy as np
from pathlib import Path

from src.load_geometry import load_stl, mesh_summary


def make_box_mesh() -> trimesh.Trimesh:
    """
    Return a simple watertight box mesh for testing.
    trimesh.creation.box() produces a valid closed solid with 12 faces.
    Bounding box is 10 x 10 x 10 in trimesh's default units.
    """
    return trimesh.creation.box(extents=[10.0, 10.0, 10.0])


class TestLoadStl:
    """Tests for the load_stl() function."""

    def test_raises_file_not_found_for_missing_path(self, tmp_path):
        """
        load_stl() must raise FileNotFoundError when the path does not exist.
        tmp_path is a pytest fixture that creates a temporary directory
        unique to each test run. We construct a path inside it that
        deliberately points to nothing.
        """
        missing = tmp_path / "nonexistent.stl"
        with pytest.raises(FileNotFoundError):
            load_stl(str(missing))

    def test_raises_value_error_for_wrong_extension(self, tmp_path):
        """
        load_stl() must raise ValueError when given a file that is not
        an STL, even if the file exists. We create a real file with a
        .obj extension to confirm the extension check fires correctly.
        """
        wrong_ext = tmp_path / "model.obj"
        wrong_ext.write_text("placeholder content")
        with pytest.raises(ValueError):
            load_stl(str(wrong_ext))

    def test_returns_trimesh_for_valid_stl(self, tmp_path):
        """
        load_stl() must return a trimesh.Trimesh object when given a
        valid STL file. We export a programmatically created box to a
        temporary STL file and load it back through our function.
        """
        stl_path = tmp_path / "box.stl"
        box = make_box_mesh()
        box.export(str(stl_path))

        mesh = load_stl(str(stl_path))
        assert isinstance(mesh, trimesh.Trimesh)

    def test_loaded_mesh_has_faces(self, tmp_path):
        """
        A successfully loaded mesh must contain at least one face.
        This guards against empty STL files slipping through.
        """
        stl_path = tmp_path / "box.stl"
        make_box_mesh().export(str(stl_path))

        mesh = load_stl(str(stl_path))
        assert len(mesh.faces) > 0

    def test_loaded_mesh_has_face_normals(self, tmp_path):
        """
        trimesh computes face normals automatically on load.
        face_normals must have the same number of rows as faces,
        and each normal must be a unit vector (magnitude of 1.0).
        """
        stl_path = tmp_path / "box.stl"
        make_box_mesh().export(str(stl_path))

        mesh = load_stl(str(stl_path))
        assert mesh.face_normals.shape == (len(mesh.faces), 3)

        magnitudes = np.linalg.norm(mesh.face_normals, axis=1)
        np.testing.assert_allclose(magnitudes, 1.0, atol=1e-6)


class TestMeshSummary:
    """Tests for the mesh_summary() function."""

    def test_summary_contains_required_keys(self, tmp_path):
        """
        mesh_summary() must return a dict with all five expected keys.
        Any missing key would break downstream JSON serialization.
        """
        stl_path = tmp_path / "box.stl"
        make_box_mesh().export(str(stl_path))
        mesh = load_stl(str(stl_path))

        summary = mesh_summary(mesh)
        required_keys = {
            "face_count",
            "vertex_count",
            "is_watertight",
            "bounding_box_mm",
            "surface_area_mm2",
        }
        assert required_keys.issubset(summary.keys())

    def test_summary_face_count_matches_mesh(self, tmp_path):
        """
        The face_count in the summary must match len(mesh.faces) exactly.
        """
        stl_path = tmp_path / "box.stl"
        box = make_box_mesh()
        box.export(str(stl_path))
        mesh = load_stl(str(stl_path))

        summary = mesh_summary(mesh)
        assert summary["face_count"] == len(mesh.faces)

    def test_summary_watertight_is_python_bool(self, tmp_path):
        """
        is_watertight must be a native Python bool, not a numpy bool.
        numpy booleans do not serialize to JSON cleanly, which would
        break report.py silently. This test enforces the coercion.
        """
        stl_path = tmp_path / "box.stl"
        make_box_mesh().export(str(stl_path))
        mesh = load_stl(str(stl_path))

        summary = mesh_summary(mesh)
        assert type(summary["is_watertight"]) is bool

    def test_box_bounding_box_dimensions(self, tmp_path):
        """
        A 10x10x10 box must report bounding box dimensions of 10.0 on
        all three axes. This confirms the bounds subtraction is correct.
        """
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        mesh = load_stl(str(stl_path))

        summary = mesh_summary(mesh)
        bb = summary["bounding_box_mm"]
        assert bb["x"] == pytest.approx(10.0, abs=0.01)
        assert bb["y"] == pytest.approx(10.0, abs=0.01)
        assert bb["z"] == pytest.approx(10.0, abs=0.01)


from src.draft_check import resolve_pull_direction, compute_draft_angles, check_draft


class TestResolvePullDirection:
    """Tests for the pull direction resolution helper."""

    def test_z_string_returns_unit_z_vector(self):
        vec = resolve_pull_direction("Z")
        np.testing.assert_allclose(vec, [0.0, 0.0, 1.0])

    def test_x_string_returns_unit_x_vector(self):
        vec = resolve_pull_direction("X")
        np.testing.assert_allclose(vec, [1.0, 0.0, 0.0])

    def test_lowercase_string_is_accepted(self):
        vec = resolve_pull_direction("z")
        np.testing.assert_allclose(vec, [0.0, 0.0, 1.0])

    def test_invalid_string_raises_value_error(self):
        with pytest.raises(ValueError):
            resolve_pull_direction("W")

    def test_arbitrary_vector_is_normalized(self):
        vec = resolve_pull_direction(np.array([0.0, 0.0, 5.0]))
        np.testing.assert_allclose(np.linalg.norm(vec), 1.0, atol=1e-6)

    def test_zero_vector_raises_value_error(self):
        with pytest.raises(ValueError):
            resolve_pull_direction(np.array([0.0, 0.0, 0.0]))


class TestComputeDraftAngles:
    """
    Tests for draft angle computation against meshes with known geometry.

    A box mesh with Z pull has:
      top and bottom faces: normals parallel to Z, draft angle = 90 degrees
      four side walls: normals perpendicular to Z, draft angle = 0 degrees
    """

    def test_horizontal_faces_have_ninety_degree_draft(self, tmp_path):
        """
        The top and bottom faces of an axis-aligned box have normals
        pointing directly along Z. Their draft angle relative to Z pull
        should be exactly 90 degrees.
        """
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        angles = compute_draft_angles(mesh, "Z")

        # Top and bottom faces: normals are [0,0,1] and [0,0,-1]
        # dot with Z = 1.0, arcsin(1.0) = 90 degrees
        top_bottom = angles[angles > 89.0]
        assert len(top_bottom) > 0

    def test_vertical_faces_have_near_zero_draft(self, tmp_path):
        """
        The four side walls of an axis-aligned box have normals perpendicular
        to Z. Their draft angle relative to Z pull should be near 0 degrees.
        """
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        angles = compute_draft_angles(mesh, "Z")

        side_walls = angles[angles < 1.0]
        assert len(side_walls) > 0

    def test_output_shape_matches_face_count(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        mesh.export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        angles = compute_draft_angles(mesh, "Z")
        assert angles.shape == (len(mesh.faces),)

    def test_all_values_between_zero_and_ninety(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        angles = compute_draft_angles(mesh, "Z")
        assert float(angles.min()) >= 0.0
        assert float(angles.max()) <= 90.0


class TestCheckDraft:
    """Tests for the top-level check_draft() function."""

    def test_box_with_z_pull_flags_side_walls(self, tmp_path):
        """
        A box aligned to the Z axis has side walls with zero draft.
        check_draft() must flag these with severity high or medium,
        not pass.
        """
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_draft(mesh, pull_direction="Z", min_draft_degrees=1.0)
        assert result["severity"] in ("medium", "high")
        assert result["face_count_flagged"] > 0

    def test_result_contains_required_keys(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_draft(mesh)
        required = {
            "category", "severity", "face_count_flagged", "face_count_total",
            "flagged_face_indices", "min_measured_degrees", "mean_measured_degrees",
            "threshold_degrees", "pull_direction", "description",
        }
        assert required.issubset(result.keys())

    def test_category_is_draft_angle(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_draft(mesh)
        assert result["category"] == "draft_angle"

    def test_flagged_indices_are_python_list(self, tmp_path):
        """
        flagged_face_indices must be a plain Python list, not a numpy array.
        numpy arrays do not serialize to JSON, which would break report.py.
        """
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_draft(mesh)
        assert isinstance(result["flagged_face_indices"], list)


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
        return np.array([], dtype=float)

    hit_origins = origins[index_ray]
    raw_distances = np.linalg.norm(locations - hit_origins, axis=1)

    valid_mask = raw_distances >= MIN_VALID_THICKNESS_MM
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
    thicknesses = cast_thickness_rays(mesh, origins_offset, inward_directions)

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
        "description": description,
    }

from src.thickness_check import (
    sample_face_indices,
    compute_face_centroids,
    check_thickness,
)


class TestSampleFaceIndices:
    """Tests for the area-weighted face sampler."""

    def test_returns_correct_count_when_mesh_larger_than_sample(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        indices = sample_face_indices(mesh, sample_count=6)
        assert len(indices) == 6

    def test_returns_all_faces_when_sample_exceeds_mesh(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        indices = sample_face_indices(mesh, sample_count=999)
        assert len(indices) == len(mesh.faces)

    def test_indices_are_valid_face_indices(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        indices = sample_face_indices(mesh, sample_count=6)
        assert indices.max() < len(mesh.faces)
        assert indices.min() >= 0

    def test_no_duplicate_indices(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        indices = sample_face_indices(mesh, sample_count=6)
        assert len(indices) == len(set(indices.tolist()))


class TestComputeFaceCentroids:
    """Tests for face centroid computation."""

    def test_centroid_shape_matches_input_count(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        indices = np.arange(6)
        centroids = compute_face_centroids(mesh, indices)
        assert centroids.shape == (6, 3)

    def test_centroid_lies_within_bounding_box(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        indices = np.arange(len(mesh.faces))
        centroids = compute_face_centroids(mesh, indices)
        bounds = mesh.bounds
        assert float(centroids.min()) >= bounds[0].min() - 0.01
        assert float(centroids.max()) <= bounds[1].max() + 0.01


class TestCheckThickness:
    """Tests for the top-level check_thickness() function."""

    def test_result_contains_required_keys(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_thickness(mesh)
        required = {
            "category", "severity", "n_samples_attempted", "n_samples_measured",
            "min_measured_mm", "max_measured_mm", "mean_measured_mm",
            "pct_too_thin", "pct_too_thick", "threshold_min_mm",
            "threshold_max_mm", "description",
        }
        assert required.issubset(result.keys())

    def test_category_is_wall_thickness(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_thickness(mesh)
        assert result["category"] == "wall_thickness"

    def test_thick_box_flagged_above_max_threshold(self, tmp_path):
        """
        A 10x10x10mm solid box has walls roughly 10mm thick when measured
        by ray casting through the full solid. This far exceeds the 4mm
        maximum threshold and should produce a non-pass severity.
        """
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_thickness(mesh, max_thickness_mm=4.0, random_seed=42)
        assert result["severity"] != "pass"

    def test_result_is_reproducible_with_fixed_seed(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result_a = check_thickness(mesh, random_seed=42)
        result_b = check_thickness(mesh, random_seed=42)
        assert result_a["mean_measured_mm"] == result_b["mean_measured_mm"]

    def test_measured_count_does_not_exceed_attempted(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_thickness(mesh, sample_count=50)
        assert result["n_samples_measured"] <= result["n_samples_attempted"]


from src.undercut_check import compute_pull_alignment, check_undercuts


class TestComputePullAlignment:
    """Tests for face normal alignment with pull direction."""

    def test_output_shape_matches_face_count(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        alignment = compute_pull_alignment(mesh, "Z")
        assert alignment.shape == (len(mesh.faces),)

    def test_values_within_minus_one_to_one(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        alignment = compute_pull_alignment(mesh, "Z")
        assert float(alignment.min()) >= -1.0 - 1e-6
        assert float(alignment.max()) <= 1.0 + 1e-6

    def test_top_face_has_positive_alignment_with_z(self):
        """
        The top face of a box has normal pointing up (0,0,1).
        Its dot product with Z pull (0,0,1) should be close to 1.0.
        """
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        alignment = compute_pull_alignment(mesh, "Z")
        assert float(alignment.max()) > 0.99

    def test_bottom_face_has_negative_alignment_with_z(self):
        """
        The bottom face of a box has normal pointing down (0,0,-1).
        Its dot product with Z pull should be close to -1.0.
        """
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        alignment = compute_pull_alignment(mesh, "Z")
        assert float(alignment.min()) < -0.99


class TestCheckUndercuts:
    """Tests for the top-level check_undercuts() function."""

    def test_result_contains_required_keys(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result = check_undercuts(mesh)
        required = {
            "category", "severity", "face_count_flagged", "face_count_total",
            "flagged_face_indices", "most_opposing_alignment",
            "opposing_threshold", "pull_direction", "description",
            "methodology_note",
        }
        assert required.issubset(result.keys())

    def test_category_is_undercuts(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result = check_undercuts(mesh)
        assert result["category"] == "undercuts"

    def test_box_bottom_face_flagged_with_z_pull(self):
        """
        A box pulled along Z has a bottom face with normal (0,0,-1).
        Its alignment with Z pull is -1.0, well below the threshold.
        The check must flag at least one face.
        """
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result = check_undercuts(mesh, pull_direction="Z")
        assert result["face_count_flagged"] > 0

    def test_flagged_indices_are_python_list(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result = check_undercuts(mesh)
        assert isinstance(result["flagged_face_indices"], list)

    def test_tight_threshold_reduces_flagged_count(self):
        """
        A threshold of -0.99 (nearly fully opposing) flags fewer faces
        than the default threshold of -0.259. This confirms the threshold
        parameter is actually applied in the computation.
        """
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result_default = check_undercuts(mesh, opposing_threshold=-0.259)
        result_tight = check_undercuts(mesh, opposing_threshold=-0.99)
        assert result_tight["face_count_flagged"] <= result_default["face_count_flagged"]

    def test_methodology_note_present_and_nonempty(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result = check_undercuts(mesh)
        assert isinstance(result["methodology_note"], str)
        assert len(result["methodology_note"]) > 0

from src.feature_check import (
    check_rib_thickness_proxy,
    compute_edge_dihedral_angles,
    check_sharp_corners,
)


class TestCheckRibThicknessProxy:
    """Tests for the rib thickness proxy check."""

    def test_result_contains_required_keys(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_rib_thickness_proxy(mesh)
        required = {
            "category", "severity", "nominal_wall_mm", "max_rib_ratio",
            "n_samples_measured", "pct_exceeding_ratio",
            "description", "methodology_note",
        }
        assert required.issubset(result.keys())

    def test_category_is_rib_thickness_proxy(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_rib_thickness_proxy(mesh)
        assert result["category"] == "rib_thickness_proxy"

    def test_methodology_note_present(self, tmp_path):
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_rib_thickness_proxy(mesh)
        assert len(result["methodology_note"]) > 0

    def test_thick_box_exceeds_rib_threshold(self, tmp_path):
        """
        A 10x10x10mm solid box measured with a 2.5mm nominal wall
        will have thickness measurements around 10mm, far exceeding
        the rib threshold of 2.5 / 0.60 = 4.17mm.
        """
        stl_path = tmp_path / "box.stl"
        trimesh.creation.box(extents=[10.0, 10.0, 10.0]).export(str(stl_path))
        from src.load_geometry import load_stl
        mesh = load_stl(str(stl_path))

        result = check_rib_thickness_proxy(
            mesh, nominal_wall_mm=2.5, max_rib_ratio=0.60, random_seed=42
        )
        assert result["severity"] != "pass"


class TestComputeEdgeDihedralAngles:
    """Tests for dihedral angle computation."""

    def test_returns_array_for_valid_mesh(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        angles = compute_edge_dihedral_angles(mesh)
        assert isinstance(angles, np.ndarray)
        assert len(angles) > 0

    def test_box_has_ninety_degree_corners(self):
        """
        A box has right angle corners. The interior dihedral angle at
        each edge should be 90 degrees.
        """
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        angles = compute_edge_dihedral_angles(mesh)
        assert any(abs(a - 90.0) < 1.0 for a in angles)

    def test_angles_within_zero_to_180(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        angles = compute_edge_dihedral_angles(mesh)
        assert float(angles.min()) >= 0.0 - 1e-6
        assert float(angles.max()) <= 180.0 + 1e-6


class TestCheckSharpCorners:
    """Tests for the sharp corner check."""

    def test_result_contains_required_keys(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result = check_sharp_corners(mesh)
        required = {
            "category", "severity", "n_edges_analyzed", "n_edges_flagged",
            "min_measured_angle_deg", "threshold_deg", "description",
        }
        assert required.issubset(result.keys())

    def test_category_is_sharp_corners(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result = check_sharp_corners(mesh)
        assert result["category"] == "sharp_corners"

    def test_box_passes_at_default_threshold(self):
        """
        A box has 90 degree corners. The default threshold is 45 degrees.
        A box should pass because all corners are above the threshold.
        """
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result = check_sharp_corners(mesh, min_corner_angle_deg=45.0)
        assert result["severity"] == "pass"

    def test_box_flagged_at_raised_threshold(self):
        """
        Setting the threshold above 90 degrees causes the box corners
        to be flagged, confirming the threshold parameter is applied.
        """
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result = check_sharp_corners(mesh, min_corner_angle_deg=120.0)
        assert result["n_edges_flagged"] > 0

    def test_flagged_count_increases_with_threshold(self):
        mesh = trimesh.creation.box(extents=[10.0, 10.0, 10.0])
        result_low = check_sharp_corners(mesh, min_corner_angle_deg=45.0)
        result_high = check_sharp_corners(mesh, min_corner_angle_deg=120.0)
        assert result_high["n_edges_flagged"] >= result_low["n_edges_flagged"]

