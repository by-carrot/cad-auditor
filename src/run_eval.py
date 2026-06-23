"""
run_eval.py

Runs all cases in eval/cases.json through the pipeline and reports
whether each check result matches the expected severity label.

Usage
-----
    python -m src.run_eval

Output
------
    Prints a pass/fail table per case per check to the terminal.
    Exits with code 1 if any case fails, 0 if all pass.
    This allows the eval runner to be used in CI pipelines.
"""

import json
import sys
from pathlib import Path

from src.load_geometry import load_stl
from src.aggregate import run_all_checks


EVAL_PATH = Path("eval/cases.json")
CHECK_KEYS = [
    "draft_angle",
    "wall_thickness",
    "undercuts",
    "rib_thickness_proxy",
    "sharp_corners",
]


def load_cases() -> list:
    with open(EVAL_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_case(case: dict) -> dict:
    """
    Run a single eval case through the geometry pipeline and return
    a result dict mapping each check to pass or fail against expected.

    Parameters
    ----------
    case : dict
        A single case from eval/cases.json.

    Returns
    -------
    dict
        Per-check results with keys: check name, expected, actual, match.
    """
    mesh = load_stl(case["stl_file"], verbose=False)
    params = case["parameters"]

    findings = run_all_checks(
        mesh,
        pull_direction=case["pull_direction"],
        min_draft_degrees=params["min_draft_degrees"],
        min_thickness_mm=params["min_thickness_mm"],
        max_thickness_mm=params["max_thickness_mm"],
        nominal_wall_mm=params["nominal_wall_mm"],
    )

    results = []
    for key in CHECK_KEYS:
        expected = case["expected"][key]
        actual = findings["checks"][key]["severity"]
        results.append({
            "check": key,
            "expected": expected,
            "actual": actual,
            "match": expected == actual,
        })

    overall_expected = case["expected"]["overall"]
    overall_actual = findings["overall_severity"]
    results.append({
        "check": "overall",
        "expected": overall_expected,
        "actual": overall_actual,
        "match": overall_expected == overall_actual,
    })

    return results


def print_case_results(case: dict, results: list) -> bool:
    """
    Print formatted results for a single case and return True if all
    checks matched, False if any failed.
    """
    all_pass = all(r["match"] for r in results)
    status = "PASS" if all_pass else "FAIL"
    print(f"\n{'='*60}")
    print(f"Case: {case['case_id']}")
    print(f"File: {case['stl_file']}")
    print(f"Status: {status}")
    print(f"{'='*60}")
    print(f"{'Check':<25} {'Expected':<15} {'Actual':<15} {'Match'}")
    print(f"{'-'*60}")
    for r in results:
        match_str = "OK" if r["match"] else "FAIL"
        print(f"{r['check']:<25} {r['expected']:<15} {r['actual']:<15} {match_str}")
    return all_pass


def main():
    cases = load_cases()
    print(f"CAD Auditor Eval Runner")
    print(f"Running {len(cases)} cases from {EVAL_PATH}")

    all_passed = True
    case_summaries = []

    for case in cases:
        print(f"\nRunning {case['case_id']}...")
        results = run_case(case)
        case_passed = print_case_results(case, results)
        all_passed = all_passed and case_passed
        case_summaries.append((case["case_id"], case_passed))

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    for case_id, passed in case_summaries:
        print(f"{'PASS' if passed else 'FAIL'}  {case_id}")

    total = len(case_summaries)
    passed_count = sum(1 for _, p in case_summaries if p)
    print(f"\n{passed_count}/{total} cases passed")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()