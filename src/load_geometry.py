"""
load_geometry.py

Responsible for one thing: accepting a file path, validating the input,
loading the STL mesh, and returning a trimesh.Trimesh object.

Every downstream module receives its mesh from this module.
Nothing else in the pipeline should call trimesh.load() directly.
"""

import trimesh
import numpy as np
from pathlib import Path


def load_stl(file_path: str, verbose: bool = True) -> trimesh.Trimesh:
    """
    Load and validate an STL file, returning a trimesh.Trimesh object.

    Parameters
    ----------
    file_path : str
        Path to the STL file. Absolute or relative paths are both accepted.
    verbose : bool
        If True, print warnings about mesh quality issues. Default True.

    Returns
    -------
    trimesh.Trimesh
        The loaded mesh with face normals pre-computed by trimesh.

    Raises
    ------
    FileNotFoundError
        If no file exists at the given path.
    ValueError
        If the file is not an STL, or the mesh contains no faces.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"No file found at: {file_path}\n"
            "Check the path and try again."
        )

    if path.suffix.lower() != ".stl":
        raise ValueError(
            f"Expected an STL file but received: {path.suffix}\n"
            "Export your part as STL from your CAD tool and try again."
        )

    mesh = trimesh.load(str(path), force="mesh")

    if len(mesh.faces) == 0:
        raise ValueError(
            f"The file at {file_path} contains no faces.\n"
            "The STL may be corrupt or empty."
        )

    if verbose and not mesh.is_watertight:
        print(
            "Warning: this mesh is not watertight.\n"
            "The surface has holes or open edges.\n"
            "Wall thickness results may be less reliable.\n"
            "Consider repairing the mesh in your CAD tool before analysis."
        )

    return mesh


def mesh_summary(mesh: trimesh.Trimesh) -> dict:
    """
    Return a lightweight dict of basic mesh properties for display
    at the start of a CLI run. Does not perform any DFM analysis.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        A loaded mesh object returned by load_stl().

    Returns
    -------
    dict
        Face count, vertex count, bounding box dimensions, watertight
        status, and total surface area.
    """
    bounds = mesh.bounds
    dimensions = bounds[1] - bounds[0]

    return {
        "face_count": len(mesh.faces),
        "vertex_count": len(mesh.vertices),
        "is_watertight": bool(mesh.is_watertight),
        "bounding_box_mm": {
            "x": round(float(dimensions[0]), 2),
            "y": round(float(dimensions[1]), 2),
            "z": round(float(dimensions[2]), 2),
        },
        "surface_area_mm2": round(float(mesh.area), 2),
    }