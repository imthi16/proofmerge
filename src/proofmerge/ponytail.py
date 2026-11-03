"""An over-engineering review of a pull request's diff, run on request from a finished run.

The rules come from the ponytail-review skill (MIT, (c) 2026 DietrichGebert), rewritten as a
prompt in prompts/ponytail.md. It reads the diff only: it never runs code, never changes the
verdict, and writes <evidence dir>.ponytail/ponytail.json beside the sealed evidence.
"""

import json
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from proofmerge.diffbudget import view_diff
from proofmerge.evidence import Proof
from proofmerge.meta import SPARK_MODEL, MetaClient

PROMPT = (resources.files("proofmerge") / "prompts" / "ponytail.md").read_text()
MAX_FINDINGS = 15


class PonytailFinding(BaseModel):
    location: str
    tag: Literal["delete", "stdlib", "native", "yagni", "shrink"]
    what: str
    replacement: str


class PonytailReview(BaseModel):
    findings: list[PonytailFinding]
    net_lines: int = 0


class PonytailReport(PonytailReview):
    run_id: str
    base_sha: str
    head_sha: str
    endpoint: str | None = None
    calls: int = 0
    truncated: bool = False  # the diff was longer than the model was shown


def ponytail_dir(evidence_dir: Path) -> Path:
    return evidence_dir.parent / f"{evidence_dir.name}.ponytail"


def load_report(evidence_dir: Path) -> PonytailReport | None:
    try:
        return PonytailReport.model_validate_json(
            (ponytail_dir(evidence_dir) / "ponytail.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


def review_diff(proof: Proof, evidence_dir: Path, client: MetaClient, diff_text: str) -> PonytailReport:
    """One model call on the diff. Raises MetaError when the model fails."""
    shown = view_diff(diff_text)
    intent = "\n".join(f"- {claim.statement}" for claim in proof.intent.claims)
    review = client.structured(
        SPARK_MODEL,
        developer=PROMPT,
        user=f"What the pull request intends:\n{intent or '(no description)'}\n\nUnified diff:\n{shown.text}",
        schema=PonytailReview,
        reasoning_effort="medium",
    )
    report = PonytailReport(
        findings=review.findings[:MAX_FINDINGS],
        net_lines=max(0, review.net_lines),
        run_id=proof.run_id,
        base_sha=proof.base_sha,
        head_sha=proof.head_sha,
        endpoint=getattr(client, "endpoint", None),
        calls=client.calls,
        truncated=bool(shown.omitted or shown.noted),
    )
    out = ponytail_dir(evidence_dir)
    out.mkdir(exist_ok=True)
    (out / "ponytail.json").write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    return report
