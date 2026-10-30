"""Offline baseline checks: injected runner and structured client, no network."""

import hashlib
import importlib.util
import itertools
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from proofmerge.meta import SPARK_MODEL, MetaError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_baselines", ROOT / "bench/run_baselines.py")
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


@pytest.fixture
def offline(tmp_path, monkeypatch):
    clients, runs, requests = [], [], []
    state = SimpleNamespace(out=tmp_path / "out", clients=clients, runs=runs,
                            requests=requests, code=0, reply="BLOCK", error=None)

    class Client:
        def __init__(self, *, max_calls):
            assert max_calls == 1
            self.calls, self.usage = 0, []
            clients.append(self)

        def structured(self, model, **kwargs):
            self.calls += 1
            requests.append((model, kwargs))
            self.usage.append(SimpleNamespace(prompt_tokens=11, completion_tokens=7))
            if state.error:
                raise state.error
            return {"decision": state.reply, "reason": "reviewed"}

    def runner(image, head, probes, name, timeout):
        runs.append((image, name, timeout))
        assert (head / "src/demo_shop/pricing.py").exists()
        assert (probes / "tests/test_pricing.py").read_bytes() == (
            ROOT / "demo/demo-shop/tests/test_pricing.py"
        ).read_bytes()
        assert (probes / "conftest.py").read_text() == bench.OUTCOME_PLUGIN
        if isinstance(state.code, Exception):
            raise state.code
        return state.code, b"suite stdout", b"suite stderr"

    state.runner = runner
    monkeypatch.setattr(bench, "MetaClient", Client)
    monkeypatch.setattr(bench, "image_digest", lambda: "sha256:fake")
    return state


def run(state, *options):
    name = next(iter(json.loads(bench.LABELS.read_text())))
    bench.main(["--out", str(state.out), "--only", name, *options], runner=state.runner)
    data = json.loads((state.out / "results.json").read_text())
    return data, data["candidates"][name]


@pytest.mark.parametrize(("code", "decision"), [
    (0, "NOT_BLOCK"), (1, "BLOCK"), (None, "ERROR"), (125, "ERROR"),
    (2, "ERROR"), (6, "ERROR"), (RuntimeError("runner unavailable"), "ERROR"),
])
def test_suite_outcomes_and_skip(offline, code, decision):
    offline.code = code
    data, result = run(offline, "--skip-model")
    assert result["baseline_a"]["decision"] == decision
    assert result["baseline_b"] is None
    assert result["model_calls"] == 0
    assert result["tokens"] == {"prompt": 0, "completion": 0}
    assert not offline.clients
    assert len(offline.runs) == 1
    assert data["metadata"]["skip_model"]
    assert str(offline.out) not in json.dumps(data)
    if not isinstance(code, Exception):
        execution = result["baseline_a"]["execution"]
        assert (offline.out / execution["stdout_path"]).read_bytes() == b"suite stdout"
        assert (offline.out / execution["stderr_path"]).read_bytes() == b"suite stderr"
        assert execution["exit_code"] == code


@pytest.mark.parametrize("reply", ["BLOCK", "NOT_BLOCK"])
def test_spark_single_call_full_input_usage_and_metadata(offline, reply):
    offline.reply = reply
    data, result = run(offline)
    assert result["baseline_b"] == {"decision": reply, "reason": "reviewed"}
    assert result["model_calls"] == 1
    assert result["tokens"] == {"prompt": 11, "completion": 7}
    assert len(offline.requests) == 1
    model, request = offline.requests[0]
    assert model == SPARK_MODEL
    assert request["schema"] is bench.Review
    assert request["max_completion_tokens"] == 8192
    assert request["developer"] == bench.PROMPT.read_text()
    name = next(iter(data["candidates"]))
    user = json.loads(request["user"])
    assert user == {
        "untrusted_pr_intent": (ROOT / "bench/candidates" / name / "intent.md").read_text(),
        "untrusted_base_to_head_diff": bench.get_context(
            offline.out / "repo", "main", f"candidate/{name}"
        ).diff_text,
    }
    meta = data["metadata"]
    assert len(meta["git_sha"]) == 40
    assert meta["spark_model"] == SPARK_MODEL
    assert meta["prompt_sha256"] == hashlib.sha256(bench.PROMPT.read_bytes()).hexdigest()
    assert datetime.fromisoformat(meta["date"]).utcoffset().total_seconds() == 0

    def check_paths(value):
        if isinstance(value, dict):
            for item in value.values():
                check_paths(item)
        elif isinstance(value, list):
            for item in value:
                check_paths(item)
        elif isinstance(value, str):
            assert not Path(value).is_absolute()
    check_paths(data)


@pytest.mark.parametrize("failure", ["api", "schema"])
def test_error_no_retry(offline, failure):
    if failure == "api":
        offline.error = MetaError("could not reach the Meta API")
    else:
        offline.reply = "PASS"
    _, result = run(offline)
    assert result["baseline_b"]["decision"] == "ERROR"
    assert result["baseline_b"]["reason"]
    assert offline.clients[0].calls == 1
    assert len(offline.requests) == 1


def test_a_failure_does_not_skip_b(offline):
    offline.code = RuntimeError("runner unavailable")
    _, result = run(offline)
    assert result["baseline_a"]["decision"] == "ERROR"
    assert result["baseline_b"]["decision"] == "BLOCK"


def test_summary_all_cells_and_table(capsys):
    results = {}
    for i, (held, label, decision) in enumerate(itertools.product(
        (False, True), ("BLOCK", "NOT_BLOCK"), ("BLOCK", "NOT_BLOCK", "ERROR")
    )):
        results[str(i)] = {
            "held_out": held, "label": label,
            "baseline_a": {"decision": decision}, "baseline_b": {"decision": decision},
        }
    summary = bench.summarize(results)
    for groups in summary.values():
        for counts in groups.values():
            assert len(counts) == 6
            assert set(counts.values()) == {1}
    bench.print_results(results, summary)
    output = capsys.readouterr().out
    for text in ("Candidate", "Held out", "Tests", "Spark", "held_out:",
                 "non_held_out:", "faulty_BLOCK: 1", "safe_ERROR: 1"):
        assert text in output


def test_prompt_is_neutral():
    prompt = bench.PROMPT.read_text().lower()
    for forbidden in ("label", "faulty", "bench", "proofmerge"):
        assert forbidden not in prompt


@pytest.mark.parametrize("labels", [
    {"../escape": {"verdict": "BLOCK"}}, {"repo": {"verdict": "BLOCK"}},
    {"case": {"verdict": "PASS"}}, {"case": {"verdict": "BLOCK", "held_out": "false"}},
])
def test_invalid_labels_no_work(offline, monkeypatch, tmp_path, labels):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(labels))
    monkeypatch.setattr(bench, "LABELS", path)
    with pytest.raises(SystemExit):
        bench.main(["--out", str(offline.out)], runner=offline.runner)
    assert not offline.out.exists()


def test_unknown_and_existing_output(offline):
    with pytest.raises(SystemExit):
        bench.main(["--out", str(offline.out), "--only", "unknown"])
    assert not offline.out.exists()
    offline.out.mkdir()
    sentinel = offline.out / "results.json"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        bench.main(["--out", str(offline.out)])
    assert sentinel.read_text() == "keep"


def test_runtime_selection_repeatable_and_continues(offline, monkeypatch, tmp_path):
    names = list(json.loads(bench.LABELS.read_text()))[:3]
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({
        name: {"verdict": "NOT_BLOCK", "held_out": True} for name in names
    }))
    monkeypatch.setattr(bench, "LABELS", path)
    offline.error = MetaError("unavailable")
    bench.main(["--out", str(offline.out), "--only", names[2], names[0],
                "--only", names[0]], runner=offline.runner)
    data = json.loads((offline.out / "results.json").read_text())
    assert list(data["candidates"]) == [names[0], names[2]]
    assert len(offline.requests) == 2
    assert data["summary"]["baseline_b"]["held_out"]["safe_ERROR"] == 2
