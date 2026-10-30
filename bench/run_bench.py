"""Run the owned benchmark candidates and record counts, usage and provenance."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from proofmerge import pipeline
from proofmerge.hypotheses import MAX_HYPOTHESES
from proofmerge.meta import SPARK_MODEL, MetaClient
from proofmerge.probes import MAX_BASE_RUNS

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "bench" / "ground_truth" / "labels.json"


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def metadata() -> dict:
    sha = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return {
        "git_sha": sha,
        "hypotheses_sha256": hashlib.sha256(
            (ROOT / "src/proofmerge/prompts/hypotheses.md").read_bytes()
        ).hexdigest(),
        "intent_sha256": hashlib.sha256(
            (ROOT / "src/proofmerge/intent.py").read_bytes()
        ).hexdigest(),
        "spark_model": os.environ.get("PROOFMERGE_SPARK_MODEL") or SPARK_MODEL,
        "backend": os.environ.get("PROOFMERGE_BACKEND", "api"),
        "date": datetime.now(UTC).isoformat(),
    }


def summarize(results: dict) -> dict:
    summary = {
        group: {
            f"{label}_{decision}": 0
            for label in ("faulty", "safe")
            for decision in ("BLOCK", "REVIEW", "PASS", "ERROR")
        }
        for group in ("non_held_out", "held_out")
    }
    for result in results.values():
        group = "held_out" if result["held_out"] else "non_held_out"
        label = "faulty" if result["label"] == "BLOCK" else "safe"
        summary[group][f"{label}_{result['decision']}"] += 1
    return summary


def print_results(results: dict, summary: dict) -> None:
    print(f"{'Candidate':36} {'Label':10} {'Held out':8} Decision")
    for name, result in results.items():
        print(f"{name:36} {result['label']:10} {result['held_out']!s:8} {result['decision']}")
    for group, counts in summary.items():
        print(f"\n{group}:")
        print(f"  faulty -> BLOCK (caught): {counts['faulty_BLOCK']}")
        print(f"  faulty -> REVIEW: {counts['faulty_REVIEW']}")
        print(f"  faulty -> PASS (missed): {counts['faulty_PASS']}")
        print(f"  safe -> BLOCK (false block): {counts['safe_BLOCK']}")
        print(f"  safe -> REVIEW: {counts['safe_REVIEW']}")
        print(f"  safe -> PASS: {counts['safe_PASS']}")
        print(f"  faulty -> ERROR: {counts['faulty_ERROR']}")
        print(f"  safe -> ERROR: {counts['safe_ERROR']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="New output directory")
    parser.add_argument("--only", action="append", default=[], help="Candidate name (repeatable)")
    parser.add_argument("--max-candidates", type=positive_int)
    args = parser.parse_args(argv)
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    unknown = set(args.only) - labels.keys()
    if unknown:
        parser.error(f"unknown candidates: {', '.join(sorted(unknown))}")
    names = [name for name in labels if not args.only or name in args.only]
    names = names[:args.max_candidates]
    # Names become evidence paths and git refs; reject traversal before creating output.
    for name in names:
        if (
            not name or name in (".", "..", "repo")
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in name)
            or labels[name].get("verdict") not in ("BLOCK", "NOT_BLOCK")
            or type(labels[name].get("held_out", False)) is not bool
        ):
            parser.error(f"invalid benchmark label: {name!r}")
    run_metadata = metadata()
    out = args.out.resolve()
    out.mkdir(parents=True)  # Never overwrite a previous run or mix evidence bundles.
    repo = out / "repo"
    subprocess.run(
        [sys.executable, str(ROOT / "demo/build_demo_repo.py"), str(repo),
         "--candidates", str(ROOT / "bench/candidates")],
        check=True,
    )
    results = {}
    for name in names:
        result = {
            "label": labels[name]["verdict"],
            "held_out": labels[name].get("held_out", False),
            "decision": "ERROR",
            "reasons": [],
            "model_calls": 0,
            "tokens": {"prompt": 0, "completion": 0},
            "evidence_dir": str(out / name),
        }
        client = None
        try:
            intent = (ROOT / "bench/candidates" / name / "intent.md").read_text(
                encoding="utf-8"
            )
            client = MetaClient(max_calls=4 + MAX_HYPOTHESES * MAX_BASE_RUNS)
            proof = pipeline.run_verify(
                repo, "main", f"candidate/{name}", intent, out / name, client
            )
            result.update(decision=proof.verdict.decision, reasons=proof.verdict.reasons)
        except Exception as error:  # noqa: BLE001 — record any candidate failure and continue
            # A failed candidate must not hide later results or masquerade as a verdict.
            result["reasons"] = [str(error)]
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
        json.dumps({"metadata": run_metadata, "candidates": results, "summary": summary}, indent=2)
        + "\n", encoding="utf-8",
    )
    print_results(results, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
