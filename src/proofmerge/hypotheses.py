"""Spark hypotheses: falsifiable guesses about what a patch breaks, each tied to a claim.

Spark proposes; deterministic code drops any hypothesis that cites an unknown
claim or points at code the patch did not change.
"""

import json
from importlib import resources

from pydantic import BaseModel

from proofmerge.context import GitContext
from proofmerge.intent import IntentContract
from proofmerge.meta import SPARK_MODEL, MetaClient

MAX_HYPOTHESES = 5
MAX_DIFF_CHARS = 60_000
PROMPT = (resources.files("proofmerge") / "prompts" / "hypotheses.md").read_text()


class ProposedHypothesis(BaseModel):
    """A hypothesis as Spark returns it, before validation."""

    claim_id: str
    title: str
    changed_locations: list[str]
    suspected_behavior: str
    expected_base: str
    expected_head: str
    test_intent: str
    risk_category: str
    falsification_criteria: str
    possibly_intended: bool


class ProposedHypotheses(BaseModel):
    hypotheses: list[ProposedHypothesis]


class Hypothesis(ProposedHypothesis):
    id: str


class RejectedHypothesis(BaseModel):
    hypothesis: ProposedHypothesis
    reason: str


class HypothesisSet(BaseModel):
    hypotheses: list[Hypothesis]
    rejected: list[RejectedHypothesis]  # kept as evidence of what the model got wrong


def _problem(hypothesis: ProposedHypothesis, claim_ids: set[str], changed: set[str]) -> str | None:
    if hypothesis.claim_id not in claim_ids:
        return f"cites unknown claim {hypothesis.claim_id!r}"
    if not hypothesis.changed_locations:
        return "names no changed location"
    for location in hypothesis.changed_locations:
        if location.split("::")[0] not in changed:
            return f"location {location!r} is not in a changed file"
    return None


def check_diff(context: GitContext) -> None:
    """Refuse a diff too large to send to the model."""
    if len(context.diff_text) > MAX_DIFF_CHARS:
        raise ValueError(f"diff is over {MAX_DIFF_CHARS} characters")


def generate_hypotheses(
    context: GitContext, contract: IntentContract, client: MetaClient
) -> HypothesisSet:
    """Ask Spark for hypotheses about base..head and keep the ones that pass validation."""
    if not context.changed_files:
        return HypothesisSet(hypotheses=[], rejected=[])
    check_diff(context)
    claims = json.dumps([claim.model_dump() for claim in contract.claims], indent=2)
    # Diff headers C-quote unusual names (b/"caf\303\251.py"), so give the literal paths too.
    files = json.dumps(context.changed_files, ensure_ascii=False)
    user = (
        f"Intent claims:\n{claims}\n\n"
        f"Changed files:\n{files}\n\n"
        f"Unified diff from base {context.base_sha} to head {context.head_sha}:\n"
        f"{context.diff_text}"
    )
    proposed = client.structured(
        SPARK_MODEL,
        developer=PROMPT,
        user=user,
        schema=ProposedHypotheses,
        reasoning_effort="medium",
    ).hypotheses
    claim_ids = {claim.id for claim in contract.claims}
    changed = set(context.changed_files)
    kept: list[Hypothesis] = []
    rejected: list[RejectedHypothesis] = []
    for hypothesis in proposed:
        reason = _problem(hypothesis, claim_ids, changed)
        if reason is None and len(kept) >= MAX_HYPOTHESES:
            reason = f"over the limit of {MAX_HYPOTHESES} hypotheses"
        if reason:
            rejected.append(RejectedHypothesis(hypothesis=hypothesis, reason=reason))
        else:
            kept.append(Hypothesis(id=f"H{len(kept) + 1}", **hypothesis.model_dump()))
    return HypothesisSet(hypotheses=kept, rejected=rejected)
