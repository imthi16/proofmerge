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

from proofmerge.diffbudget import DiffView, view_diff
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


def review_text(intent: str, client: MetaClient, diff_text: str) -> tuple[PonytailReview, DiffView]:
    """One model call on the diff. Raises MetaError when the model fails."""
    shown = view_diff(diff_text)
    review = client.structured(
        SPARK_MODEL,
        developer=PROMPT,
        user=f"What the pull request intends:\n{intent.strip() or '(no description)'}\n\nUnified diff:\n{shown.text}",
        schema=PonytailReview,
        reasoning_effort="medium",
    )
    return review, shown


def _report(review: PonytailReview, shown: DiffView, client: MetaClient, run_id: str, base: str, head: str) -> PonytailReport:
    return PonytailReport(
        findings=review.findings[:MAX_FINDINGS],
        net_lines=max(0, review.net_lines),
        run_id=run_id,
        base_sha=base,
        head_sha=head,
        endpoint=getattr(client, "endpoint", None),
        calls=client.calls,
        truncated=bool(shown.omitted or shown.noted),
    )


def review_diff(proof: Proof, evidence_dir: Path, client: MetaClient, diff_text: str) -> PonytailReport:
    """The review of a finished run's diff, written beside its evidence."""
    intent = "\n".join(f"- {claim.statement}" for claim in proof.intent.claims)
    review, shown = review_text(intent, client, diff_text)
    report = _report(review, shown, client, proof.run_id, proof.base_sha, proof.head_sha)
    out = ponytail_dir(evidence_dir)
    out.mkdir(exist_ok=True)
    (out / "ponytail.json").write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    return report


def review_pull_request(
    label: str, base_sha: str, head_sha: str, intent: str, client: MetaClient, diff_text: str, out: Path
) -> PonytailReport:
    """The review of a pull request's diff, with no run needed, written to `out`."""
    review, shown = review_text(intent, client, diff_text)
    report = _report(review, shown, client, label, base_sha, head_sha)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    return report


def load_report_file(path: Path) -> PonytailReport | None:
    try:
        return PonytailReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
