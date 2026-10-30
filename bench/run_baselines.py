"""Run existing-suite and one-shot Spark baselines on owned candidates."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from proofmerge.context import get_context
from proofmerge.meta import SPARK_MODEL, MetaClient
from proofmerge.sandbox import (
    OUTCOME_PLUGIN,
    PYTEST_ARGV,
    TIMEOUT_SECONDS,
    Runner,
    docker_runner,
    export_tree,
    image_digest,
    open_to_runner,
    outcome_for,
)

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "bench/ground_truth/labels.json"
PROMPT = ROOT / "bench/prompts/one_shot_review.md"


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["BLOCK", "NOT_BLOCK"]
    reason: str


def metadata() -> dict:
    sha = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    backend = os.environ.get("PROOFMERGE_BACKEND", "api")
    return {
        "git_sha": sha,
        "prompt_sha256": hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
        "spark_model": os.environ.get("PROOFMERGE_SPARK_MODEL") or (
            SPARK_MODEL if backend == "api" else "CLI default"
        ),
        "backend": backend,
        "date": datetime.now(UTC).isoformat(),
    }


def existing_tests(repo, context, evidence, *, runner: Runner, digest: str) -> dict:
    """Run committed base tests against head so a patch cannot weaken its check."""
    evidence.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="proofmerge-baseline-") as tmp:
        root = Path(tmp)
        root.chmod(0o755)
        base, head, probes = root / "base", root / "head", root / "probes"
        export_tree(repo, context.base_sha, base)
        export_tree(repo, context.head_sha, head)
        shutil.copytree(base / "tests", probes / "tests")
        (probes / "conftest.py").write_text(OUTCOME_PLUGIN, encoding="utf-8")
        open_to_runner(head)
        open_to_runner(probes)
        start = time.monotonic()
        code, stdout, stderr = runner(digest, head, probes, "tests", TIMEOUT_SECONDS)
        duration = round((time.monotonic() - start) * 1000)
    (evidence / "head.stdout").write_bytes(stdout)
    (evidence / "head.stderr").write_bytes(stderr)
    outcome = outcome_for(code)
    return {
        "decision": {"PASS": "NOT_BLOCK", "FAIL": "BLOCK"}.get(outcome, "ERROR"),
        "execution": {
            "commit_sha": context.head_sha, "tests_commit_sha": context.base_sha,
            "command_argv": [*PYTEST_ARGV, "probes/tests"],
            "exit_code": code, "outcome": outcome, "duration_ms": duration,
            "timeout_seconds": TIMEOUT_SECONDS, "runner_image_digest": digest,
            "stdout_path": f"{evidence.parent.name}/baseline_a/head.stdout",
            "stderr_path": f"{evidence.parent.name}/baseline_a/head.stderr",
        },
    }


def one_shot(client, intent: str, diff: str) -> dict:
    reply = client.structured(
        SPARK_MODEL, developer=PROMPT.read_text(encoding="utf-8"),
        user=json.dumps({"untrusted_pr_intent": intent, "untrusted_base_to_head_diff": diff}),
        schema=Review, max_completion_tokens=8192,
    )
    return Review.model_validate(
        reply.model_dump() if isinstance(reply, BaseModel) else reply, strict=True
    ).model_dump()


def summarize(results: dict) -> dict:
    summary = {
        baseline: {
            group: {f"{label}_{decision}": 0
                    for label in ("faulty", "safe")
                    for decision in ("BLOCK", "NOT_BLOCK", "ERROR")}
            for group in ("non_held_out", "held_out")
        } for baseline in ("baseline_a", "baseline_b")
    }
    for result in results.values():
        group = "held_out" if result["held_out"] else "non_held_out"
        label = "faulty" if result["label"] == "BLOCK" else "safe"
        for baseline in summary:
            if result[baseline] is not None:
                summary[baseline][group][f"{label}_{result[baseline]['decision']}"] += 1
    return summary


def print_results(results: dict, summary: dict) -> None:
    print(f"{'Candidate':36} {'Label':10} {'Held out':8} {'Tests':10} Spark")
    for name, result in results.items():
        spark = result["baseline_b"]["decision"] if result["baseline_b"] else "SKIPPED"
        print(f"{name:36} {result['label']:10} {result['held_out']!s:8} "
              f"{result['baseline_a']['decision']:10} {spark}")
    for baseline, groups in summary.items():
        for group, counts in groups.items():
            print(f"\n{baseline} {group}:")
            for key, count in counts.items():
                print(f"  {key}: {count}")


def main(argv: list[str] | None = None, *, runner: Runner = docker_runner) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="New output directory")
    parser.add_argument("--only", action="append", nargs="+", default=[],
                        help="Candidate names (repeatable)")
    parser.add_argument("--skip-model", action="store_true")
    args = parser.parse_args(argv)
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    only = {name for selection in args.only for name in selection}
    unknown = only - labels.keys()
    if unknown:
        parser.error(f"unknown candidates: {', '.join(sorted(unknown))}")
    names = [name for name in labels if not only or name in only]
    for name in names:
        if (
            not name or name in (".", "..", "repo")
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in name)
            or labels[name].get("verdict") not in ("BLOCK", "NOT_BLOCK")
            or type(labels[name].get("held_out", False)) is not bool
        ):
            parser.error(f"invalid benchmark label: {name!r}")
    run_metadata = metadata()
    run_metadata["skip_model"] = args.skip_model
    out = args.out.resolve()
    out.mkdir(parents=True)
    repo = out / "repo"
    subprocess.run(
        [sys.executable, str(ROOT / "demo/build_demo_repo.py"), str(repo),
         "--candidates", str(ROOT / "bench/candidates")], check=True,
    )
    results = {}
    for name in names:
        result = {
            "label": labels[name]["verdict"], "held_out": labels[name].get("held_out", False),
            "baseline_a": {"decision": "ERROR"}, "baseline_b": None,
            "model_calls": 0, "tokens": {"prompt": 0, "completion": 0}, "evidence_dir": name,
        }
        context = None
        try:
            context = get_context(repo, "main", f"candidate/{name}")
            result["baseline_a"] = existing_tests(
                repo, context, out / name / "baseline_a", runner=runner, digest=image_digest()
            )
        except Exception as error:  # noqa: BLE001 — retain failures and continue candidates
            result["baseline_a"]["reason"] = str(error).replace(str(out), ".")
        if not args.skip_model:
            client = None
            try:
                if context is None:
                    context = get_context(repo, "main", f"candidate/{name}")
                intent = (ROOT / "bench/candidates" / name / "intent.md").read_text(
                    encoding="utf-8"
                )
                client = MetaClient(max_calls=1)
                result["baseline_b"] = one_shot(client, intent, context.diff_text)
            except Exception as error:  # noqa: BLE001 — no retry on API/schema/input failure
                result["baseline_b"] = {
                    "decision": "ERROR", "reason": str(error).replace(str(out), "."),
                }
            if client is not None:
                usage = [u for u in client.usage if u is not None]
                result["model_calls"] = client.calls
                result["tokens"] = {
                    "prompt": sum(u.prompt_tokens for u in usage),
                    "completion": sum(u.completion_tokens for u in usage),
                }
        results[name] = result
    summary = summarize(results)
    (out / "results.json").write_text(
        json.dumps({"metadata": run_metadata, "candidates": results, "summary": summary},
                   indent=2) + "\n", encoding="utf-8",
    )
    print_results(results, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
