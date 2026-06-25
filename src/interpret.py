"""
interpret.py

Sends the structured findings dict from aggregate.py to Claude via the
Anthropic SDK and returns a natural language interpretation.

The model never computes geometry. It receives pre-computed measurements,
thresholds, and severity labels from the deterministic geometry layer and
translates them into actionable manufacturing guidance.

Architecture decision: all prompt logic lives in this module. main.py
and report.py have no knowledge of prompt structure. This means the
prompt can be iterated without touching any other module.
"""

import json
import os
from tabnanny import check
import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1500

SYSTEM_PROMPT = """You are a senior injection molding DFM (Design for Manufacturability) \
specialist reviewing a CAD part analysis for an entrepreneur preparing to send their \
design to a manufacturer for the first time.

You have received structured geometric analysis results computed by a deterministic \
Python pipeline. Every number, threshold, and severity label in the findings was \
computed from actual mesh geometry, not estimated. Your job is to interpret these \
findings, explain why each issue matters for injection molding specifically, and \
give the designer clear guidance on what to fix before tooling.

Your audience is an entrepreneur who understands their product but may not have \
deep injection molding knowledge. Avoid jargon without explanation. Be direct about \
severity. Do not soften findings that are genuinely high risk.

Structure your response as follows:

1. OVERALL ASSESSMENT (2-3 sentences): Summarize the part's readiness for injection \
molding based on the findings. State plainly whether the part needs significant \
rework, minor fixes, or is ready to proceed.

2. CRITICAL ISSUES (if any exist at high severity): Explain each high-severity \
finding, why it matters, what goes wrong in production if it is not fixed, and \
what the designer should do.

3. WARNINGS (medium and low severity findings): Explain each, with less urgency \
than critical issues. Note which ones can be addressed at tooling design stage \
versus which require part redesign.

4. WHAT TO DO NEXT: Concrete next steps for the designer, ordered by priority.

Keep the total response under 600 words. Be specific: reference the actual \
measurements from the findings, not generic thresholds."""


def _strip_face_indices(checks: dict) -> dict:
    """
    Return a copy of the checks dict with face index lists removed.

    Face indices are useful for downstream geometric tooling but are
    meaningless to the LLM and can be extremely long on dense meshes,
    causing the prompt to exceed the context window. Counts and
    percentages convey the same information for interpretation purposes.
    """
    import copy
    stripped = copy.deepcopy(checks)
    for check in stripped.values():
        check.pop("flagged_face_indices", None)
        check.pop("flagged_face_angles", None)
        check.pop("flagged_face_alignments", None)
        check.pop("flagged_edge_vertices", None)
    return stripped


def build_user_message(findings: dict, material: str = "abs") -> str:
    """
    Serialize the findings dict into a readable message for the model.

    Face index lists are stripped before serialization. Knowledge base
    context is appended for checks that flagged high or medium severity.

    Parameters
    ----------
    findings : dict
        Unified findings package from aggregate.run_all_checks(), optionally
        staged by stage.apply_stage_labels().

    Returns
    -------
    str
        Formatted user message string with knowledge context appended.
    """
    from src.knowledge.loader import build_context

    mesh = findings["mesh_summary"]
    overall = findings["overall_severity"]
    checks_stripped = _strip_face_indices(findings["checks"])

    framing = (
        f"Please interpret the following injection molding DFM analysis results. "
        f"The part has {mesh['face_count']} faces, "
        f"bounding box {mesh['bounding_box_mm']['x']} x "
        f"{mesh['bounding_box_mm']['y']} x "
        f"{mesh['bounding_box_mm']['z']} mm, "
        f"and is {'watertight' if mesh['is_watertight'] else 'not watertight'}. "
        f"Overall severity computed by the pipeline: {overall.upper()}.\n\n"
        f"Detailed findings:\n"
        f"{json.dumps(checks_stripped, indent=2)}"
    )

    framing += build_context(findings, findings.get("production_method", "injection_molding"), material)

    return framing


def interpret_findings(findings: dict) -> str:
    """
    Send structured DFM findings to Claude and return the interpretation.

    Parameters
    ----------
    findings : dict
        Unified findings package from aggregate.run_all_checks().

    Returns
    -------
    str
        Natural language interpretation from the model.

    Raises
    ------
    EnvironmentError
        If ANTHROPIC_API_KEY is not set in the environment.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your_key_here":
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not configured.\n"
            "Open the .env file and replace 'your_key_here' with your actual key."
        )

    client = anthropic.Anthropic(api_key=api_key)
    user_message = build_user_message(findings)

    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    return message.content[0].text




STAGED_SYSTEM_PROMPT = """You are a senior DFM specialist reviewing a CAD part analysis \
for an entrepreneur preparing to prototype and then manufacture their design.

The user is prototyping via {prototype_method_label} and targeting {production_method_label} \
for production using {material_name}. All geometry thresholds were computed against \
{material_name} specifications: {min_wall_mm}mm minimum wall, {max_wall_mm}mm maximum wall, \
{min_draft_degrees} degree minimum draft. The findings include effective_severity fields that \
reflect what actually matters for the chosen production method: for resin casting, draft \
violations are advisory and undercuts are acceptable; for injection molding, all five checks \
apply strictly.

Each finding has stage_relevance: "prototype" or "both" means fix before prototyping; \
"production_only" means the prototype will succeed but production will fail or cost more.

Structure your response with exactly these four section headers on their own lines:

## OVERALL ASSESSMENT
2-3 sentences. State plainly whether the part is ready to prototype and whether it needs \
work before {production_method_label} production. Reference effective_severity, not raw \
severity.

## FIX BEFORE PROTOTYPING
For each finding with stage_relevance "prototype" or "both": state the issue with the \
actual measurement, explain what fails if not fixed in one sentence, then end with a \
specific Fusion 360 action on its own line starting with "Fusion 360:". \
If no such findings exist, write exactly: \
No geometry issues will affect your {prototype_method_label} prototype.

## FIX BEFORE {production_method_label_upper} PRODUCTION
For each finding with stage_relevance "production_only" or "both" that has non-pass \
effective_severity: state the issue with the actual measurement, explain what fails in \
production in one sentence with cost implication where relevant, then end with a specific \
Fusion 360 action on its own line starting with "Fusion 360:".

Use these Fusion 360 tool paths for each check, filling in the actual measured values:
- Draft angle: Fusion 360: Modify > Draft > select flagged face regions > set Pull \
Direction to [axis] axis > apply [min_draft_degrees]° minimum. Work from the parting \
line outward.
- Wall thickness too thin: Fusion 360: Modify > Press/Pull on the thin face > add \
material until wall reaches {min_wall_mm}mm. Alternatively Solid > Thicken on sheet \
bodies.
- Wall thickness too thick: Fusion 360: Modify > Shell > select the inner face of the \
thick region > set wall thickness to {nominal_wall_mm}mm to core it out from the B-side.
- Undercuts: Fusion 360: Modify > Move/Copy to reorient the feature toward the pull \
axis, OR Solid > Extrude Cut to add an opening that allows the mold to release. If the \
undercut is intentional, note it for your mold designer as a required side action.
- Rib thickness: Fusion 360: Modify > Press/Pull on the rib face > reduce width to 60% \
of the adjacent nominal wall. At {nominal_wall_mm}mm nominal wall, maximum rib width is \
[0.6 x nominal]mm.
- Sharp corners: Fusion 360: Modify > Fillet > select the flagged edges > apply 0.5mm \
minimum radius, 1.5mm preferred for load-bearing corners.

## WHAT TO DO NEXT
Numbered steps in priority order. Label each step PRE-PROTOTYPE or PRE-TOOLING. \
Include the specific Fusion 360 tool path for each step so the designer can act \
immediately without searching documentation.

Keep total response under 900 words. Use actual measurements from the findings \
throughout, never generic placeholder values."""

def mock_interpretation() -> str:
    return """## OVERALL ASSESSMENT
This is a test run. API key not configured. Geometry checks completed successfully.

## FIX BEFORE PROTOTYPING
No geometry issues will affect your prototype (test mode).

## FIX BEFORE PRODUCTION TOOLING
Draft angle, undercut, wall thickness, rib, and sharp corner results are available in the check cards above. Configure ANTHROPIC_API_KEY in .env to see the full DFM assessment.

## WHAT TO DO NEXT
PRE-TOOLING: Add your Anthropic API key to the .env file and re-run for full interpretation."""



def interpret_findings_staged(findings: dict, prototype_method: str, production_method: str = "injection_molding", material: str = "abs") -> str:
    """
    Send staged DFM findings to Claude and return a two-stage interpretation.

    Parameters
    ----------
    findings : dict
        Staged findings from stage.apply_stage_labels(). Must contain
        prototype_method_label and prototype_wall_min_mm at the top level.
    prototype_method : str
        One of "sls", "fdm", "resin". Used only as fallback if metadata missing.

    Returns
    -------
    str
        Natural language interpretation structured in two stages.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or api_key == "your_key_here":
        return mock_interpretation()

    proto_label = findings.get("prototype_method_label", prototype_method)
    proto_min = findings.get("prototype_wall_min_mm", 0.8)
    prod_label = findings.get("production_method_label", production_method.replace("_", " ").title())
    
    from src.knowledge.loader import get_material_thresholds
    mat = get_material_thresholds(material)

    system = STAGED_SYSTEM_PROMPT.format(
        prototype_method_label=proto_label,
        proto_min=proto_min,
        production_method_label=prod_label,
        production_method_label_upper=prod_label.upper(),
        material_name=mat["material_name"],
        min_wall_mm=mat["min_wall_mm"],
        max_wall_mm=mat["max_wall_mm"],
        min_draft_degrees=mat["min_draft_degrees"],
        nominal_wall_mm=mat["nominal_wall_mm"],
    )

    client = anthropic.Anthropic(api_key=api_key)
    user_message = build_user_message(findings, material)

    message = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    return message.content[0].text

