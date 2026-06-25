"""
stage.py

Applies stage_relevance labels and production method severity overrides to
each check in the findings dict.

Two separate concerns are handled here:

1. Stage relevance: distinguishes findings that matter for the current
   prototype method (prototype stage) from those that only matter for
   the chosen production method (production stage).

2. Production method overrides: when the production method is resin casting,
   certain check severities are overridden because the geometry pipeline runs
   against injection molding thresholds. Draft violations and undercuts are
   not structural issues in resin casting; wall thickness threshold drops from
   1.5mm to 0.5mm.

Both outputs (stage_relevance and effective_severity) are added to each check
and passed through to the interpret layer and frontend.

Sources for resin casting thresholds:
- WayKen: Vacuum Casting Design Guide (waykenrm.com)
- SyBridge Technologies: Critical Design Guidelines for Urethane Casting
- FacFox: Design Tips for Urethane Casting (facfox.com)
"""

import copy

PROTOTYPE_WALL_MIN_MM = {
    "sls": 0.8,
    "fdm": 1.0,
    "resin": 0.5,
}

PROTOTYPE_LABELS = {
    "sls": "SLS nylon printing",
    "fdm": "FDM printing",
    "resin": "resin (SLA) printing",
}

PRODUCTION_LABELS = {
    "injection_molding": "Injection molding",
    "resin_casting": "Resin casting (urethane)",
}

# Wall thickness minimum for each production method in mm.
# The geometry pipeline runs against injection molding defaults (1.5mm min).
# For resin casting, readings above 0.5mm are acceptable.
PRODUCTION_WALL_MIN_MM = {
    "injection_molding": 1.5,
    "resin_casting": 0.5,
}

# For resin casting, severity overrides per check.
# These do not change the raw geometry finding; they add an effective_severity
# that reflects what actually matters for the chosen production method.
_RESIN_CASTING_OVERRIDES = {
    "draft_angle": {
        # Draft not mechanically required; 3-5 degrees advisory for mold life.
        "high": "low",
        "medium": "low",
        "low": "pass",
        "pass": "pass",
        "inconclusive": "inconclusive",
    },
    "undercuts": {
        # Silicone stretches over undercuts. No tooling impact.
        "high": "pass",
        "medium": "pass",
        "low": "pass",
        "pass": "pass",
        "inconclusive": "inconclusive",
    },
    # wall_thickness, rib_thickness_proxy, sharp_corners: handled separately
    # because wall_thickness depends on min_measured_mm vs resin threshold.
}


def _apply_production_overrides(checks: dict, production_method: str) -> dict:
    """
    Apply production method severity overrides to each check.

    Adds effective_severity to each check. For injection molding, effective
    severity equals the raw geometry severity. For resin casting, certain
    checks are downgraded or suppressed.

    Parameters
    ----------
    checks : dict
        Deep-copied checks dict from findings.
    production_method : str
        One of "injection_molding", "resin_casting".

    Returns
    -------
    dict
        Checks dict with effective_severity added to each check.
    """
    if production_method == "injection_molding":
        for check in checks.values():
            check["effective_severity"] = check["severity"]
        return checks

    prod_wall_min = PRODUCTION_WALL_MIN_MM.get(production_method, 1.5)

    for check_name, check in checks.items():
        raw = check["severity"].lower()

        if check_name in _RESIN_CASTING_OVERRIDES:
            override_map = _RESIN_CASTING_OVERRIDES[check_name]
            check["effective_severity"] = override_map.get(raw, raw)

        elif check_name == "wall_thickness":
            min_measured = check.get("min_measured_mm")
            if min_measured is None:
                check["effective_severity"] = raw
            elif min_measured < prod_wall_min:
                # Below resin casting threshold: keep the severity
                check["effective_severity"] = raw
            else:
                # Above resin casting minimum but flagged by IM threshold.
                # Downgrade: was flagged only because IM needs 1.5mm.
                if raw == "high":
                    check["effective_severity"] = "low"
                elif raw == "medium":
                    check["effective_severity"] = "pass"
                else:
                    check["effective_severity"] = raw

        else:
            # rib_thickness_proxy, sharp_corners: keep raw severity
            check["effective_severity"] = raw

    return checks


def _compute_overall_effective_severity(checks: dict) -> str:
    """
    Compute overall severity from effective_severity fields after overrides.

    Parameters
    ----------
    checks : dict
        Checks dict with effective_severity on each check.

    Returns
    -------
    str
        The most severe effective_severity across all checks.
    """
    severity_order = {"high": 0, "medium": 1, "low": 2, "pass": 3, "inconclusive": 4}
    worst = "pass"
    for check in checks.values():
        sev = check.get("effective_severity", check.get("severity", "pass"))
        if severity_order.get(sev, 99) < severity_order.get(worst, 99):
            worst = sev
    return worst


def apply_stage_labels(
    findings: dict,
    prototype_method: str,
    production_method: str = "injection_molding",
    material_min_wall_mm: float = 1.5,
) -> dict:
    """
    Return a deep copy of findings with stage_relevance, effective_severity,
    and production method metadata added to each check.

    Parameters
    ----------
    findings : dict
        Unified findings package from aggregate.run_all_checks().
    prototype_method : str
        One of "sls", "fdm", "resin".
    production_method : str
        One of "injection_molding", "resin_casting". Default: injection_molding.

    Returns
    -------
    dict
        Deep copy with stage_relevance, effective_severity per check, overall
        effective severity, and prototype and production metadata at top level.
    """
    method = prototype_method.lower()
    prod = production_method.lower()
    proto_min = PROTOTYPE_WALL_MIN_MM.get(method, 0.8)
    prod_wall_min = (
        material_min_wall_mm
        if prod == "injection_molding"
        else PRODUCTION_WALL_MIN_MM.get(prod, 0.5)
    )

    staged = copy.deepcopy(findings)
    checks = staged["checks"]

    # Apply stage relevance (prototype vs production) ─────────────────────────

    # Draft: not required by any prototype method; only matters for production.
    checks["draft_angle"]["stage_relevance"] = "production_only"

    # Wall thickness: depends on measured minimum vs prototype threshold.
    wt = checks["wall_thickness"]
    min_measured = wt.get("min_measured_mm")
    if min_measured is not None and min_measured < proto_min:
        wt["stage_relevance"] = "both"
    else:
        wt["stage_relevance"] = "production_only"

    # Undercuts: all prototype methods tolerate them freely.
    checks["undercuts"]["stage_relevance"] = "production_only"

    # Rib proxy: relevant only to production quality.
    checks["rib_thickness_proxy"]["stage_relevance"] = "production_only"

    # Sharp corners: prototype methods handle them; production concentrates stress.
    checks["sharp_corners"]["stage_relevance"] = "production_only"

    # Apply production method severity overrides ───────────────────────────────
    checks = _apply_production_overrides(checks, prod)
    staged["checks"] = checks

    # Recompute overall severity based on effective severities
    staged["overall_effective_severity"] = _compute_overall_effective_severity(checks)

    # Metadata ────────────────────────────────────────────────────────────────
    staged["prototype_method"] = method
    staged["prototype_method_label"] = PROTOTYPE_LABELS.get(method, method)
    staged["prototype_wall_min_mm"] = proto_min
    staged["production_method"] = prod
    staged["production_method_label"] = PRODUCTION_LABELS.get(prod, prod)
    staged["production_wall_min_mm"] = prod_wall_min

    return staged
