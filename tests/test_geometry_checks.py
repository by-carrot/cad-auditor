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