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


def build_user_message(findings: dict) -> str:
    """
    Serialize the findings dict into a readable message for the model.

    The full findings dict is included so the model has access to every
    computed measurement. A brief framing sentence precedes the JSON
    to orient the model toward its interpretation task.

    Parameters
    ----------
    findings : dict
        Unified findings package from aggregate.run_all_checks().

    Returns
    -------
    str
        Formatted user message string.
    """
    mesh = findings["mesh_summary"]
    overall = findings["overall_severity"]

    framing = (
        f"Please interpret the following injection molding DFM analysis results. "
        f"The part has {mesh['face_count']} faces, "
        f"bounding box {mesh['bounding_box_mm']['x']} x "
        f"{mesh['bounding_box_mm']['y']} x "
        f"{mesh['bounding_box_mm']['z']} mm, "
        f"and is {'watertight' if mesh['is_watertight'] else 'not watertight'}. "
        f"Overall severity computed by the pipeline: {overall.upper()}.\n\n"
        f"Detailed findings:\n"
        f"{json.dumps(findings['checks'], indent=2)}"
    )

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