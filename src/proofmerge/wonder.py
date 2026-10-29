"""Independent Wonder checks; disagreement can only qualify evidence for REVIEW."""

import json
from importlib import resources

from pydantic import BaseModel

from proofmerge.hypotheses import Hypothesis
from proofmerge.intent import C0, ClaimKind, IntentContract
from proofmerge.meta import WONDER_MODEL, MetaClient
from proofmerge.probes import Probe

CLAIMS_PROMPT = (resources.files("proofmerge") / "prompts" / "wonder_claims.md").read_text()
PROBES_PROMPT = (resources.files("proofmerge") / "prompts" / "wonder_probes.md").read_text()


class CheckedClaim(BaseModel):
    id: str
    kind: ClaimKind


class CheckedClaims(BaseModel):
    claims: list[CheckedClaim]


class CheckedProbe(BaseModel):
    hypothesis_id: str
    tests_hypothesis: bool
    reason: str


class CheckedProbes(BaseModel):
    results: list[CheckedProbe]


def cross_check_claims(
    contract: IntentContract, intent: str | None, client: MetaClient
) -> frozenset[str]:
    """Re-read only verified quotes, without Spark's kind or paraphrase."""
    explicit = [claim for claim in contract.claims if claim.id != C0.id]
    if not explicit:
        return frozenset()
    reply = client.structured(
        WONDER_MODEL,
        developer=CLAIMS_PROMPT,
        user=json.dumps({
            "intent": intent,
            "claims": [{"id": c.id, "source_quote": c.source_quote} for c in explicit],
        }),
        schema=CheckedClaims,
    )
    # Every returned interpretation must agree; duplicates cannot hide disagreement.
    return frozenset(
        claim.id for claim in explicit
        if not any(item.id == claim.id for item in reply.claims)
        or any(item.id == claim.id and item.kind != claim.kind for item in reply.claims)
    )


def check_probes(
    hypotheses: list[Hypothesis], probes: list[Probe], client: MetaClient
) -> frozenset[str]:
    """Check every base-passing probe in one call, with no execution or verdict authority."""
    passed = [probe for probe in probes if probe.passed_on_base]
    if not passed:
        return frozenset()
    by_id = {h.id: h for h in hypotheses}
    reply = client.structured(
        WONDER_MODEL,
        developer=PROBES_PROMPT,
        user=json.dumps({"probes": [
            {
                "hypothesis_id": probe.hypothesis_id,
                "hypothesis": by_id[probe.hypothesis_id].model_dump(include={
                    "title", "suspected_behavior", "expected_base", "expected_head",
                }) if probe.hypothesis_id in by_id else None,
                "test_source": probe.test_source,
            }
            for probe in passed
        ]}),
        schema=CheckedProbes,
    )
    return frozenset(
        probe.hypothesis_id for probe in passed
        if probe.hypothesis_id not in by_id
        or not any(item.hypothesis_id == probe.hypothesis_id for item in reply.results)
        or any(
            item.hypothesis_id == probe.hypothesis_id and not item.tests_hypothesis
            for item in reply.results
        )
    )
