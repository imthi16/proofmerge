import hashlib
import json
import os
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from proofmerge import cli
from proofmerge.manifest import ManifestError, evidence_sha256, verify_manifest, write_manifest


def test_non_regular_files_are_rejected(tmp_path):
    os.mkfifo(tmp_path / "manifest.json")
    os.mkfifo(tmp_path / "probe.py")

    def expire(*_args):
        raise TimeoutError("blocked reading a fifo")

    signal.signal(signal.SIGALRM, expire)
    signal.alarm(2)
    try:
        with pytest.raises(ManifestError, match="regular file"):
            write_manifest(tmp_path)
        assert verify_manifest(tmp_path) == ["Cannot verify manifest.json: not a regular file."]
        with pytest.raises(ManifestError, match="regular file"):
            evidence_sha256(tmp_path, "probe.py")
    finally:
        signal.alarm(0)


def test_write_then_verify(tmp_path):
    (tmp_path / "probes").mkdir()
    (tmp_path / "probes/test_probe.py").write_bytes(b"probe")
    (tmp_path / "z.log").write_bytes(b"log")
    manifest = write_manifest(tmp_path)
    assert manifest == tmp_path / "manifest.json"
    assert json.loads(manifest.read_text()) == {
        "files": {
            "probes/test_probe.py": hashlib.sha256(b"probe").hexdigest(),
            "z.log": hashlib.sha256(b"log").hexdigest(),
        }
    }
    assert verify_manifest(tmp_path) == []
    write_manifest(tmp_path)
    assert verify_manifest(tmp_path) == []


@pytest.mark.parametrize("change", ["modify", "delete", "add"])
def test_changed_files(tmp_path, change):
    probe = tmp_path / "probe.py"
    probe.write_bytes(b"probe")
    write_manifest(tmp_path)
    if change == "modify":
        probe.write_bytes(b"Probe")
        expected = "Hash mismatch: probe.py."
    elif change == "delete":
        probe.unlink()
        expected = "File missing: probe.py."
    else:
        (tmp_path / "extra.log").write_text("extra")
        expected = "File present but not listed: extra.log."
    assert verify_manifest(tmp_path) == [expected]


@pytest.mark.parametrize("name", ["../x", "/tmp/x"])
def test_unsafe_manifest_path(tmp_path, name):
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {name: "0" * 64}}))
    assert verify_manifest(tmp_path) == [f"Unsafe manifest path: {name}."]


@pytest.mark.parametrize("content", [None, "{", "[]", '{"files": []}', '{"files": {"x": 1}}'])
def test_missing_or_invalid_manifest_is_one_problem(tmp_path, content):
    if content is not None:
        (tmp_path / "manifest.json").write_text(content)
    problems = verify_manifest(tmp_path)
    assert len(problems) == 1
    assert "manifest.json" in problems[0]


@pytest.mark.parametrize("kind", ["file", "directory", "broken", "manifest"])
def test_symlinks_are_rejected(tmp_path, kind):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    write_manifest(evidence)
    outside = tmp_path / "outside"
    if kind == "directory":
        outside.mkdir()
        (outside / "secret").write_text("not evidence")
    elif kind != "broken":
        outside.write_text("not evidence")
    name = "manifest.json" if kind == "manifest" else "link"
    link = evidence / name
    if link.exists():
        link.unlink()
    link.symlink_to(outside)
    with pytest.raises(ManifestError, match="Symlink"):
        write_manifest(evidence)
    assert any(f"Symlink is not allowed: {name}." == p for p in verify_manifest(evidence))


@pytest.fixture
def proof_bundle(tmp_path):
    data = json.loads((Path(__file__).parent / "fixtures/sample_proof_block.json").read_text())
    source = b"def test_probe():\n    assert True\n"
    for experiment in data["experiments"]:
        target = tmp_path / experiment["test_source_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        experiment["test_source_sha256"] = hashlib.sha256(source).hexdigest()
    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps(data))
    write_manifest(tmp_path)
    return proof


def test_replay_intact(proof_bundle):
    result = CliRunner().invoke(cli.app, ["replay", str(proof_bundle)])
    assert result.exit_code == 0
    assert result.stdout == "evidence intact: 2 files\n"


@pytest.mark.parametrize("rewrite_manifest", [False, True])
def test_replay_modified_probe(proof_bundle, rewrite_manifest):
    (proof_bundle.parent / "probes/test_h1.py").write_text("changed")
    if rewrite_manifest:
        write_manifest(proof_bundle.parent)
    result = CliRunner().invoke(cli.app, ["replay", str(proof_bundle)])
    assert result.exit_code == 1
    assert "Probe hash mismatch: probes/test_h1.py." in result.stdout


@pytest.mark.parametrize("source", ["../outside.py", "/tmp/outside.py", "missing.py"])
def test_replay_rejects_invalid_probe_path(proof_bundle, source):
    data = json.loads(proof_bundle.read_text())
    data["experiments"][0]["test_source_path"] = source
    proof_bundle.write_text(json.dumps(data))
    write_manifest(proof_bundle.parent)
    result = CliRunner().invoke(cli.app, ["replay", str(proof_bundle)])
    assert result.exit_code == 1
    assert source in result.stdout


def test_replay_invalid_proof(proof_bundle):
    proof_bundle.write_text("{}")
    write_manifest(proof_bundle.parent)
    result = CliRunner().invoke(cli.app, ["replay", str(proof_bundle)])
    assert result.exit_code == 1
    assert "Cannot read proof" in result.stdout


def test_probe_writes_manifest_after_run_json(tmp_path, monkeypatch):
    context = SimpleNamespace(changed_files=["x.py"], base_sha="base", head_sha="head")
    contract = SimpleNamespace(model_dump=dict)
    hypotheses = SimpleNamespace(hypotheses=[], model_dump=dict)
    client = SimpleNamespace(usage=[], calls=0)
    monkeypatch.setattr(cli, "get_context", lambda *args: context)
    monkeypatch.setattr(cli, "check_diff", lambda *args: None)
    monkeypatch.setattr(cli, "MetaClient", lambda **kwargs: client)
    monkeypatch.setattr(cli, "extract_intent", lambda *args: contract)
    monkeypatch.setattr(cli, "generate_hypotheses", lambda *args: hypotheses)
    monkeypatch.setattr(cli, "probe_hypotheses", lambda *args: ([], []))
    evidence = tmp_path / "evidence"
    result = CliRunner().invoke(cli.app, [
        "probe", str(tmp_path), "--base", "base", "--head", "head",
        "--evidence-dir", str(evidence),
    ])
    assert result.exit_code == 0, result.output
    assert verify_manifest(evidence) == []
    assert list(json.loads((evidence / "manifest.json").read_text())["files"]) == [
        "probe-run.json"
    ]
