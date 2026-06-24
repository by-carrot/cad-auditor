"""
stage.py

Applies stage_relevance labels to each check in the findings dict based on
the user's selected prototype method. Labels distinguish findings that affect
the current prototype stage from those that only matter for injection molding.

stage_relevance values:
- "prototype": affects the current prototype method. Fix before prototyping.
- "production_only": irrelevant to the prototype. Fix before production tooling.
- "both": severe enough to affect both stages.

This module is the single source of truth for prototype method thresholds.
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


def apply_stage_labels(findings: dict, prototype_method: str) -> dict:
    """
    Return a deep copy of findings with stage_relevance added to each check,
    plus prototype context metadata at the top level.

    Parameters
    ----------
    findings : dict
        Unified findings package from aggregate.run_all_checks().
    prototype_method : str
        One of "sls", "fdm", "resin".

    Returns
    -------
    dict
        Deep copy with stage_relevance on each check and prototype metadata.
    """
    method = prototype_method.lower()
    proto_min = PROTOTYPE_WALL_MIN_MM.get(method, 0.8)

    staged = copy.deepcopy(findings)
    checks = staged["checks"]

    # Draft angle: no draft required by any prototype method.
    checks["draft_angle"]["stage_relevance"] = "production_only"

    # Wall thickness: flagged as prototype concern only if below prototype minimum.
    wt = checks["wall_thickness"]
    min_measured = wt.get("min_measured_mm")
    if min_measured is not None and min_measured < proto_min:
        wt["stage_relevance"] = "both"
    else:
        wt["stage_relevance"] = "production_only"

    # Undercuts: all three prototype methods tolerate them freely.
    checks["undercuts"]["stage_relevance"] = "production_only"

    # Rib proxy: relevant only to injection molding sink mark risk.
    checks["rib_thickness_proxy"]["stage_relevance"] = "production_only"

    # Sharp corners: prototypes handle them; injection molding concentrates stress.
    checks["sharp_corners"]["stage_relevance"] = "production_only"

    staged["prototype_method"] = method
    staged["prototype_method_label"] = PROTOTYPE_LABELS.get(method, method)
    staged["prototype_wall_min_mm"] = proto_min

    return staged