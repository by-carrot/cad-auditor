"""
loader.py

Loads the DFM knowledge base from JSON files and builds a context string
to inject into the interpretation prompt.

Knowledge is split into three categories:

1. Check-specific rules: included only when the corresponding check flagged
   non-passing severity, keeping the prompt focused.

2. Supplementary rules (injection molding): always included for injection
   molding: bosses, weld lines, ejector pins, defects, gate types, runner
   systems, venting, surface finish standards, secondary operations.

3. Resin casting rules: included instead of supplementary IM rules when the
   production method is resin_casting. Covers the full resin casting process,
   common defects, pour gate design, and transition planning.

The collectibles context is always included for both production methods.
"""

import json
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"

_dfm_rules       = None
_materials       = None
_collectibles    = None
_resin_casting   = None

_CHECK_KEYS = {
    "draft_angle",
    "wall_thickness",
    "undercuts",
    "rib_thickness_proxy",
    "sharp_corners",
}

_IM_SUPPLEMENTARY_KEYS = [
    "bosses",
    "weld_lines",
    "ejector_pins",
    "common_defects",
    "gate_types",
    "runner_systems",
    "venting",
    "surface_finish_standards",
    "secondary_operations",
]


def _load():
    global _dfm_rules, _materials, _collectibles, _resin_casting
    if _dfm_rules is not None:
        return
    with open(_DATA_DIR / "dfm_rules.json",           encoding="utf-8") as f:
        _dfm_rules = json.load(f)
    with open(_DATA_DIR / "materials.json",            encoding="utf-8") as f:
        _materials = json.load(f)
    with open(_DATA_DIR / "collectibles.json",         encoding="utf-8") as f:
        _collectibles = json.load(f)
    with open(_DATA_DIR / "resin_casting_rules.json",  encoding="utf-8") as f:
        _resin_casting = json.load(f)


def _format_im_rule_block(key: str, rule: dict) -> str:
    header = key.upper().replace("_", " ")
    lines = [f"--- {header} ---"]

    if "why_it_matters" in rule:
        lines.append(f"Why it matters: {rule['why_it_matters']}")
    if "failure_modes" in rule:
        lines.append("Failure modes:")
        for fm in rule["failure_modes"]:
            lines.append(f"  - {fm}")
    if "cost_of_ignoring" in rule:
        lines.append(f"Cost of ignoring: {rule['cost_of_ignoring']}")
    if "fixes" in rule:
        lines.append("How to fix:")
        for fix in rule["fixes"]:
            lines.append(f"  - {fix}")
    if "collectible_specific" in rule:
        lines.append(f"For collectible form objects: {rule['collectible_specific']}")
    if key == "undercuts" and "tooling_options" in rule:
        lines.append("Tooling options if redesign is not possible:")
        for opt_name, opt in rule["tooling_options"].items():
            if opt_name == "redesign_to_eliminate":
                continue
            lines.append(
                f"  - {opt_name.replace('_', ' ').title()}: "
                f"{opt['description']} Cost: {opt['cost_usd']}."
            )
    if key == "ejector_pins" and "design_guidance" in rule:
        lines.append("Ejector pin design guidance:")
        for g in rule["design_guidance"]:
            lines.append(f"  - {g}")
        if "text_and_logo_guidance" in rule:
            lines.append("Text and logo on molded parts:")
            for r in rule["text_and_logo_guidance"].get("rules", []):
                lines.append(f"  - {r}")
    if key == "common_defects":
        for defect_name, defect in rule.items():
            if defect_name.startswith("_") or not isinstance(defect, dict):
                continue
            lines.append(
                f"{defect_name.replace('_', ' ').title()}: "
                f"Cause: {defect.get('cause', '')} "
                f"Prevention: {defect.get('prevention', '')}"
            )
    if key == "bosses" and "design_rules" in rule:
        dr = rule["design_rules"]
        lines.append(
            f"Boss design rules: outer wall max {dr.get('recommended_ratio_range', '60-70%')} "
            f"of adjacent nominal wall. Min draft {dr.get('draft_min_degrees', 1.0)} degrees. "
            f"Min base fillet {dr.get('fillet_at_base_min_mm', 0.5)}mm."
        )
    if key == "weld_lines" and "where_weld_lines_form" in rule:
        lines.append("Where weld lines form:")
        for loc in rule["where_weld_lines_form"]:
            lines.append(f"  - {loc}")
    if key == "gate_types":
        if "gate_selection_rules" in rule:
            lines.append("Gate selection rules:")
            for r in rule["gate_selection_rules"]:
                lines.append(f"  - {r}")
        if "cost_of_wrong_gate" in rule:
            lines.append(f"Cost of wrong gate: {rule['cost_of_wrong_gate']}")
    if key == "runner_systems" and "decision_framework" in rule:
        lines.append(f"Decision framework: {rule['decision_framework']}")
    if key == "venting":
        if "where_traps_occur" in rule:
            lines.append("Where air traps occur:")
            for loc in rule["where_traps_occur"]:
                lines.append(f"  - {loc}")
        if "design_guidance_for_part_designers" in rule:
            lines.append("Design guidance:")
            for g in rule["design_guidance_for_part_designers"]:
                lines.append(f"  - {g}")
    if key == "surface_finish_standards" and "decision_framework" in rule:
        lines.append(f"Decision framework: {rule['decision_framework']}")
    if key == "secondary_operations":
        if "planning_guidance" in rule:
            lines.append(f"Planning: {rule['planning_guidance']}")
        if "collectible_specific" in rule:
            lines.append(f"For collectibles: {rule['collectible_specific']}")

    return "\n".join(lines)


def _format_resin_casting_context(findings: dict) -> str:
    rc = _resin_casting
    checks = findings.get("checks", {})
    sections = []

    overview = rc.get("process_overview", {})
    lines = ["--- RESIN CASTING: PROCESS OVERVIEW ---"]
    if "description" in overview:
        lines.append(overview["description"])
    if "mold_life" in overview:
        lines.append(f"Mold life: {overview['mold_life']}")
    if "tolerances" in overview:
        t = overview["tolerances"]
        lines.append(
            f"Tolerances: {t.get('standard', '')} standard, "
            f"{t.get('precision', '')} precision. "
            f"Shrinkage: {t.get('shrinkage_allowance', '')}."
        )
    cost = overview.get("cost_comparison_vs_injection_molding", {})
    if cost:
        lines.append(
            f"Cost: silicone mold {cost.get('tooling_cost_resin_casting', '')} "
            f"vs IM {cost.get('tooling_cost_injection_molding', '')}. "
            f"Per-part: {cost.get('per_part_cost_resin_casting', '')}. "
            f"Break-even: {cost.get('break_even_point', '')}."
        )
    sections.append("\n".join(lines))

    for check_name, check in checks.items():
        raw_sev = check.get("severity", "pass")
        effective_sev = check.get("effective_severity", raw_sev)
        rc_rule = rc.get(check_name)
        if not rc_rule:
            continue
        lines = [f"--- {check_name.upper().replace('_', ' ')} (RESIN CASTING) ---"]
        lines.append(
            f"Raw geometry severity: {raw_sev.upper()}. "
            f"Effective severity for resin casting: {effective_sev.upper()}."
        )
        if "relevance_for_resin_casting" in rc_rule:
            lines.append(f"Relevance: {rc_rule['relevance_for_resin_casting']}")
        if "severity_in_context" in rc_rule:
            lines.append(f"Context: {rc_rule['severity_in_context']}")
        if "action" in rc_rule:
            lines.append(f"Action: {rc_rule['action']}")
        if "fixes" in rc_rule:
            lines.append("Fixes:")
            for fix in rc_rule["fixes"]:
                lines.append(f"  - {fix}")
        sections.append("\n".join(lines))

    defects = rc.get("common_defects", {})
    if defects:
        lines = ["--- RESIN CASTING: COMMON DEFECTS ---"]
        for defect_name, defect in defects.items():
            if not isinstance(defect, dict):
                continue
            desc = defect.get("description", "")
            causes = defect.get("causes", [])
            prev = defect.get("prevention_by_design", defect.get("prevention", ""))
            lines.append(f"{defect_name.replace('_', ' ').title()}: {desc}")
            if isinstance(causes, list) and causes:
                lines.append(f"  Causes: {'; '.join(str(c) for c in causes[:2])}")
            if isinstance(prev, list) and prev:
                lines.append(f"  Prevention by design: {'; '.join(str(p) for p in prev[:2])}")
            elif isinstance(prev, str) and prev:
                lines.append(f"  Prevention: {prev}")
        sections.append("\n".join(lines))

    gate = rc.get("pour_gate_and_venting", {})
    if gate:
        lines = ["--- RESIN CASTING: POUR GATE AND VENTING ---"]
        if "description" in gate:
            lines.append(gate["description"])
        for r in gate.get("rules", []):
            lines.append(f"  - {r}")
        if "collectible_specific" in gate:
            lines.append(f"For collectibles: {gate['collectible_specific']}")
        sections.append("\n".join(lines))

    transition = rc.get("transition_to_injection_molding", {})
    if transition:
        lines = ["--- TRANSITION TO INJECTION MOLDING (FUTURE PLANNING) ---"]
        if "description" in transition:
            lines.append(transition["description"])
        for d in transition.get("design_decisions_that_ease_transition", []):
            lines.append(f"  - {d}")
        sections.append("\n".join(lines))

    return (
        "\n\nResin casting domain knowledge "
        "(sources: WayKen, SyBridge Technologies, Formlabs, GD Prototyping, FacFox):\n\n"
        + "\n\n".join(sections)
    )


def _build_im_context(findings: dict) -> str:
    checks = findings.get("checks", {})
    sections = []

    for check_name, check in checks.items():
        if check.get("severity") in ("pass", "inconclusive"):
            continue
        rule = _dfm_rules.get(check_name)
        if rule:
            sections.append(_format_im_rule_block(check_name, rule))

    for key in _IM_SUPPLEMENTARY_KEYS:
        rule = _dfm_rules.get(key)
        if rule:
            sections.append(_format_im_rule_block(key, rule))

    if not sections:
        return ""

    return (
        "\n\nDomain knowledge relevant to these findings "
        "(sourced from Protolabs, Fictiv, ZetarMold, and Malloy "
        "Plastic Part Design for Injection Molding, Hanser 2010):\n\n"
        + "\n\n".join(sections)
    )


def build_context(findings: dict, production_method: str = "injection_molding") -> str:
    """
    Build a knowledge context string based on production method.

    Parameters
    ----------
    findings : dict
        Staged findings from stage.apply_stage_labels().
    production_method : str
        One of "injection_molding" or "resin_casting".

    Returns
    -------
    str
        Formatted knowledge context string with collectibles context appended.
    """
    _load()

    prod = production_method.lower()
    context = (
        _format_resin_casting_context(findings)
        if prod == "resin_casting"
        else _build_im_context(findings)
    )

    c = _collectibles
    coll_lines = ["--- COLLECTIBLE FORM OBJECT CONTEXT ---"]
    coll_lines.append(f"Parting line: {c['parting_line_strategy']['description']}")
    for rule in c["parting_line_strategy"]["rules"]:
        coll_lines.append(f"  - {rule}")
    coll_lines.append(f"Gate location: {c['gate_location']['description']}")
    for rule in c["gate_location"]["rules"]:
        coll_lines.append(f"  - {rule}")
    coll_lines.append(f"Fine detail limits: {c['fine_detail_limits']['description']}")
    for rule in c["fine_detail_limits"]["rules"]:
        coll_lines.append(f"  - {rule}")
    coll_lines.append(f"Surface finish: {c['surface_finish_and_texture']['description']}")
    for rule in c["surface_finish_and_texture"]["rules"]:
        coll_lines.append(f"  - {rule}")

    return context + "\n\n" + "\n".join(coll_lines)
