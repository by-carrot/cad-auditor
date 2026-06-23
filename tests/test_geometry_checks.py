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