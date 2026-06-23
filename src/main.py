"""
main.py

CLI entry point for the CAD Auditor injection molding DFM checker.

Usage
-----
    python src/main.py --file path/to/part.stl --pull-direction Z

Options
-------
    --file            Path to the STL file to analyze (required)
    --pull-direction  Mold opening direction: X, Y, or Z (default: Z)
    --min-draft       Minimum draft angle in degrees (default: 1.0)
    --min-thickness   Minimum wall thickness in mm (default: 1.5)
    --max-thickness   Maximum wall thickness in mm (default: 4.0)
    --nominal-wall    Nominal wall thickness for rib analysis (default: 2.5)
    --output-dir      Output directory for reports (default: output)
    --no-interpret    Skip LLM interpretation and write geometry findings only
"""

import argparse
import sys
from pathlib import Path

from src.load_geometry import load_stl
from src.aggregate import run_all_checks
from src.interpret import interpret_findings
from src.report import write_report


def parse_args():
    parser = argparse.ArgumentParser(
        description="CAD Auditor: injection molding DFM checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the STL file to analyze.",
    )
    parser.add_argument(
        "--pull-direction",
        default="Z",
        choices=["X", "Y", "Z"],
        help="Mold opening direction. Default: Z.",
    )
    parser.add_argument(
        "--min-draft",
        type=float,
        default=1.0,
        help="Minimum draft angle in degrees. Default: 1.0.",
    )
    parser.add_argument(
        "--min-thickness",
        type=float,
        default=1.5,
        help="Minimum wall thickness in mm. Default: 1.5.",
    )
    parser.add_argument(
        "--max-thickness",
        type=float,
        default=4.0,
        help="Maximum wall thickness in mm. Default: 4.0.",
    )
    parser.add_argument(
        "--nominal-wall",
        type=float,
        default=2.5,
        help="Nominal wall thickness for rib ratio analysis. Default: 2.5.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to write report files. Default: output.",
    )
    parser.add_argument(
        "--no-interpret",
        action="store_true",
        help="Skip LLM interpretation. Write geometry findings only.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print(f"\nCAD Auditor — Injection Molding DFM Review")
    print(f"File: {args.file}")
    print(f"Pull direction: {args.pull_direction}")
    print(f"")

    print("Loading geometry...")
    mesh = load_stl(args.file, verbose=True)
    print(f"Loaded: {len(mesh.faces):,} faces, {len(mesh.vertices):,} vertices")
    print(f"")

    print("Running geometry checks...")
    findings = run_all_checks(
        mesh,
        pull_direction=args.pull_direction,
        min_draft_degrees=args.min_draft,
        min_thickness_mm=args.min_thickness,
        max_thickness_mm=args.max_thickness,
        nominal_wall_mm=args.nominal_wall,
    )
    print(f"Overall severity: {findings['overall_severity'].upper()}")
    print(f"")

    interpretation = ""
    if not args.no_interpret:
        print("Interpreting findings with Claude...")
        interpretation = interpret_findings(findings)
        print("Interpretation complete.")
        print(f"")

    print("Writing report...")
    paths = write_report(
        findings=findings,
        interpretation=interpretation,
        stl_path=args.file,
        output_dir=args.output_dir,
    )

    print(f"Report written:")
    print(f"  JSON: {paths['json_path']}")
    print(f"  Markdown: {paths['md_path']}")
    print(f"")


if __name__ == "__main__":
    main()