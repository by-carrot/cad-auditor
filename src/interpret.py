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
    return stripped


def build_user_message(findings: dict) -> str:
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

    framing += build_context(findings, findings.get("production_method", "injection_molding"))

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
for production. The findings include effective_severity fields that reflect what actually matters \
for the chosen production method: for resin casting, draft violations are advisory and undercuts \
are acceptable; for injection molding, all five checks apply strictly.

Each finding has stage_relevance: "prototype" or "both" means fix before prototyping; \
"production_only" means the prototype will succeed but production will fail or cost more.

Structure your response with exactly these four section headers:

## OVERALL ASSESSMENT
State plainly whether the part is ready to prototype and whether it needs work before \
{production_method_label} production. Reference effective_severity, not raw severity.

## FIX BEFORE PROTOTYPING
Findings with stage_relevance "prototype" or "both". If none: write exactly: \
No geometry issues will affect your {prototype_method_label} prototype.

## FIX BEFORE {production_method_label_upper} PRODUCTION
Findings with stage_relevance "production_only" or "both" that have non-pass effective_severity. \
Reference actual measurements. Be specific about production consequences.

## WHAT TO DO NEXT
Ordered steps labeled PRE-PROTOTYPE or PRE-PRODUCTION.

Keep under 750 words. Reference actual measurements, not generic thresholds."""


def interpret_findings_staged(findings: dict, prototype_method: str, production_method: str = "injection_molding") -> str:
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
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not configured.\n"
            "Open the .env file and replace 'your_key_here' with your actual key."
        )

    proto_label = findings.get("prototype_method_label", prototype_method)
    proto_min = findings.get("prototype_wall_min_mm", 0.8)
    prod_label = findings.get("production_method_label", production_method.replace("_", " ").title())
    
    system = STAGED_SYSTEM_PROMPT.format(
        prototype_method_label=proto_label,
        proto_min=proto_min,
        production_method_label=prod_label,
        production_method_label_upper=prod_label.upper(),
    )

    client = anthropic.Anthropic(api_key=api_key)
    user_message = build_user_message(findings)

    message = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    return message.content[0].text

