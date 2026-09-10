from __future__ import annotations

import json
import os

from ..models import AgentNarrative, EvidenceItem, Hypothesis, RemediationProposal


async def synthesize_narrative(
    evidence: list[EvidenceItem], hypotheses: list[Hypothesis],
    root_cause: str, proposal: RemediationProposal, model: str,
) -> tuple[AgentNarrative | None, str]:
    """Optionally explain deterministic evidence with the Agents SDK; never change findings/actions."""
    if not os.getenv("OPENAI_API_KEY"):
        return None, "deterministic-tool-runtime"
    try:
        from agents import Agent, Runner
    except ImportError:
        return None, "deterministic-tool-runtime (Agents SDK not installed)"

    payload = {
        "evidence": [item.model_dump(mode="json") for item in evidence],
        "ranked_hypotheses": [item.model_dump(mode="json") for item in hypotheses],
        "deterministic_root_cause": root_cause,
        "approved_action_boundary": proposal.model_dump(mode="json"),
    }
    agent = Agent(
        name="EvoSRE incident narrator",
        model=model,
        instructions=(
            "You are an incident commander. Explain only the supplied observability evidence. "
            "Do not change confidence values, root cause, action, thresholds or claim that an "
            "action already ran. Write a concise executive summary and up to five operator notes."
        ),
        output_type=AgentNarrative,
    )
    try:
        result = await Runner.run(agent, json.dumps(payload, ensure_ascii=False))
        output = result.final_output
        narrative = output if isinstance(output, AgentNarrative) else AgentNarrative.model_validate(output)
        return narrative, f"openai-agents/{model}"
    except Exception as exc:
        return None, f"deterministic-tool-runtime (model unavailable: {type(exc).__name__})"
