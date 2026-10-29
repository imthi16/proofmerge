"""Benchmark acceptance checks: no model, network or Docker calls."""

import hashlib
import importlib.util
import itertools
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from proofmerge.hypotheses import MAX_HYPOTHESES
from proofmerge.meta import SPARK_MODEL
from proofmerge.probes import MAX_BASE_RUNS

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_bench", ROOT / "bench/run_bench.py")
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


@pytest.fixture
def offline(tmp_path, monkeypatch):
    clients, calls, commands = [], [], []
    real_run = subprocess.run

    def client(**kwargs):
        instance = SimpleNamespace(calls=0, usage=[], max_calls=kwargs["max_calls"])
        clients.append(instance)
        return instance

    def verify(repo, base, head, intent, evidence, client):
        calls.append((repo, base, head, intent, evidence, client))
        client.calls = 3
        client.usage = [None, SimpleNamespace(prompt_tokens=11, completion_tokens=7)]
        evidence.mkdir()
        return SimpleNamespace(verdict=SimpleNamespace(decision="REVIEW", reasons=["inconclusive"]))

    def run(argv, **kwargs):
        commands.append((argv, kwargs))
        if argv[0] == sys.executable:
            return SimpleNamespace(returncode=0)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(bench, "MetaClient", client)
    monkeypatch.setattr(bench.pipeline, "run_verify", verify)
    monkeypatch.setattr(bench.subprocess, "run", run)
    return SimpleNamespace(
        out=tmp_path / "out", clients=clients, calls=calls, commands=commands,
        verify=verify, real_run=real_run,
    )


def load(out):
    return json.loads((out / "results.json").read_text())


def test_counts_held_out_errors_and_table(offline, tmp_path, monkeypatch, capsys):
    names = list(json.loads(bench.LABELS.read_text()))[:16]
    combinations = list(itertools.product((False, True), ("BLOCK", "NOT_BLOCK"),
                                          ("BLOCK", "REVIEW", "PASS", "ERROR")))
    labels = {
        name: {"verdict": label, "held_out": held_out}
        for name, (held_out, label, _) in zip(names, combinations, strict=True)
    }
    labels_file = tmp_path / "labels.json"
    labels_file.write_text(json.dumps(labels))
    monkeypatch.setattr(bench, "LABELS", labels_file)
    outcomes = dict(zip(names, (c[2] for c in combinations), strict=True))
    message = 'failure $(touch SHOULD_NOT_EXIST); `false` "quoted"'

    def verify(*args):
        proof = offline.verify(*args)
        decision = outcomes[args[2].removeprefix("candidate/")]
        if decision == "ERROR":
            raise RuntimeError(message)
        proof.verdict.decision = decision
        proof.verdict.reasons = [f"recorded {decision}"]
        return proof

    monkeypatch.setattr(bench.pipeline, "run_verify", verify)
    assert bench.main(["--out", str(offline.out)]) == 0
    data = load(offline.out)
    for group in ("held_out", "non_held_out"):
        assert data["summary"][group] == {
            f"{kind}_{decision}": 1
            for kind in ("faulty", "safe")
            for decision in ("BLOCK", "REVIEW", "PASS", "ERROR")
        }
    for name, (held_out, label, decision) in zip(names, combinations, strict=True):
        result = data["candidates"][name]
        assert result == {
            "label": label, "held_out": held_out, "decision": decision,
            "reasons": [message] if decision == "ERROR" else [f"recorded {decision}"],
            "model_calls": 3, "tokens": {"prompt": 11, "completion": 7},
            "evidence_dir": str(offline.out / name),
        }
    assert len(offline.calls) == 16  # Includes candidates after failures.
    assert len({id(client) for client in offline.clients}) == 16
    for repo, base, head, intent, evidence, client in offline.calls:
        name = head.removeprefix("candidate/")
        assert repo == offline.out / "repo"
        assert base == "main"
        assert intent == (ROOT / "bench/candidates" / name / "intent.md").read_text()
        assert evidence == offline.out / name
        assert client.max_calls == 4 + MAX_HYPOTHESES * MAX_BASE_RUNS
    output = capsys.readouterr().out
    assert "%" not in output
    for text in ("Candidate", "Held out", "non_held_out:", "held_out:",
                 "faulty -> BLOCK (caught): 1", "faulty -> REVIEW: 1",
                 "faulty -> PASS (missed): 1", "safe -> BLOCK (false block): 1",
                 "safe -> REVIEW: 1", "safe -> PASS: 1", "faulty -> ERROR: 1",
                 "safe -> ERROR: 1"):
        assert text in output
    assert all("shell" not in kwargs for _, kwargs in offline.commands)


def test_metadata_and_repeatable_selection_limit(offline):
    names = list(json.loads(bench.LABELS.read_text()))
    assert bench.main([
        "--out", str(offline.out), "--only", names[2], "--only", names[0],
        "--only", names[2], "--max-candidates", "1",
    ]) == 0
    data = load(offline.out)
    assert list(data["candidates"]) == [names[0]]
    meta = data["metadata"]
    sha = offline.real_run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                           check=True, text=True, capture_output=True).stdout.strip()
    assert meta["git_sha"] == sha
    for key, relative in (("hypotheses_sha256", "prompts/hypotheses.md"),
                          ("intent_sha256", "intent.py")):
        assert meta[key] == hashlib.sha256((ROOT / "src/proofmerge" / relative).read_bytes()).hexdigest()
    assert meta["spark_model"] == SPARK_MODEL
    assert datetime.fromisoformat(meta["date"]).utcoffset().total_seconds() == 0


def test_builds_bench_repo_using_demo_builder(offline, monkeypatch):
    monkeypatch.setattr(bench.subprocess, "run", offline.real_run)
    bench.main(["--out", str(offline.out), "--only", "mutate-stock"])
    repo = offline.out / "repo"
    result = offline.real_run(["git", "-C", str(repo), "branch", "--list", "candidate/*"],
                              check=True, capture_output=True, text=True)
    branches = {line.strip() for line in result.stdout.splitlines()}
    assert branches == {f"candidate/{name}" for name in json.loads(bench.LABELS.read_text())}
    assert len(offline.calls) == 1


@pytest.mark.parametrize("options", [
    ["--only", "unknown"], ["--max-candidates", "0"],
    ["--max-candidates", "-1"], ["--max-candidates", "text"],
])
def test_invalid_options_do_no_work(offline, options):
    with pytest.raises(SystemExit) as error:
        bench.main(["--out", str(offline.out), *options])
    assert error.value.code == 2
    assert not offline.out.exists()
    assert not offline.clients
    assert not offline.commands


def test_existing_output_is_preserved(offline):
    offline.out.mkdir()
    sentinel = offline.out / "results.json"
    sentinel.write_text("keep me")
    with pytest.raises(FileExistsError):
        bench.main(["--out", str(offline.out)])
    assert sentinel.read_text() == "keep me"
    assert not offline.clients


@pytest.mark.parametrize("label", [
    {"../escape": {"verdict": "BLOCK"}},
    {"repo": {"verdict": "BLOCK"}},
    {"case": {"verdict": "PASS"}},
    {"case": {"verdict": "BLOCK", "held_out": "false"}},
])
def test_invalid_labels_refused(offline, tmp_path, monkeypatch, label):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(label))
    monkeypatch.setattr(bench, "LABELS", path)
    with pytest.raises(SystemExit):
        bench.main(["--out", str(offline.out)])
    assert not offline.out.exists()
    assert not offline.commands


def test_client_setup_error_recorded_and_continues(offline, monkeypatch):
    def unavailable(**kwargs):
        raise RuntimeError("client unavailable")

    monkeypatch.setattr(bench, "MetaClient", unavailable)
    bench.main(["--out", str(offline.out), "--max-candidates", "2"])
    data = load(offline.out)
    assert len(data["candidates"]) == 2
    for result in data["candidates"].values():
        assert result["decision"] == "ERROR"
        assert result["reasons"] == ["client unavailable"]
        assert result["model_calls"] == 0
        assert result["tokens"] == {"prompt": 0, "completion": 0}


def test_repeatable_only_runs_each_selected_candidate_once(offline):
    names = list(json.loads(bench.LABELS.read_text()))
    bench.main([
        "--out", str(offline.out), "--only", names[3], "--only", names[1],
        "--only", names[3],
    ])
    assert list(load(offline.out)["candidates"]) == [names[1], names[3]]
    assert len(offline.clients) == 2
