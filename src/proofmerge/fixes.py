"""Suggest a fix for each verified regression, then check it the way the regression was found.

The writer model proposes new content for files the pull request changed. ProofMerge builds
that as a commit under refs/proofmerge/fix/ (never a branch, never the working tree), runs
the failing probe on base and on the fixed head in the sandbox, and calls the fix verified
only when the probe passes every attempt on both. The fix never touches the verdict.

Results go to <evidence dir>.fix/, beside the sealed evidence: the run's manifest stays
exactly as verify wrote it.
"""

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from importlib import resources
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from proofmerge.evidence import Experiment, Proof, read_evidence
from proofmerge.meta import SPARK_MODEL, MetaClient, MetaError
from proofmerge.sandbox import HYPOTHESIS_ID, SHA

PROMPT = (resources.files("proofmerge") / "prompts" / "fix.md").read_text()
MAX_FIXES = 3  # regressions per run that get a fix
MAX_DRAFTS = 2  # a second draft sees why the first one did not pass
MAX_FILES = 5
MAX_FILE_CHARS = 200_000
TEST_FILE = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|^conftest\.py$)")
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs"}

# (fixed head sha, hypothesis id, probe source, destination) -> the probe's runs on base and the fix
ProbeRunner = Callable[[str, str, str, Path], Experiment]


class FixError(Exception):
    """A fix that cannot be built or checked; the message is safe to show."""


class FileFix(BaseModel):
    path: str
    content: str


class ProposedFix(BaseModel):
    explanation: str = Field(max_length=1200)
    files: list[FileFix]


class FixResult(BaseModel):
    hypothesis_id: str
    explanation: str
    files: list[FileFix] = []
    patch: str = ""  # unified diff from the PR head to the fix
    fix_sha: str | None = None
    base_outcomes: str = ""  # e.g. "PPP"
    fixed_outcomes: str = ""
    verified: bool = False  # the failing probe passed every attempt on base and on the fix
    drafts: int = 0
    problem: str | None = None  # why there is no verified fix, in plain words


class FixReport(BaseModel):
    run_id: str
    base_sha: str
    head_sha: str
    endpoint: str | None = None
    calls: int = 0
    fixes: list[FixResult]


def fix_dir(evidence_dir: Path) -> Path:
    return evidence_dir.parent / f"{evidence_dir.name}.fix"


def load_report(evidence_dir: Path) -> FixReport | None:
    path = fix_dir(evidence_dir) / "fix.json"
    try:
        return FixReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def regressions(proof: Proof) -> list[str]:
    """Hypotheses whose verified regression stands: the evidence behind a BLOCK.

    A probe Wonder doubted, or one that may show the intended change, gets no fix: fixing it
    could undo what the pull request is meant to do. A regression against a claim Wonder contested
    is a REVIEW matter, not a BLOCK one, so it gets no fix either."""
    doubted = set(proof.mismatched_probe_ids) | set(proof.explained_probe_ids)
    return [
        f.hypothesis_id
        for f in proof.findings
        if f.verified
        and f.classification == "REGRESSION"
        and f.hypothesis_id not in doubted
        and f.claim_id not in proof.contested_claim_ids
    ][:MAX_FIXES]


def is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(TEST_FILE.search(parts[-1])) or any(p in TEST_DIRS for p in parts[:-1])


def _git(
    repo: Path, *args: str, stdin: bytes | None = None, env: dict[str, str] | None = None
) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        env=env,
        check=False,
        timeout=60,
    )
    if result.returncode:
        raise FixError(
            f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()[:200]}"
        )
    return result.stdout


def head_files(repo: Path, head_sha: str, paths: list[str]) -> dict[str, str]:
    """The PR head's text of each changed file that still exists there."""
    files = {}
    for path in paths:
        try:
            files[path] = _git(repo, "show", f"{head_sha}:{path}").decode("utf-8")
        except (FixError, UnicodeDecodeError):
            continue  # deleted at head, or not text: not a file a fix may edit
    return files


def check_proposal(proposal: ProposedFix, allowed: dict[str, str]) -> list[FileFix]:
    """The files a proposal changes, or FixError naming the first rule it breaks."""
    if not proposal.files:
        raise FixError("The model found no safe fix inside the changed files.")
    if len(proposal.files) > MAX_FILES:
        raise FixError(f"The fix changes more than {MAX_FILES} files.")
    seen: set[str] = set()
    changed = []
    for file in proposal.files:
        if file.path in seen:
            raise FixError(f"The fix lists {file.path} twice.")
        seen.add(file.path)
        if file.path not in allowed:
            raise FixError(f"The fix edits {file.path!r}, which the pull request did not change.")
        if is_test_path(file.path):
            raise FixError(f"The fix edits the test file {file.path}; fixes may not change tests.")
        if len(file.content) > MAX_FILE_CHARS:
            raise FixError(f"The fix for {file.path} is too large.")
        if file.content != allowed[file.path]:
            changed.append(file)
    if not changed:
        raise FixError("The fix does not change anything.")
    return changed


def commit_fix(repo: Path, head_sha: str, files: list[FileFix], ref: str) -> str:
    """Build head + files as a commit under refs/proofmerge/fix/, without a checkout."""
    if not SHA.fullmatch(head_sha) or not ref.startswith("refs/proofmerge/fix/"):
        raise FixError("Refusing an unexpected commit or ref.")
    with tempfile.TemporaryDirectory(prefix="proofmerge-fix-") as tmp:
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(Path(tmp) / "index"),
            "GIT_AUTHOR_NAME": "ProofMerge",
            "GIT_AUTHOR_EMAIL": "proofmerge@localhost",
            "GIT_COMMITTER_NAME": "ProofMerge",
            "GIT_COMMITTER_EMAIL": "proofmerge@localhost",
        }
        _git(repo, "read-tree", head_sha, env=env)
        for file in files:
            listed = _git(repo, "ls-tree", head_sha, "--", file.path).decode().split()
            mode = listed[0] if listed and listed[0] in ("100644", "100755") else "100644"
            blob = (
                _git(repo, "hash-object", "-w", "--stdin", stdin=file.content.encode("utf-8"))
                .decode()
                .strip()
            )
            _git(repo, "update-index", "--cacheinfo", f"{mode},{blob},{file.path}", env=env)
        tree = _git(repo, "write-tree", env=env).decode().strip()
        sha = (
            _git(
                repo, "commit-tree", tree, "-p", head_sha, "-m", "ProofMerge suggested fix", env=env
            )
            .decode()
            .strip()
        )
    _git(repo, "update-ref", ref, sha)
    return sha


def _outcomes(experiment: Experiment, revision: str) -> str:
    runs = sorted(
        (e for e in experiment.executions if e.revision == revision), key=lambda e: e.attempt
    )
    return "".join(e.outcome[0] for e in runs)


def _user_message(
    proof: Proof,
    hypothesis_id: str,
    probe: str,
    failure: str,
    allowed: dict[str, str],
    diff_text: str,
) -> str:
    hypothesis = next(h for h in proof.hypotheses.hypotheses if h.id == hypothesis_id)
    claims = "\n".join(f"- {c.id} {c.kind}: {c.statement}" for c in proof.intent.claims)
    files = "\n\n".join(f"--- {path} (head)\n{text}" for path, text in allowed.items())
    return (
        f"Intent claims of the pull request:\n{claims}\n\n"
        f"Verified regression {hypothesis.id}: {hypothesis.title}\n{hypothesis.suspected_behavior}\n"
        f"Expected (base behaviour): {hypothesis.expected_base}\n\n"
        f"Probe that passes on base and fails on head:\n{probe}\n\n"
        f"How it fails on head:\n{failure or '(no output recorded)'}\n\n"
        f"Unified diff of the pull request:\n{diff_text}\n\n"
        f"Files you may change, as they are at head:\n{files}"
    )


def suggest_fixes(
    proof: Proof,
    evidence_dir: Path,
    repo: Path,
    client: MetaClient,
    diff_text: str,
    changed_files: list[str],
    run_probe: ProbeRunner,
    failures: dict[str, str],
) -> FixReport:
    """Propose, build and check one fix per verified regression; write <evidence>.fix/."""
    out = fix_dir(evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    allowed = {
        p: t
        for p, t in head_files(repo, proof.head_sha, changed_files).items()
        if not is_test_path(p)
    }
    experiments = {e.hypothesis_id: e for e in proof.experiments}
    results = []
    for hid in regressions(proof):
        if not HYPOTHESIS_ID.fullmatch(hid) or hid not in experiments:
            continue
        experiment = experiments[hid]
        probe = read_evidence(evidence_dir, experiment.test_source_path)
        if probe is None:
            results.append(
                FixResult(
                    hypothesis_id=hid,
                    explanation="",
                    problem="The probe source is missing from the evidence, or its path leaves it.",
                )
            )
            continue
        user = _user_message(proof, hid, probe, failures.get(hid, ""), allowed, diff_text)
        history: list[dict[str, str]] = []
        result = FixResult(hypothesis_id=hid, explanation="", problem="No fix was proposed.")
        for draft in range(1, MAX_DRAFTS + 1):
            try:
                proposal = client.structured(
                    SPARK_MODEL,
                    developer=PROMPT,
                    user=user,
                    schema=ProposedFix,
                    history=history,
                    reasoning_effort="medium",
                    max_completion_tokens=16384,
                )
            except MetaError as error:
                result = result.model_copy(
                    update={"problem": f"Model call failed: {error}", "drafts": draft}
                )
                break
            history.append({"role": "assistant", "content": proposal.model_dump_json()})
            result = FixResult(hypothesis_id=hid, explanation=proposal.explanation, drafts=draft)
            try:
                files = check_proposal(proposal, allowed)
                sha = commit_fix(
                    repo, proof.head_sha, files, f"refs/proofmerge/fix/{proof.head_sha[:12]}/{hid}"
                )
                patch = _git(
                    repo, "diff", "--no-color", "--no-ext-diff", proof.head_sha, sha
                ).decode("utf-8", "replace")
                checked = run_probe(sha, hid, probe, out / f"{hid}-draft{draft}")
            except (FixError, OSError, ValueError) as error:
                result = result.model_copy(update={"problem": str(error)})
                history.append(
                    {"role": "user", "content": f"That fix was rejected: {error} Try again."}
                )
                continue
            base, fixed = _outcomes(checked, "base"), _outcomes(checked, "head")
            verified = bool(base) and set(base) == {"P"} and bool(fixed) and set(fixed) == {"P"}
            result = result.model_copy(
                update={
                    "files": files,
                    "patch": patch,
                    "fix_sha": sha,
                    "base_outcomes": base,
                    "fixed_outcomes": fixed,
                    "verified": verified,
                    "problem": None
                    if verified
                    else f"The probe still does not pass on the fix (base {base}, fix {fixed}).",
                }
            )
            if verified:
                break
            history.append(
                {
                    "role": "user",
                    "content": f"With that fix the probe ran base {base}, fix {fixed}; it must pass every time. Try again.",
                }
            )
        results.append(result)
    report = FixReport(
        run_id=proof.run_id,
        base_sha=proof.base_sha,
        head_sha=proof.head_sha,
        endpoint=getattr(client, "endpoint", None),
        calls=client.calls,
        fixes=results,
    )
    (out / "fix.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
    return report
