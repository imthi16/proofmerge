"""A real `proofmerge dashboard` server over fixture evidence, for browser tests.

Only the outside world is faked: `gh` lists two pull requests, `git fetch` succeeds,
and `proofmerge verify` writes stage events to its --events-file at a readable pace and
then copies a demo run's evidence. Nothing here touches Docker, the network or a model.

    python e2e/serve.py --port 8799
"""

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from proofmerge.dashboard import make_server
from proofmerge.pipeline import SKIP_WHEN, STAGES

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo" / "runs" / "2026-10-29"
BENCH = ROOT / "bench" / "results" / "2026-10-30-run3"
BASELINES = ROOT / "bench" / "results" / "2026-10-30-baselines"

PRS = [
    {"number": 12, "title": "Cache SKU prices per tenant", "author": {"login": "agent-bot"},
     "headRefName": "agent/sku-cache", "baseRefName": "main", "state": "OPEN",
     "url": "https://github.com/example/shop/pull/12", "updatedAt": "2026-10-31T10:00:00Z",
     "isDraft": False, "body": "Cache prices. Returned prices must not change for any tenant.\n"
                               "<img src=x onerror=alert(1)>"},
    {"number": 9, "title": "Rename helper", "author": {"login": "dev"},
     "headRefName": "dev/rename", "baseRefName": "main", "state": "MERGED",
     "url": "https://github.com/example/shop/pull/9", "updatedAt": "2026-10-20T10:00:00Z",
     "isDraft": False, "body": ""},
]
STEP_SECONDS = 0.25


def stage_lines():
    """Stage events shaped like a real BLOCK run, with the tamper guard skipped."""
    skipped = {"run_tamper_guard"}
    assert skipped <= set(SKIP_WHEN)
    facts = {
        "extract_intent": {"claims": 2}, "generate_hypotheses": {"hypotheses": 2},
        "generate_execute_probes": {"probes": 2, "executions": 12},
        "normalize_findings": {"findings": 2, "verified": 2},
        "deterministic_decide": {"decision": "BLOCK"},
    }
    for name, _ in STAGES:
        if name in skipped:
            yield {"type": "stage.skipped", "stage": name, "data": {}}
            continue
        yield {"type": "stage.started", "stage": name, "data": {}}
        if name == "extract_intent":
            yield {"type": "stage.retrying", "stage": name, "data": {"attempt": 1, "max_attempts": 3}}
        yield {"type": "stage.completed", "stage": name,
               "data": {"duration_ms": 180, "errors": 0} | facts.get(name, {})}


class FakeWorld:
    def __init__(self, head_sha: str) -> None:
        self.head_sha = head_sha

    def __call__(self, argv, **kwargs):
        if argv[:3] == ["gh", "repo", "view"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"nameWithOwner": "example/shop", "url": "https://github.com/example/shop"}), stderr="")
        if argv[:3] == ["gh", "pr", "list"]:
            prs = [p | {"headRefOid": self.head_sha if p["number"] == 12 else "f" * 40} for p in PRS]
            return SimpleNamespace(returncode=0, stdout=json.dumps(prs), stderr="")
        if argv[:2] == ["gh", "auth"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["docker", "version"] or argv[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "verify" in argv:
            events = Path(argv[argv.index("--events-file") + 1])
            evidence = Path(argv[argv.index("--evidence-dir") + 1])
            with events.open("a") as handle:
                for line in stage_lines():
                    time.sleep(STEP_SECONDS)
                    handle.write(json.dumps(line) + "\n")
                    handle.flush()
            shutil.copytree(DEMO / "tenant-cache-take2", evidence)
            return SimpleNamespace(returncode=2, stdout="verdict: BLOCK\n", stderr="")
        if argv[:2] == ["git", "-C"]:
            time.sleep(STEP_SECONDS)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")


def build_root(base: Path) -> Path:
    root = base / "runs"
    for name in ("tenant-cache-take2", "intended-change", "test-tamper"):
        shutil.copytree(DEMO / name, root / name)
    shutil.copytree(BENCH, root / BENCH.name)
    shutil.copytree(BASELINES, root / BASELINES.name)
    return root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="proofmerge-e2e-"))
    root = build_root(base)
    head = json.loads((root / "tenant-cache-take2" / "proof.json").read_text())["head_sha"]
    repo = base / "repo"
    (repo / ".git").mkdir(parents=True)
    server = make_server(root, args.port, repo, FakeWorld(head), heartbeat=2.0)
    print(f"e2e dashboard on http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
